# core/risk_engine.py

import asyncio
import logging
from typing import Optional
import time
from exchanges.base import Order
from core.analysis.regex_analyzer import analyze as regex_analyze, RegexResult
from core.storage.merchant_db import MerchantDB
from core.analysis.behavioral_analyzer import analyze_history
from core.analysis.identity_analyzer import analyze_identity
from core.utils.cache import TTLCache
from config.defaults import (
    MIN_ORDERS, MIN_COMPLETION,
    TRUSTED_MIN_ORDERS, TRUSTED_MIN_COMPLETION,
    TRUSTED_MAX_RISK_SCORE, TRUSTED_LLM_MIN_SCORE,
    BEHAVIOR_HISTORY_MINUTES, BOT_ALERT_COOLDOWN_SEC,
    DB_ASYNC_ANALYZE_CONCURRENCY,
    REVIEW_WARN_NEG_PCT, REVIEW_WARN_MIN_NEG,
    REVIEW_BLOCK_NEG_PCT, REVIEW_BLOCK_MIN_NEG,
)
logger = logging.getLogger("RiskEngine")

# MIN_ORDERS — з config/defaults.py

# MIN_COMPLETION — з config/defaults.py

# TRUSTED_MAX_RISK_SCORE — з config/defaults.py
# TRUSTED_LLM_MIN_SCORE — з config/defaults.py

# BEHAVIOR_HISTORY_MINUTES — з config/defaults.py
BEHAVIOR_ALERT_SCORE = 60
# BOT_ALERT_COOLDOWN_SEC — з config/defaults.py

_ASYNC_ANALYZE_CONCURRENCY = DB_ASYNC_ANALYZE_CONCURRENCY

# REVIEW_* — з config/defaults.py


# _BoundedTTLCache → замінено на core/utils/cache.py TTLCache


def _is_trusted_merchant(order, risk_score: int = 0) -> bool:
    return (
            order.month_order_count >= TRUSTED_MIN_ORDERS
            and order.finish_rate_pct >= TRUSTED_MIN_COMPLETION
            and risk_score <= TRUSTED_MAX_RISK_SCORE
    )


