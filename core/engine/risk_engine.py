# core/engine/risk_engine.py
"""
RiskEngine v2.0

Зміни:
  1. CompositeScorer — єдиний normalized score 0-100 зі всіх шарів
  2. Verdict freshness — вердикт живе 7 днів, потім перераховується
  3. _build_review_flags_from_summary — враховує neg_pct тепер коректно
     (pos/neg більше не нулі після фіксу review_fetcher)
  4. Кеш-інвалідація при зростанні composite_score
  5. Identity: weighted matching (замість all-or-nothing) перенесено
     в identity_analyzer.py; тут тільки оркестрація
"""

from __future__ import annotations

import asyncio
import logging
import time
from typing import Optional

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

BEHAVIOR_ALERT_SCORE       = 60
_ASYNC_ANALYZE_CONCURRENCY = DB_ASYNC_ANALYZE_CONCURRENCY

# Вердикт живе 3 дні (знижено з 7 — відгуки TTL=24h, 7d давало стейл вердикти).
VERDICT_MAX_AGE_DAYS = 3


# ─────────────────────────────────────────────────────────────────────────────
# CompositeScorer — єдиний normalized score 0-100
# ─────────────────────────────────────────────────────────────────────────────

class CompositeScorer:
    """
    Зводить сигнали з усіх шарів в єдиний normalized_risk_score 0-100.

    Ваги підібрані так щоб:
      - Regex BLOCK (score=100) + будь-що ще → composite >= 75 → BLOCK
      - Тільки behavioral бот (score=50) без інших → composite ~= 40 → SUSPICIOUS
      - Тільки погані відгуки (25% neg) без інших → composite ~= 30 → WARN
      - Identity клон (is_twin) тепер дає суттєвий внесок (~15 балів)
      - review_text_score враховує аналіз текстів відгуків (окремо від neg_pct)

    Зміни v2.1:
      - W_IDENTITY: 0.05 → 0.10 (клон це сильний сигнал)
      - W_REVIEWS розбито: W_REVIEWS_PCT (кількісний) + W_REVIEWS_TEXT (текстовий)
      - review_text_score: новий параметр, avg score bad_texts (0-100)
    """
    W_REGEX        = 0.28
    W_BEHAVIOR     = 0.22
    W_REVIEWS_PCT  = 0.12   # neg_pct (кількісний сигнал)
    W_REVIEWS_TEXT = 0.08   # текстовий аналіз bad_texts (раніше ігнорувався)
    W_LLM          = 0.20
    W_IDENTITY     = 0.10   # підвищено з 0.05 — підтверджений клон важливий

    @classmethod
    def compute(
        cls,
        regex_score:       int,
        behavior_score:    int,
        review_neg_pct:    float,
        llm_verdict:       str,
        is_twin:           bool,
        finish_rate:       float = 100.0,
        order_count:       int   = 0,
        review_text_score: float = 0.0,   # новий: avg score текстів bad_texts
    ) -> int:
        """Повертає composite_score 0-100."""

        r_norm    = min(100, max(0, regex_score))
        b_norm    = min(100, max(0, behavior_score))
        rev_norm  = min(100, review_neg_pct * 2.5)   # 40% neg = 100 score
        rtext_norm = min(100, max(0, review_text_score))
        l_map     = {"OK": 0, "SUSPICIOUS": 50, "BLOCK": 100, "UNKNOWN": 25}
        l_norm    = l_map.get(llm_verdict.upper(), 25)
        id_norm   = 100 if is_twin else 0

        raw = (
            r_norm     * cls.W_REGEX        +
            b_norm     * cls.W_BEHAVIOR     +
            rev_norm   * cls.W_REVIEWS_PCT  +
            rtext_norm * cls.W_REVIEWS_TEXT +
            l_norm     * cls.W_LLM          +
            id_norm    * cls.W_IDENTITY
        )

        # Підвищений ризик для нових акаунтів з поганим рейтингом
        if order_count < 50 and finish_rate < 92.0:
            raw = min(100, raw * 1.3)

        return min(100, int(raw))

    @classmethod
    def to_verdict(cls, score: int) -> str:
        if score >= 75: return "BLOCK"
        if score >= 45: return "SUSPICIOUS"
        if score >= 20: return "WARN"
        return "OK"


# ─────────────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────────────