class RiskEngine:
    def __init__(self, db: Optional[MerchantDB] = None, llm_pool=None):
        self._db = db
        self._llm = llm_pool
        self._analyzed = 0
        self._db_hits = 0
        self._bot_alert_cache: dict[tuple[str, str], tuple[str, float]] = {}
        self._b_cache  = TTLCache(ttl_seconds=60.0, max_size=2000)
        self._id_cache = TTLCache(ttl_seconds=60.0, max_size=2000)
        self._db_sem = asyncio.Semaphore(_ASYNC_ANALYZE_CONCURRENCY)

    def analyze(self, order: Order) -> Order:
        self._analyzed += 1
        behavior_flags = self._behavior(order)

        if self._db and order.merchant_id:
            asyncio.ensure_future(self._async_analyze(order, behavior_flags))
            initial_flags = _dedupe_flags(behavior_flags)
            order.risk_flag = _join_flags(initial_flags) if initial_flags else "PENDING"
            return order

        result = regex_analyze(
            order.trade_terms,
            order.finish_rate_pct,
            order.month_order_count,
            order.is_verified,
        )

        order.regex_warn_flags = list(getattr(result, "warn_flags", []) or [])
        order.regex_score = int(getattr(result, "score", 0) or 0)

        flags: list[str] = []

        if result.verdict == "BLOCK":
            flags.append(f"BLOCK:{result.risk_type}:{result.reason}")
        elif result.verdict == "NEEDS_LLM":
            flags.append(_build_pending_flag(result))
        elif result.reason:
            flags.append(_build_weak_regex_flag(result))

        flags.extend(behavior_flags)
        flags = _dedupe_flags(flags)
        order.risk_flag = _join_flags(flags) if flags else "OK"
        return order

    async def _async_analyze(self, order: Order, behavior_flags: list[str]) -> None:
        async with self._db_sem:
            await self._async_analyze_inner(order, behavior_flags)

    async def _async_analyze_inner(self, order: Order, behavior_flags: list[str]) -> None:
        try:
            exchange = order.exchange
            mid = order.merchant_id
            terms = getattr(order, "trade_terms", "") or ""
            now = time.time()

            cache_key = (exchange, mid)

            need_snapshots = self._b_cache.get(cache_key) is None
            need_twins = bool(order.merchant_name) and self._id_cache.get(cache_key) is None

            coros = [
                self._db.is_blacklisted(exchange, mid),
                self._db.get_reviews_summary(exchange, mid),
                self._db.get_recent_snapshots(exchange, mid, minutes=BEHAVIOR_HISTORY_MINUTES)
                if need_snapshots else _noop(None),
                self._db.find_digital_twins(order.merchant_name, exchange, minutes=15)
                if need_twins else _noop(None),
                self._db.get_verdict(exchange, mid, terms),
                self._db.get_risk_score(exchange, mid),
            ]

            (
                (is_bl, bl_reason),
                review_summary_raw,
                snapshots_raw,
                twins_raw,
                cached_verdict,
                score,
            ) = await asyncio.gather(*coros)

            if is_bl:
                order.risk_flag = f"BLOCK:BLACKLIST:{bl_reason}"
                logger.warning("🚫 Blacklist: %s [%s] — %s", order.merchant_name, exchange, bl_reason)
                return

            review_flags = _build_review_flags_from_summary(review_summary_raw)

            # ── 1. ПОВЕДІНКОВИЙ ШАР ───────────────────────────────────────────
            cached_b = self._b_cache.get(cache_key)
            if cached_b is not None:
                behavior_result = cached_b
            else:
                behavior_result = analyze_history(order, snapshots_raw or [])
                self._b_cache.set(cache_key, behavior_result)

            if behavior_result.flags:
                behavior_flags.extend(behavior_result.flags)

            behavior_score = int(getattr(behavior_result, "score", 0) or 0)
            behavior_reason = str(getattr(behavior_result, "reason", "") or "")
            behavior_needs_llm = bool(getattr(behavior_result, "needs_llm", False))

            # ── 2. ШАР ЦИФРОВИХ ДВІЙНИКІВ ─────────────────────────────────────
            if order.merchant_name:
                cached_id = self._id_cache.get(cache_key)
                if cached_id is not None:
                    id_result = cached_id
                else:
                    id_result = analyze_identity(order, twins_raw or [])
                    self._id_cache.set(cache_key, id_result)

                if id_result.is_twin:
                    behavior_flags.append(f"CROSS_EXCHANGE_BOT:{id_result.reason}")

            if behavior_score >= BEHAVIOR_ALERT_SCORE or behavior_needs_llm:
                behavior_flags.append(f"BEHAVIOR_BOTLIKE:S{behavior_score}")
                signature = _behavior_signature(behavior_result.flags, behavior_score, behavior_reason)
                if _should_log_behavior_alert(self._bot_alert_cache, exchange, mid, signature):
                    logger.warning("🤖 Підозра на БОТА: %s [%s] — %s", order.merchant_name, exchange,
                                   behavior_reason or signature)

            behavior_flags = _dedupe_flags(behavior_flags)

            # 🚀 МАТРИЦЯ ДОКАЗІВ: ПІДГОТОВКА ЗМІННИХ
            has_exact_limits = any(f.startswith("EXACT_LIMITS") or f.startswith("STATIC_DROP") for f in behavior_flags)
            has_cross_bot = any(f.startswith("CROSS_EXCHANGE_BOT") for f in behavior_flags)
            has_api_replenish = any(f.startswith("API_REPLENISH") for f in behavior_flags)
            has_bad_reviews = any(f.startswith("BADREVIEWS:") for f in review_flags)

            # 🚨 CACHE OVERRIDE (ІНВАЛІДАЦІЯ)
            has_anomalous_behavior = has_exact_limits or has_cross_bot or has_api_replenish
            if has_anomalous_behavior and cached_verdict == "OK":
                cached_verdict = None
                logger.debug("💥 Cache Override: Знайдено поведінкові аномалії для %s", order.merchant_name)

            # ── 3. ПОВЕРНЕННЯ З КЕШУ (Тільки якщо все чисто) ──────────────────
            if cached_verdict and cached_verdict not in ("NEEDS_LLM",):
                self._db_hits += 1

            if score >= 80 and cached_verdict == "OK":
                flags = _dedupe_flags(["HIGH_RISK_SCORE"] + review_flags + behavior_flags)
                order.risk_flag = _join_flags(flags) or "HIGH_RISK_SCORE"
                return

            if cached_verdict is not None:
                cached_flag = await _build_cached_flag(cached_verdict, exchange, mid, self._db)

                block_review = _pick_block_review(review_flags)
                if block_review:
                    order.risk_flag = block_review
                    return

                flags: list[str] = []
                if cached_flag and cached_flag != "OK":
                    flags.append(cached_flag)
                flags.extend(review_flags)
                flags.extend(behavior_flags)

                order.risk_flag = _join_flags(_dedupe_flags(flags)) or "OK"
                return

            # ── 4. REGEX АНАЛІЗ ───────────────────────────────────────────────
            regex_result = regex_analyze(
                terms,
                order.finish_rate_pct,
                order.month_order_count,
                order.is_verified,
            )

            order.regex_warn_flags = list(getattr(regex_result, "warn_flags", []) or [])
            order.regex_score = int(getattr(regex_result, "score", 0) or 0)
            has_soft_regex = order.regex_score > 0

            # 🚀 HARD EVIDENCE 1: Детерміністичний REGEX BLOCK
            if regex_result.verdict == "BLOCK":
                await self._db.save_verdict(
                    exchange, mid, order.merchant_name, terms,
                    "BLOCK", regex_result.risk_type, regex_result.reason, "regex"
                )
                block_flag = f"BLOCK:{regex_result.risk_type}:{regex_result.reason}"
                flags = _dedupe_flags([block_flag] + review_flags + behavior_flags)
                order.risk_flag = _join_flags(flags) or block_flag
                logger.warning("🚫 Regex BLOCK: %s [%s] %s — %s", order.merchant_name, exchange, regex_result.risk_type,
                               regex_result.reason)
                return

            # 🚀 HARD EVIDENCE 2: Детерміністичний BAD REVIEWS BLOCK
            block_review = _pick_block_review(review_flags)
            if block_review:
                order.risk_flag = block_review
                return

            # 🚀 COMPOSITE EVIDENCE (ДЕЛЕГУЄМО ФІНАЛЬНЕ РІШЕННЯ В LLM)
            composite_risk = ""
            if has_cross_bot and has_bad_reviews:
                composite_risk = "Мережа клонів + Негативні відгуки"
            elif has_api_replenish and has_soft_regex:
                composite_risk = "Бот-автопоповнення + Підозрілі умови в тексті"
            elif has_exact_limits and has_cross_bot and has_soft_regex:
                composite_risk = "Фіксована сума + Клони на біржах + М'які ризики"

            if composite_risk:
                logger.info("⚖️ КОМПОЗИТНИЙ РИЗИК: %s [%s] — %s. Делегуємо фінальне рішення в LLM.",
                            order.merchant_name, exchange, composite_risk)
                behavior_needs_llm = True  # 🚀 Форсуємо виклик нейронки
                behavior_flags.append("COMPOSITE_RISK")

                # Інвалідуємо старий чистий кеш, щоб перепровірити
                if cached_verdict == "OK":
                    cached_verdict = None

            # ── 5. LLM TIE-BREAKER ────────────────────────────────────────────
            # 🚀 ФІКС: Нейронка тепер викликається і через текст (regex), і через поведінку (behavior)
            if (regex_result.needs_llm or behavior_needs_llm) and self._llm:
                trusted = _is_trusted_merchant(order, score)

                # ЗНЯТТЯ ІМУНІТЕТУ: поведінкові аномалії змушують VIP-мерчанта йти на перевірку LLM
                if trusted and has_anomalous_behavior:
                    logger.debug("🔍 Trusted immunity stripped for %s due to anomalous behavior.", order.merchant_name)
                    trusted = False

                if trusted and regex_result.score < TRUSTED_LLM_MIN_SCORE:
                    logger.debug("✅ Trusted skip: %s [%s] score=%d < %d", order.merchant_name, exchange,
                                 regex_result.score, TRUSTED_LLM_MIN_SCORE)
                    flags: list[str] = []
                    flags.extend(review_flags)
                    flags.extend(behavior_flags)
                    if not flags and regex_result.reason:
                        flags.append(_build_weak_regex_flag(regex_result))
                    order.risk_flag = _join_flags(_dedupe_flags(flags)) or "OK"
                    return

                # 🚀 ФІКС: Передаємо всю статистику та ліміти у воркер!
                scheduled = self._llm.schedule(
                    exchange=exchange,
                    merchant_id=mid,
                    merchant_name=order.merchant_name,
                    trade_terms=terms,
                    regex_result=regex_result,
                    finish_rate=order.finish_rate_pct,
                    month_order_count=order.month_order_count,
                    is_verified=order.is_verified,
                    min_limit=order.min_limit,
                    max_limit=order.max_limit,
                    behavior_flags=behavior_flags
                )

                if scheduled:
                    # 🚀 Правильний PENDING маркер для поведінкових тригерів
                    if composite_risk or behavior_needs_llm:
                        pending_flag = f"LLM_PENDING:BEHAVIOR:S{behavior_score}"
                    else:
                        pending_flag = _build_pending_flag(regex_result)

                    flags = _dedupe_flags([pending_flag] + review_flags + behavior_flags)
                    order.risk_flag = _join_flags(flags) or pending_flag
                    return

                if scheduled:
                    # 🚀 ФІКС: Правильний PENDING маркер для поведінкових тригерів
                    if composite_risk or behavior_needs_llm:
                        pending_flag = f"LLM_PENDING:BEHAVIOR:S{behavior_score}"
                    else:
                        pending_flag = _build_pending_flag(regex_result)

                    flags = _dedupe_flags([pending_flag] + review_flags + behavior_flags)
                    order.risk_flag = _join_flags(flags) or pending_flag
                    return
                else:
                    # 🚀 ЗАХИСТ ВІД СПАМУ: Якщо форсували LLM через бот-поведінку,
                    # але спрацював кулдаун (щоб не платити за API) або черга повна —
                    # заморожуємо статус у PENDING, щоб не було помилкових OK.
                    if composite_risk or behavior_needs_llm:
                        order.risk_flag = f"LLM_PENDING:COOLDOWN:Зачекайте_на_LLM"
                        return

            # Якщо не потребує LLM або не вдалося запланувати:
            flags: list[str] = []
            if regex_result.reason:
                flags.append(_build_weak_regex_flag(regex_result))
            flags.extend(review_flags)
            flags.extend(behavior_flags)
            order.risk_flag = _join_flags(_dedupe_flags(flags)) or "OK"

        except Exception as e:
            logger.error("RiskEngine async помилка для %s: %s", order.merchant_name, e, exc_info=True)

    def _behavior(self, order: Order) -> list[str]:
        flags = []
        exchange = order.exchange

        # LOW_STATS більше не є глобальним блоком — перенесено в AlertDispatcher._user_wants()
        # де кожен юзер має свої персональні пороги (merchant_filters_json)

        # 🚀 ФІКС 3: ЗНЯТТЯ ІМУНІТЕТУ ДЛЯ ФІКСОВАНИХ СУМ
        if order.min_limit > 0 and order.max_limit > 0:
            # Різниця до 5 грн — це вже аномалія (EXACT_TOLERANCE)
            is_exact = abs(order.max_limit - order.min_limit) <= 5.0

            if is_exact:
                # ЖОДНОГО ІМУНІТЕТУ для фіксованих сум
                flags.append("SUSPICIOUS_LIMITS")
            else:
                # Для звичайних ордерів залишаємо старе правило (імунітет > 1000 угод)
                spread = float(order.max_limit - order.min_limit) / float(order.max_limit)
                if spread < 0.02 and float(order.max_limit) > 500:
                    if not (order.is_verified or order.month_order_count > 1000):
                        flags.append("SUSPICIOUS_LIMITS")

        return flags

    def analyze_batch(self, orders: list[Order]) -> list[Order]:
        for order in orders:
            self.analyze(order)
        return orders

    async def analyze_batch_async(self, orders: list[Order]) -> list[Order]:
        for order in orders:
            self.analyze(order)
        return orders

    def stats(self) -> str:
        return f"RiskEngine: {self._analyzed} analyzed, {self._db_hits} db hits"


async def _noop(value):
    return value


def _build_review_flags_from_summary(summary: dict | None) -> list[str]:
    if not summary:
        return []

    pos = int(summary.get("positive", 0) or 0)
    neg = int(summary.get("negative", 0) or 0)
    neutral = int(summary.get("neutral", 0) or 0)
    bad_texts = summary.get("bad_texts", []) or []

    total = pos + neg + neutral
    if total <= 0:
        return []

    neg_pct = (neg / total) * 100.0
    sample = ""
    if bad_texts:
        sample = str(bad_texts[0]).replace("\n", " ").strip()[:120]

    reason = f"{neg_pct:.0f}% neg ({neg}/{total})"
    if sample:
        reason += f" | {sample}"

    if neg >= REVIEW_BLOCK_MIN_NEG and neg_pct >= REVIEW_BLOCK_NEG_PCT:
        return [f"BLOCK:BADREVIEWS:{reason}"]

    if neg >= REVIEW_WARN_MIN_NEG and neg_pct >= REVIEW_WARN_NEG_PCT:
        return [f"BADREVIEWS:{reason}"]

    return []


def _join_flags(flags: list[str]) -> str:
    clean = [f for f in flags if f]
    return ",".join(clean)


def _pick_block_review(flags: list[str]) -> str | None:
    for f in flags:
        if f.startswith("BLOCK:BADREVIEWS:"):
            return f
    return None