def _is_trusted_merchant(order, risk_score: int = 0) -> bool:
    return (
        order.month_order_count >= TRUSTED_MIN_ORDERS
        and order.finish_rate_pct >= TRUSTED_MIN_COMPLETION
        and risk_score <= TRUSTED_MAX_RISK_SCORE
    )


def _build_review_flags_from_summary(summary: dict | None) -> list[str]:
    if not summary:
        return[]

    status = summary.get("status", "OK")
    if status == "UNAVAILABLE":
        return[]

    pos       = int(summary.get("positive",  0) or 0)
    neg       = int(summary.get("negative",  0) or 0)
    neutral   = int(summary.get("neutral",   0) or 0)
    bad_texts = summary.get("bad_texts", []) or[]

    # 🚀 НОВЕ: ХАРД-БЛОК ЗА КРИТИЧНІ ВІДГУКИ (Навіть якщо він один)
    CRITICAL_CATEGORIES = {"TRIANGLE", "CHARGEBACK", "FINCRIME", "CASINO"}
    for bad_text_item in bad_texts:
        if isinstance(bad_text_item, dict):
            cats = bad_text_item.get("categories",[])
            if any(c in CRITICAL_CATEGORIES for c in cats):
                cat_names = ", ".join(cats)
                excerpt = str(bad_text_item.get("excerpt", ""))[:100]
                return[f"BLOCK:BADREVIEWS:Критичний відгук ({cat_names}) | {excerpt}"]

    total = pos + neg + neutral
    if total <= 0:
        if bad_texts:
            sample = str(bad_texts[0]).replace("\n", " ").strip()[:120]
            return[f"BADREVIEWS_TEXTS:відгуки є але лічильники відсутні | {sample}"]
        return[]

    neg_pct = (neg / total) * 100.0
    sample  = ""
    if bad_texts:
        # Для логування беремо текст (або словник)
        first_bad = bad_texts[0]
        sample = str(first_bad.get("text", first_bad) if isinstance(first_bad, dict) else first_bad).replace("\n", " ").strip()[:120]

    reason = f"{neg_pct:.0f}% neg ({neg}/{total})"
    if sample:
        reason += f" | {sample}"

    if neg >= REVIEW_BLOCK_MIN_NEG and neg_pct >= REVIEW_BLOCK_NEG_PCT:
        return [f"BLOCK:BADREVIEWS:{reason}"]

    if neg >= REVIEW_WARN_MIN_NEG and neg_pct >= REVIEW_WARN_NEG_PCT:
        return [f"BADREVIEWS:{reason}"]

    return[]


def _is_verdict_stale(analyzed_at: float) -> bool:
    """
    Вердикт вважається застарілим якщо він старший VERDICT_MAX_AGE_DAYS.
    Примушує перерахунок навіть якщо terms_hash не змінився.
    """
    if not analyzed_at:
        return True
    age_days = (time.time() - analyzed_at) / 86400.0
    return age_days > VERDICT_MAX_AGE_DAYS


async def _noop(value):
    return value


def _join_flags(flags: list[str]) -> str:
    clean = [f for f in flags if f]
    return ",".join(clean)


def _pick_block_review(flags: list[str]) -> str | None:
    for f in flags:
        if f.startswith("BLOCK:BADREVIEWS:"):
            return f
    return None


def _build_pending_flag(result: RegexResult) -> str:
    risk  = result.risk_type or "SUSPICIOUS"
    score = getattr(result, "score", 0) or 0
    return f"LLM_PENDING:{risk}:S{score}"


def _build_weak_regex_flag(result: RegexResult) -> str:
    risk  = result.risk_type or "WEAK_SIGNAL"
    score = getattr(result, "score", 0) or 0
    return f"REGEX_WEAK:{risk}:S{score}"


async def _build_cached_flag(verdict: str, exchange: str, merchant_id: str, db: MerchantDB) -> str:
    risk_type, reason = await db.get_reason(exchange, merchant_id)
    risk_type = (risk_type or "").strip()
    reason    = (reason    or "").strip()[:120]

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
    core         = ",".join(flags)
    short_reason = (reason or "")[:160]
    return f"{core}|S{score}|{short_reason}" if short_reason else f"{core}|S{score}"