def _build_pending_flag(result: RegexResult) -> str:
    risk = result.risk_type or "SUSPICIOUS"
    score = getattr(result, "score", 0) or 0
    return f"LLM_PENDING:{risk}:S{score}"


def _build_weak_regex_flag(result: RegexResult) -> str:
    risk = result.risk_type or "WEAK_SIGNAL"
    score = getattr(result, "score", 0) or 0
    return f"REGEX_WEAK:{risk}:S{score}"


async def _build_cached_flag(verdict: str, exchange: str, merchant_id: str, db: MerchantDB) -> str:
    risk_type, reason = await db.get_reason(exchange, merchant_id)
    risk_type = (risk_type or "").strip()
    reason = (reason or "").strip()[:120]

    if verdict == "BLOCK":
        if risk_type or reason:
            return f"BLOCK:{risk_type or 'CACHED'}:{reason or 'cached verdict'}"
        return "BLOCK:CACHED"
    if verdict == "SUSPICIOUS":
        if risk_type or reason:
            return f"LLM_SUSPICIOUS:{risk_type or 'SUSPICIOUS'}:{reason or 'cached suspicious'}"
        return "LLM_SUSPICIOUS"
    if verdict == "UNKNOWN":
        if reason:
            return f"LLM_UNKNOWN:NONE:{reason}"
        return "LLM_UNKNOWN"
    return "OK"


def _behavior_signature(flags: list[str], score: int, reason: str = "") -> str:
    core = ",".join(flags)
    short_reason = (reason or "")[:160]
    if short_reason:
        return f"{core}|S{score}|{short_reason}"
    return f"{core}|S{score}"


def _should_log_behavior_alert(cache: dict[tuple[str, str], tuple[str, float]], exchange: str, merchant_id: str,
                               signature: str, cooldown_sec: int = BOT_ALERT_COOLDOWN_SEC) -> bool:
    import time
    key = (exchange, merchant_id)
    now = time.time()
    prev = cache.get(key)
    if prev is None:
        cache[key] = (signature, now)
        return True
    prev_signature, prev_ts = prev
    if signature != prev_signature or (now - prev_ts) >= cooldown_sec:
        cache[key] = (signature, now)
        return True
    return False


def _dedupe_flags(flags: list[str]) -> list[str]:
    out = []
    seen = set()
    for f in flags:
        if not f or f in seen:
            continue
        seen.add(f)
        out.append(f)
    return out