def _should_log_behavior_alert(
    cache: TTLCache, exchange: str, merchant_id: str,
    signature: str, cooldown_sec: int = BOT_ALERT_COOLDOWN_SEC,
) -> bool:
    key            = f"{exchange}:{merchant_id}"
    prev_signature = cache.get(key)
    if prev_signature != signature:
        cache.set(key, signature)
        return True
    return False


def _dedupe_flags(flags: list[str]) -> list[str]:
    out, seen = [], set()
    for f in flags:
        if not f or f in seen:
            continue
        seen.add(f)
        out.append(f)
    return out


# ─────────────────────────────────────────────────────────────────────────────
# RiskEngine
# ─────────────────────────────────────────────────────────────────────────────

class RiskEngine:
    def __init__(self, db: Optional[MerchantDB] = None, llm_pool=None, review_fetcher=None):
        self._db      = db
        self._llm     = llm_pool
        self._review_fetcher = review_fetcher  # 🚀 ДОДАНО
        self._analyzed  = 0
        self._db_hits   = 0
        self._bot_alert_cache = TTLCache(ttl_seconds=BOT_ALERT_COOLDOWN_SEC, max_size=5000)
        self._b_cache   = TTLCache(ttl_seconds=60.0, max_size=2000)
        self._id_cache  = TTLCache(ttl_seconds=60.0, max_size=2000)
        self._db_sem    = asyncio.Semaphore(_ASYNC_ANALYZE_CONCURRENCY)

        # 🚀 ДОДАЄМО МЕТОД СИНЕРГІЙ ОДРАЗУ ПІСЛЯ __init__
    def _check_synergies(self, order: Order, regex_flags: list[str], behavior_flags: list[str]) -> list[str]:
        synergies = []
        has_api    = any("API_REPLENISH"   in f for f in behavior_flags)
        has_exact  = any("EXACT_LIMITS"    in f for f in behavior_flags)
        has_cross  = any("CROSS_EXCHANGE_BOT" in f for f in behavior_flags)
        has_static = any("STATIC_DROP"     in f for f in behavior_flags)
        has_flicker = any("FLICKER_RELIST" in f for f in behavior_flags)
        has_velocity = any("VELOCITY_SPIKE" in f for f in behavior_flags)

        has_ext_link       = any("EXTERNAL_LINK" in f for f in regex_flags)
        has_chat_first     = any("CHAT_FIRST"    in f for f in regex_flags)
        has_chargeback_warn = any("CHARGEBACK"   in f for f in regex_flags)
        has_anonymous      = any("ANONYMOUS"     in f for f in regex_flags)
        has_fincrime       = any("FINCRIME"      in f for f in regex_flags)

        is_new   = order.month_order_count < 50
        bad_rate = order.finish_rate_pct < 90.0

        # Оригінальні 4
        if has_api and has_ext_link:
            synergies.append("BLOCK:SYNERGY:Бот-автопоповнення + Зовнішній лінк")
        if is_new and has_exact and has_chargeback_warn:
            synergies.append("BLOCK:SYNERGY:Новий акаунт + Фікс.ліміт + Ризик рефанду")
        if has_api and has_chat_first and bad_rate:
            synergies.append("BLOCK:SYNERGY:Бот + Тягне в чат + Низький %")
        if has_cross and has_ext_link:
            synergies.append("BLOCK:SYNERGY:Кросс-біржовий клон + Зовнішній лінк")

        # Нові комбо v2.1
        if has_api and has_cross:
            # Бот на кількох біржах одночасно — беззаперечна автоматизація
            synergies.append("BLOCK:SYNERGY:Бот-автопоповнення + Крос-біржовий клон")
        if has_flicker and has_ext_link:
            # Скрипт блимає і виводить у зовнішній чат — класичний скам-патерн
            synergies.append("BLOCK:SYNERGY:Flicker-relist + Зовнішній лінк")
        if has_velocity and has_exact and not order.is_verified:
            # Висока швидкість угод + фікс.ліміт + не верифікований
            synergies.append("BLOCK:SYNERGY:Velocity spike + Фікс.ліміт + Не верифікований")
        if is_new and has_api:
            # Новий акаунт вже з ботом — не може бути органіки
            synergies.append("BLOCK:SYNERGY:Новий акаунт + Бот-автопоповнення")
        if has_static and has_anonymous:
            # Стабільний дроп + без KYC = сервіс обналу
            synergies.append("BLOCK:SYNERGY:Static-drop + Анонімність/без KYC")
        if has_fincrime and (has_api or has_cross):
            # Regex вже бачить fincrime-слова + бот/клон = підтверджена схема
            synergies.append("BLOCK:SYNERGY:Fincrime-сигнал + Автоматизований аккаунт")

        return synergies

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
        order.regex_score      = int(getattr(result, "score", 0) or 0)

        flags: list[str] = []
        if result.verdict == "BLOCK":
            flags.append(f"BLOCK:{result.risk_type}:{result.reason}")
        elif result.verdict == "NEEDS_LLM":
            flags.append(_build_pending_flag(result))
        elif result.reason:
            flags.append(_build_weak_regex_flag(result))

        flags.extend(behavior_flags)
        order.risk_flag = _join_flags(_dedupe_flags(flags)) if flags else "OK"
        return order

    async def _async_analyze(self, order: Order, behavior_flags: list[str]) -> None:
        async with self._db_sem:
            await self._async_analyze_inner(order, behavior_flags)

    async def _async_analyze_inner(self, order: Order, behavior_flags: list[str]) -> None:
        try:
            exchange  = order.exchange
            mid       = order.merchant_id
            terms     = getattr(order, "trade_terms", "") or ""
            cache_key = (exchange, mid)

            need_snapshots = self._b_cache.get(cache_key) is None
            need_twins     = bool(order.merchant_name) and self._id_cache.get(cache_key) is None

            coros = [
                self._db.is_blacklisted(exchange, mid),
                self._db.get_reviews_summary(exchange, mid),
                self._db.get_recent_snapshots(exchange, mid, minutes=BEHAVIOR_HISTORY_MINUTES)
                    if need_snapshots else _noop(None),
                self._db.find_digital_twins(order.merchant_name, exchange, minutes=15)
                    if need_twins else _noop(None),
                self._db.get_verdict(exchange, mid, terms),
                self._db.get_risk_score(exchange, mid),
                self._db.get_verdict_timestamp(exchange, mid),   # v2: для freshness check
            ]

            (
                (is_bl, bl_reason),
                review_summary_raw,
                snapshots_raw,
                twins_raw,
                cached_verdict,
                score,
                verdict_ts,
            ) = await asyncio.gather(*coros)

            # ── 🚀 СИНХРОННИЙ ДОКАЧ ВІДГУКІВ ДЛЯ НОВИХ МЕРЧАНТІВ ──────────
            rev_summary_test = review_summary_raw or {}
            rev_total_test = int(rev_summary_test.get("positive", 0)) + int(rev_summary_test.get("negative", 0)) + int(
                rev_summary_test.get("neutral", 0))

            if rev_total_test == 0 and self._review_fetcher and rev_summary_test.get("status") != "UNAVAILABLE":
                logger.debug(f"⏳ Синхронний fetch відгуків для нового мерчанта {order.merchant_name} [{exchange}]")
                review_summary_raw = await self._review_fetcher.fetch_now(exchange, mid)

            # ── Blacklist: найвищий пріоритет ──────────────────────────────
            if is_bl:
                order.risk_flag = f"BLOCK:BLACKLIST:{bl_reason}"
                logger.warning("🚫 Blacklist: %s [%s] — %s", order.merchant_name, exchange, bl_reason)
                return

            review_flags = _build_review_flags_from_summary(review_summary_raw)

            # ── v2.1: Розрахунок review_neg_pct + review_text_score ──────────
            rev_summary = review_summary_raw or {}
            rev_pos  = int(rev_summary.get("positive", 0) or 0)
            rev_neg  = int(rev_summary.get("negative", 0) or 0)
            rev_neu  = int(rev_summary.get("neutral",  0) or 0)
            rev_total = rev_pos + rev_neg + rev_neu
            review_neg_pct = (rev_neg / rev_total * 100.0) if rev_total > 0 else 0.0

            # review_text_score: середній score bad_texts (аналіз текстів відгуків)
            # Раніше цей сигнал ніколи не потрапляв у CompositeScorer.
            bad_texts = rev_summary.get("bad_texts") or []
            if bad_texts:
                text_scores = [
                    int(bt.get("score", 0))
                    for bt in bad_texts
                    if isinstance(bt, dict) and bt.get("score", 0) > 0
                ]
                review_text_score = float(sum(text_scores) / len(text_scores)) if text_scores else 0.0
            else:
                review_text_score = 0.0

            # ── 1. Behavioral ───────────────────────────────────────────────
            cached_b = self._b_cache.get(cache_key)
            if cached_b is not None:
                behavior_result = cached_b
            else:
                behavior_result = analyze_history(order, snapshots_raw or [])
                self._b_cache.set(cache_key, behavior_result)

            if behavior_result.flags:
                behavior_flags.extend(behavior_result.flags)

            behavior_score    = int(getattr(behavior_result, "score", 0) or 0)
            behavior_reason   = str(getattr(behavior_result, "reason", "") or "")
            behavior_needs_llm = bool(getattr(behavior_result, "needs_llm", False))

            # ── 2. Identity ─────────────────────────────────────────────────
            is_twin = False
            if order.merchant_name:
                cached_id = self._id_cache.get(cache_key)
                if cached_id is not None:
                    id_result = cached_id
                else:
                    id_result = analyze_identity(order, twins_raw or [])
                    self._id_cache.set(cache_key, id_result)

                if id_result.is_twin:
                    is_twin = True
                    behavior_flags.append(f"CROSS_EXCHANGE_BOT:{id_result.reason}")

            if behavior_score >= BEHAVIOR_ALERT_SCORE or behavior_needs_llm:
                behavior_flags.append(f"BEHAVIOR_BOTLIKE:S{behavior_score}")
                signature = _behavior_signature(behavior_result.flags, behavior_score, behavior_reason)
                if _should_log_behavior_alert(self._bot_alert_cache, exchange, mid, signature):
                    logger.warning(
                        "🤖 Підозра на БОТА: %s [%s] — %s",
                        order.merchant_name, exchange, behavior_reason or signature,
                    )

            behavior_flags = _dedupe_flags(behavior_flags)

            # ── v2.1: CompositeScorer ───────────────────────────────────────
            # Рахуємо composite на поточному стані (без LLM — він async)
            composite_score = CompositeScorer.compute(
                regex_score        = 0,   # буде оновлено після regex
                behavior_score     = behavior_score,
                review_neg_pct     = review_neg_pct,
                llm_verdict        = "UNKNOWN",
                is_twin            = is_twin,
                finish_rate        = order.finish_rate_pct,
                order_count        = order.month_order_count,
                review_text_score  = review_text_score,
            )
            order.composite_score = composite_score   # зберігаємо на ордері

            # ── Матриця доказів ─────────────────────────────────────────────
            has_exact_limits   = any(f.startswith("EXACT_LIMITS") or f.startswith("STATIC_DROP") for f in behavior_flags)
            has_cross_bot      = any(f.startswith("CROSS_EXCHANGE_BOT") for f in behavior_flags)
            has_api_replenish  = any(f.startswith("API_REPLENISH") for f in behavior_flags)
            has_bad_reviews    = any(f.startswith("BADREVIEWS") for f in review_flags)
            has_anomalous_behavior = has_exact_limits or has_cross_bot or has_api_replenish

            # ── v2: Verdict freshness — інвалідуємо застарілий кеш ─────────
            if cached_verdict and _is_verdict_stale(verdict_ts):
                logger.debug(
                    "🔄 Verdict freshness: %s [%s] вердикт старший %dd, перераховуємо",
                    order.merchant_name, exchange, VERDICT_MAX_AGE_DAYS,
                )
                cached_verdict = None

            # Cache override при поведінкових аномаліях
            if has_anomalous_behavior and cached_verdict == "OK":
                cached_verdict = None
                logger.debug("💥 Cache Override: аномалії для %s", order.merchant_name)

            # ── 3. Повернення з кешу ────────────────────────────────────────
            if score >= 80 and cached_verdict == "OK":
                flags = _dedupe_flags(["HIGH_RISK_SCORE"] + review_flags + behavior_flags)
                order.risk_flag = _join_flags(flags) or "HIGH_RISK_SCORE"
                return

            if cached_verdict is not None:
                self._db_hits += 1
                cached_flag   = await _build_cached_flag(cached_verdict, exchange, mid, self._db)

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

            # ── 4. Regex аналіз ─────────────────────────────────────────────
            regex_result = regex_analyze(
                terms,
                order.finish_rate_pct,
                order.month_order_count,
                order.is_verified,
            )
            order.regex_warn_flags = list(getattr(regex_result, "warn_flags", []) or [])
            order.regex_score      = int(getattr(regex_result, "score", 0) or 0)
            has_soft_regex         = order.regex_score > 0

            # Оновлюємо composite з реальним regex_score
            composite_score = CompositeScorer.compute(
                regex_score        = order.regex_score,
                behavior_score     = behavior_score,
                review_neg_pct     = review_neg_pct,
                llm_verdict        = "UNKNOWN",
                is_twin            = is_twin,
                finish_rate        = order.finish_rate_pct,
                order_count        = order.month_order_count,
                review_text_score  = review_text_score,
            )
            order.composite_score = composite_score

            # HARD EVIDENCE 1: Regex BLOCK
            if regex_result.verdict == "BLOCK":
                await self._db.save_verdict(
                    exchange, mid, order.merchant_name, terms,
                    "BLOCK", regex_result.risk_type, regex_result.reason, "regex",
                )
                block_flag = f"BLOCK:{regex_result.risk_type}:{regex_result.reason}"
                flags      = _dedupe_flags([block_flag] + review_flags + behavior_flags)
                order.risk_flag = _join_flags(flags) or block_flag
                logger.warning(
                    "🚫 Regex BLOCK: %s [%s] %s — %s",
                    order.merchant_name, exchange, regex_result.risk_type, regex_result.reason,
                )
                return

            # HARD EVIDENCE 2: Bad reviews BLOCK
            block_review = _pick_block_review(review_flags)
            if block_review:
                order.risk_flag = block_review
                logger.warning(
                    "🚫 Reviews BLOCK: %s [%s] — %s",
                    order.merchant_name, exchange, block_review,
                )
                return

            # v2: Composite BLOCK (без LLM якщо composite >= 80)
            if composite_score >= 80:
                composite_reason = f"composite_score={composite_score}/100"
                await self._db.save_verdict(
                    exchange, mid, order.merchant_name, terms,
                    "BLOCK", "COMPOSITE_HIGH_RISK", composite_reason, "composite",
                )
                flags = _dedupe_flags(
                    [f"BLOCK:COMPOSITE_HIGH_RISK:{composite_reason}"] + review_flags + behavior_flags
                )
                order.risk_flag = _join_flags(flags)
                logger.warning(
                    "🚫 Composite BLOCK: %s [%s] score=%d",
                    order.merchant_name, exchange, composite_score,
                )
                return

            temp_regex_flags = []
            if regex_result.verdict == "NEEDS_LLM":
                temp_regex_flags.append(regex_result.risk_type)
            elif regex_result.reason:
                temp_regex_flags.append(regex_result.risk_type)

            synergy_flags = self._check_synergies(order, temp_regex_flags, behavior_flags)
            if synergy_flags:
                # Синергія дає миттєвий бан!
                await self._db.save_verdict(
                    exchange, mid, order.merchant_name, terms,
                    "BLOCK", "SYNERGY", synergy_flags[0], "synergy_engine",
                )
                order.risk_flag = _join_flags(_dedupe_flags(synergy_flags + review_flags + behavior_flags))
                logger.warning(f"🚫 Synergy BLOCK: {order.merchant_name} [{exchange}] — {synergy_flags[0]}")
                return

            # COMPOSITE EVIDENCE → LLM (якщо немає синергії)
            composite_risk = ""
            if has_cross_bot and has_bad_reviews:
                composite_risk = "Мережа клонів + Негативні відгуки"

            # ── 5. LLM ──────────────────────────────────────────────────────
            if (regex_result.needs_llm or behavior_needs_llm) and self._llm:
                trusted = _is_trusted_merchant(order, score)

                if trusted and has_anomalous_behavior:
                    logger.debug("🔍 Trusted immunity stripped for %s (anomaly)", order.merchant_name)
                    trusted = False

                if trusted and regex_result.score < TRUSTED_LLM_MIN_SCORE:
                    logger.debug(
                        "✅ Trusted skip: %s [%s] score=%d < %d",
                        order.merchant_name, exchange, regex_result.score, TRUSTED_LLM_MIN_SCORE,
                    )
                    flags = list(review_flags) + list(behavior_flags)
                    if not flags and regex_result.reason:
                        flags.append(_build_weak_regex_flag(regex_result))
                    order.risk_flag = _join_flags(_dedupe_flags(flags)) or "OK"
                    return

                scheduled = self._llm.schedule(
                    exchange          = exchange,
                    merchant_id       = mid,
                    merchant_name     = order.merchant_name,
                    trade_terms       = terms,
                    regex_result      = regex_result,
                    finish_rate       = order.finish_rate_pct,
                    month_order_count = order.month_order_count,
                    is_verified       = order.is_verified,
                    min_limit         = order.min_limit,
                    max_limit         = order.max_limit,
                    behavior_flags    = behavior_flags,
                    account_age_days  = getattr(order, "account_age_days", 0),
                )

                if scheduled:
                    if composite_risk or behavior_needs_llm:
                        pending_flag = f"LLM_PENDING:BEHAVIOR:S{behavior_score}:C{composite_score}"
                    else:
                        pending_flag = _build_pending_flag(regex_result)

                    flags = _dedupe_flags([pending_flag] + review_flags + behavior_flags)
                    order.risk_flag = _join_flags(flags) or pending_flag
                    return
                else:
                    if composite_risk or behavior_needs_llm:
                        order.risk_flag = f"LLM_PENDING:COOLDOWN:C{composite_score}"
                        return

            # Без LLM або не вдалося запланувати
            flags = []
            if regex_result.reason:
                flags.append(_build_weak_regex_flag(regex_result))
            flags.extend(review_flags)
            flags.extend(behavior_flags)
            order.risk_flag = _join_flags(_dedupe_flags(flags)) or "OK"

        except Exception as e:
            logger.error("RiskEngine async помилка для %s: %s", order.merchant_name, e, exc_info=True)

    def _behavior(self, order: Order) -> list[str]:
        """
        Швидкий синхронний pre-check до завантаження снапшотів з БД.
        EXACT_LIMITS тепер виключно в behavioral_analyzer (з контекстом history).
        Тут залишаємо тільки NARROW_SPREAD — аномально вузький діапазон без
        точного співпадіння (< 1% spread, сума > 500, не верифікований).
        """
        flags = []
        if order.min_limit > 0 and order.max_limit > 0:
            max_f = float(order.max_limit)
            min_f = float(order.min_limit)
            diff  = max_f - min_f
            # EXACT_LIMITS (diff <= 5) — вже обробляється behavioral_analyzer з history
            # Тут ловимо тільки non-exact але дуже вузький діапазон
            if diff > 5:
                spread = diff / max_f
                if spread < 0.01 and max_f > 500:
                    if not (order.is_verified or order.month_order_count > 1000):
                        flags.append("NARROW_SPREAD")
        return flags

    def analyze_batch(self, orders: list[Order]) -> list[Order]:
        for order in orders:
            self.analyze(order)
        return orders

    async def analyze_batch_async(self, orders: list[Order]) -> list[Order]:
        """
        Справжній async batch — всі ордери обробляються паралельно через gather.
        Попередня версія викликала sync analyze() і ніколи не чекала async аналізу.
        """
        tasks = []
        for order in orders:
            self._analyzed += 1
            behavior_flags = self._behavior(order)
            if self._db and order.merchant_id:
                order.risk_flag = _join_flags(_dedupe_flags(behavior_flags)) or "PENDING"
                tasks.append(self._async_analyze(order, behavior_flags))
            else:
                # fallback: немає DB → sync regex
                result = regex_analyze(
                    order.trade_terms,
                    order.finish_rate_pct,
                    order.month_order_count,
                    order.is_verified,
                )
                order.regex_warn_flags = list(getattr(result, "warn_flags", []) or [])
                order.regex_score      = int(getattr(result, "score", 0) or 0)
                flags: list[str] = []
                if result.verdict == "BLOCK":
                    flags.append(f"BLOCK:{result.risk_type}:{result.reason}")
                elif result.verdict == "NEEDS_LLM":
                    flags.append(_build_pending_flag(result))
                elif result.reason:
                    flags.append(_build_weak_regex_flag(result))
                flags.extend(behavior_flags)
                order.risk_flag = _join_flags(_dedupe_flags(flags)) or "OK"

        if tasks:
            results = await asyncio.gather(*tasks, return_exceptions=True)
            for exc in results:
                if isinstance(exc, Exception):
                    logger.error("analyze_batch_async помилка: %s", exc, exc_info=False)

        return orders

    def stats(self) -> str:
        return f"RiskEngine: {self._analyzed} analyzed, {self._db_hits} db hits"