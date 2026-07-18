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
    async def load_weights(cls, db) -> None:
        """Loads weights from bot_settings database table."""
        try:
            conn = getattr(db, "db", None) or getattr(db, "_db", db)
            async with conn.execute(
                "SELECT key, value FROM bot_settings WHERE user_id = 0 AND key IN ('W_REGEX', 'W_BEHAVIOR', 'W_REVIEWS_PCT', 'W_REVIEWS_TEXT', 'W_LLM', 'W_IDENTITY')"
            ) as cur:
                rows = await cur.fetchall()
            for row in rows:
                key = row[0] if isinstance(row, tuple) else row["key"]
                val = row[1] if isinstance(row, tuple) else row["value"]
                if val is not None:
                    setattr(cls, key, float(val))
            logger.info("CompositeScorer weights loaded: W_REGEX=%.2f, W_BEHAVIOR=%.2f, W_REVIEWS_PCT=%.2f, W_REVIEWS_TEXT=%.2f, W_LLM=%.2f, W_IDENTITY=%.2f",
                        cls.W_REGEX, cls.W_BEHAVIOR, cls.W_REVIEWS_PCT, cls.W_REVIEWS_TEXT, cls.W_LLM, cls.W_IDENTITY)
        except Exception as e:
            logger.warning("Failed to load weights from bot_settings: %s", e)

    @classmethod
    def compute(
        cls,
        regex_score:           int,
        behavior_score:        int,
        review_neg_pct:        float,
        llm_verdict:           str,
        is_twin:               bool,
        finish_rate:           float = 100.0,
        order_count:           int   = 0,
        review_text_score:     float = 0.0,   # avg score текстів bad_texts
        review_trend_penalty:  int   = 0,     # -5 (improving) … +20 (worsening)
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

        # Тренд відгуків: погіршення додає бали, поліпшення знімає
        raw = raw + review_trend_penalty

        # Підвищений ризик для нових акаунтів з поганим рейтингом
        if order_count < 50 and finish_rate < 92.0:
            raw = min(100, raw * 1.3)

        return min(100, max(0, int(raw)))

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
    # Статуси що не є помилкою — просто немає даних, не штрафуємо
    if status in ("UNAVAILABLE", "NOT_SUPPORTED", "NO_AUTH", "UNKNOWN"):
        return[]

    pos       = int(summary.get("positive",  0) or 0)
    neg       = int(summary.get("negative",  0) or 0)
    neutral   = int(summary.get("neutral",   0) or 0)
    bad_texts = summary.get("bad_texts", []) or[]

    # 🚀 НОВЕ: ХАРД-БЛОК ЗА КРИТИЧНІ ВІДГУКИ (Навіть якщо він один)
    CRITICAL_CATEGORIES = {"TRIANGLE", "CHARGEBACK", "FINCRIME", "CASINO"}
    SOFT_CATEGORIES = {"CHAT_FIRST", "APPEAL_PRESSURE", "SUSPICIOUS_BIZ", "EXTERNAL_LINK", "MIDDLEMAN", "NO_COMMENTS"}
    soft_flags: list[str] = []
    unflagged_count = 0
    for bad_text_item in bad_texts:
        if isinstance(bad_text_item, dict):
            cats = bad_text_item.get("categories",[])
            if any(c in CRITICAL_CATEGORIES for c in cats):
                cat_names = ", ".join(cats)
                excerpt = str(bad_text_item.get("excerpt", ""))[:100]
                return[f"NEEDS_LLM:BADREVIEWS:Критичний відгук ({cat_names}) | {excerpt}"]
            # Збираємо м'які сигнали для LLM-контексту
            soft_cats = [c for c in cats if c in SOFT_CATEGORIES]
            if soft_cats:
                excerpt = str(bad_text_item.get("excerpt", ""))[:80]
                soft_flags.append(f"REVIEW_SOFT:{','.join(soft_cats)} | {excerpt}")
            # Відгуки без keyword-тригерів — LLM має побачити їх самостійно
            if not bad_text_item.get("keyword_flagged", True) and not cats:
                unflagged_count += 1

    # Якщо є відгуки без ключових слів — м'який сигнал для LLM
    if unflagged_count > 0:
        soft_flags.append(f"REVIEW_UNFLAGGED:{unflagged_count} відгуків без тригерів — потребують LLM аналізу")

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
        return [f"NEEDS_LLM:BADREVIEWS:{reason}"] + soft_flags

    if neg >= REVIEW_WARN_MIN_NEG and neg_pct >= REVIEW_WARN_NEG_PCT:
        return [f"BADREVIEWS:{reason}"] + soft_flags

    # Навіть без порогу — повертаємо м'які сигнали якщо є
    return soft_flags


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


def _pick_llm_review(flags: list[str]) -> str | None:
    """Знаходить review-флаг що потребує LLM перевірки (поганий відгук)."""
    for f in flags:
        if f.startswith("NEEDS_LLM:BADREVIEWS:"):
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
    # Замінюємо коми на крапку з комою — reason вбудовується в comma-joined risk_flag,
    # тому коми в тексті ламають flag.split(",") при парсингу в notifier._risk_badge
    reason    = (reason or "").strip()[:900].replace(",", ";")

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
    def __init__(self, db: Optional[MerchantDB] = None, llm_pool=None, review_fetcher=None, review_ttl: float = 24.0):
        self._db      = db
        self._llm     = llm_pool
        self._review_fetcher = review_fetcher
        self._review_ttl     = review_ttl   # для lazy fetch
        self._analyzed  = 0
        self._db_hits   = 0
        self._bot_alert_cache = TTLCache(ttl_seconds=BOT_ALERT_COOLDOWN_SEC, max_size=5000)
        self._b_cache   = TTLCache(ttl_seconds=60.0, max_size=2000)
        self._id_cache  = TTLCache(ttl_seconds=60.0, max_size=2000)
        self._db_sem    = asyncio.Semaphore(_ASYNC_ANALYZE_CONCURRENCY)

    async def _build_review_flags(self, exchange: str, merchant_id: str) -> list[str]:
        if not self._db:
            return []
        summary = await self._db.get_reviews_summary(exchange, merchant_id)
        return _build_review_flags_from_summary(summary)

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
            synergies.append("SYNERGY:Бот-автопоповнення + Зовнішній лінк")
        if is_new and has_exact and has_chargeback_warn:
            synergies.append("SYNERGY:Новий акаунт + Фікс.ліміт + Ризик рефанду")
        if has_api and has_chat_first and bad_rate:
            synergies.append("SYNERGY:Бот + Тягне в чат + Низький %")
        if has_cross and has_ext_link:
            synergies.append("SYNERGY:Кросс-біржовий клон + Зовнішній лінк")

        # Нові комбо v2.1
        if has_api and has_cross:
            # Бот на кількох біржах одночасно — беззаперечна автоматизація
            synergies.append("SYNERGY:Бот-автопоповнення + Крос-біржовий клон")
        if has_flicker and has_ext_link:
            # Скрипт блимає і виводить у зовнішній чат — класичний скам-патерн
            synergies.append("SYNERGY:Flicker-relist + Зовнішній лінк")
        if has_velocity and has_exact and not order.is_verified:
            # Висока швидкість угод + фікс.ліміт + не верифікований
            synergies.append("SYNERGY:Velocity spike + Фікс.ліміт + Не верифікований")
        if is_new and has_api:
            # Новий акаунт вже з ботом — не може бути органіки
            synergies.append("SYNERGY:Новий акаунт + Бот-автопоповнення")
        if has_static and has_anonymous:
            # Стабільний дроп + без KYC = сервіс обналу
            synergies.append("SYNERGY:Static-drop + Анонімність/без KYC")
        if has_fincrime and (has_api or has_cross):
            # Regex вже бачить fincrime-слова + бот/клон = підтверджена схема
            synergies.append("SYNERGY:Fincrime-сигнал + Автоматизований аккаунт")

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
            
            # ── Custom configurable blocks (FOP/TOV and Banka/Jar) ─────────
            # Runs on-the-fly to append metadata flags for per-user filters
            from core.analysis.regex_analyzer import check_custom_blocks_metadata
            custom_flags = check_custom_blocks_metadata(order.trade_terms)
            if custom_flags:
                existing = [f.strip() for f in getattr(order, "risk_flag", "").split(",") if f.strip()]
                unique = []
                for f in existing + custom_flags:
                    if f not in unique:
                        if f in ("OK", "PENDING"):
                            continue
                        unique.append(f)
                order.risk_flag = ",".join(unique) if unique else "OK"

    async def _async_analyze_inner(self, order: Order, behavior_flags: list[str]) -> None:
        try:
            exchange  = order.exchange
            mid       = order.merchant_id
            terms     = getattr(order, "trade_terms", "") or ""

            # ── Динамічне підтягування умов ордеру ────────────────
            # Завжди намагаємось підтягнути ПОВНІ умови для спреду, оскільки в пошуковій видачі вони обрізані або застарілі.
            if exchange == "Binance" and mid and self._review_fetcher and getattr(self._review_fetcher, "_binance", None):
                try:
                    # Отримуємо сесію Binance з БД для обходу Cloudflare
                    headers, cookies, _ = await self._db.get_auth_session("Binance")
                    if not headers or not cookies:
                        order.trade_terms = "не вдалося отримати доступ до умов через відсутність активної сесії"
                        terms = order.trade_terms
                    else:
                        profile = await self._review_fetcher._binance.fetch_merchant_profile(
                            mid, session_headers=headers, session_cookies=cookies
                        )
                        if profile:
                            # Шукаємо наш ордер за advNo (вилучаємо з order.id)
                            adv_no = order.id.replace("bn_", "") if order.id else ""
                            all_ads = profile.get("sellList", []) + profile.get("buyList", [])
                            found_remarks = ""
                            for ad in all_ads:
                                if str(ad.get("advNo", "")) == adv_no:
                                    found_remarks = ad.get("remarks") or ""
                                    break
                            # Fallback на перші непусті умови будь-якого активного оголошення
                            if not found_remarks:
                                for ad in all_ads:
                                    rem = ad.get("remarks") or ""
                                    if rem.strip():
                                        found_remarks = rem
                                        break
                            if found_remarks:
                                order.trade_terms = found_remarks.strip().lower()
                                terms = order.trade_terms
                                logger.debug("🎯 Binance terms retrieved for %s: %s", order.merchant_name, terms[:100])
                            else:
                                order.trade_terms = ""
                        else:
                            order.trade_terms = "не вдалося отримати доступ до умов через технічну помилку сесії"
                            terms = order.trade_terms
                except Exception as pe:
                    logger.debug("Не вдалось завантажити умови реклами Binance для %s: %s", order.merchant_name, pe)
                    order.trade_terms = "не вдалося отримати доступ до умов через технічну помилку сесії"
                    terms = order.trade_terms

            elif exchange == "OKX" and order.id and self._review_fetcher and hasattr(self._review_fetcher, "fetch_okx_ad_detail"):
                try:
                    headers, cookies, _ = await self._db.get_auth_session("OKX")
                    if not headers or not cookies or "authorization" not in headers:
                        order.trade_terms = "не вдалося отримати доступ до умов через відсутність активної сесії"
                        terms = order.trade_terms
                    else:
                        ad_data = await self._review_fetcher.fetch_okx_ad_detail(order.id)
                        if ad_data:
                            desc = ad_data.get("tradingOrderInfo", {}).get("tradeOrderDesc") or ""
                            if desc:
                                order.trade_terms = desc.strip().lower()
                                terms = order.trade_terms
                                logger.debug("🎯 OKX terms retrieved for %s: %s", order.merchant_name, terms[:100])
                            else:
                                order.trade_terms = ""
                        else:
                            order.trade_terms = "не вдалося отримати доступ до умов через технічну помилку сесії"
                            terms = order.trade_terms
                except Exception as pe:
                    logger.debug("Не вдалось завантажити умови реклами OKX для %s: %s", order.merchant_name, pe)
                    order.trade_terms = "не вдалося отримати доступ до умов через технічну помилку сесії"
                    terms = order.trade_terms

            cache_key = (exchange, mid)

            need_snapshots = self._b_cache.get(cache_key) is None
            need_twins     = bool(order.merchant_name) and self._id_cache.get(cache_key) is None

            coros = [
                self._db.is_blacklisted(exchange, mid, order.merchant_name),
                self._db.get_reviews_summary(exchange, mid),
                self._db.get_recent_snapshots(exchange, mid, minutes=BEHAVIOR_HISTORY_MINUTES)
                    if need_snapshots else _noop(None),
                self._db.find_digital_twins(order.merchant_name, exchange, minutes=15)
                    if need_twins else _noop(None),
                self._db.get_verdict(exchange, mid, terms),
                self._db.get_risk_score(exchange, mid),
                self._db.get_verdict_timestamp(exchange, mid),   # v2: для freshness check
                self._db.get_trade_recommendation(exchange, mid),  # v2.3: для anti-recheck guard
            ]

            (
                (is_bl, bl_reason),
                review_summary_raw,
                snapshots_raw,
                twins_raw,
                cached_verdict,
                score,
                verdict_ts,
                current_rec,
            ) = await asyncio.gather(*coros)

            # ── Lazy fetch відгуків для кандидата спреду ──────────────
            # Відгуки тягнуться ТІЛЬКИ тут — для реальних спред-кандидатів.
            # Якщо є в кеші БД (TTL 24г) → fast. Немає → fetch_now (1 запит).
            if self._review_fetcher:
                needs_fetch = await self._db.needs_review_fetch(exchange, mid, self._review_ttl if hasattr(self, '_review_ttl') else 24.0)
                if needs_fetch:
                    logger.debug("⏳ Lazy fetch відгуків: %s [%s]", order.merchant_name, exchange)
                    review_summary_raw = await self._review_fetcher.fetch_now(exchange, mid)
                elif not review_summary_raw:
                    # Є в кеші але не завантажено в цьому запиті
                    review_summary_raw = await self._db.get_reviews_summary(exchange, mid)

            # ── Bybit / Binance: fallback pos/neg з Order-статистики ────────
            # Profile API обох бірж повертає 404 — positive=0 завжди в БД.
            # Але в search response є month_order_count і:
            #   - Bybit:   finish_rate_pct (completion rate, ~100%)
            #   - Binance: positive_rate   (review rate, 0.0–1.0)
            # Інжектуємо positive коли він нульовий, щоб neg_pct не був 100%:
            #   - Без сесії (neg=0):  pos=972, neg=0  → neg_pct=0%   ✓
            #   - З сесією  (neg=3):  pos=969, neg=3  → neg_pct=0.3% ✓
            if (
                exchange in ("Bybit", "Binance")
                and review_summary_raw
                and review_summary_raw.get("status") in ("OK", "NO_SESSION", "NO_FEEDBACK")
                and order.month_order_count > 0
                and review_summary_raw.get("positive", 0) == 0
            ):
                neg_known = int(review_summary_raw.get("negative", 0) or 0)
                total_est = order.month_order_count
                neg_est = neg_known  # default

                if exchange == "Binance" and getattr(order, "positive_rate", 0) > 0:
                    # Binance: positive_rate = 0.99310344 → neg = 972 * (1 - 0.993) ≈ 7
                    neg_est = max(neg_known, int(total_est * (1.0 - order.positive_rate)))

                pos_est = max(0, total_est - neg_est)

                review_summary_raw = dict(review_summary_raw)
                review_summary_raw["positive"] = pos_est
                review_summary_raw["negative"] = neg_est
                review_summary_raw["estimated_from_stats"] = True
                logger.debug(
                    "%s pos fallback (profile 404): %s pos=%d neg=%d (orders=%d)",
                    exchange, order.merchant_name, pos_est, neg_est, total_est,
                )

            # ── Blacklist: найвищий пріоритет ──────────────────────────────
            if is_bl:
                order.risk_flag = f"BLOCK:BLACKLIST:{bl_reason}"
                logger.warning("🚫 Blacklist: %s [%s] — %s", order.merchant_name, exchange, bl_reason)
                return

            # ── 1.1 Regex Direct Block check (always runs to catch absolute stop-words) ──
            regex_result = regex_analyze(
                terms,
                order.finish_rate_pct,
                order.month_order_count,
                order.is_verified,
            )
            order.regex_warn_flags = list(getattr(regex_result, "warn_flags", []) or [])
            order.regex_score      = int(getattr(regex_result, "score", 0) or 0)

            from core.analysis.rules import HARD_DIRECT_BLOCK
            if regex_result.verdict == "BLOCK" and regex_result.risk_type in HARD_DIRECT_BLOCK:
                order.risk_flag = f"BLOCK:{regex_result.risk_type}:{regex_result.reason}"
                logger.warning("🚫 Regex Direct Fallback Block: %s [%s] — Category=%s, Reason=%s",
                               order.merchant_name, exchange, regex_result.risk_type, regex_result.reason)
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

            # ── v2.2: Тренд відгуків (зберігаємо снапшот + рахуємо штраф) ───────────
            review_trend_penalty = 0
            rev_status_for_snap = rev_summary.get("status", "")
            if rev_total > 0 and rev_status_for_snap == "OK":
                # Записуємо снапшот для історії тренду
                try:
                    await self._db.save_review_snapshot(exchange, mid, rev_pos, rev_neg, review_neg_pct)
                except Exception as _snap_err:
                    logger.debug("save_review_snapshot error: %s", _snap_err)
                # Отримуємо тренд за 7 днів
                try:
                    trend_data = await self._db.get_review_trend(exchange, mid, days=7)
                    trend_dir = trend_data.get("trend", "stable")
                    if trend_dir == "worsening":
                        review_trend_penalty = 20   # +20 балів за погіршення
                        behavior_flags.append("TREND_WORSENING")
                        logger.debug(
                            "📈 Review trend WORSENING: %s [%s] delta=%.1f%%",
                            order.merchant_name, exchange, trend_data.get("delta", 0),
                        )
                    elif trend_dir == "improving":
                        review_trend_penalty = -5   # -5 балів за поліпшення
                except Exception as _trend_err:
                    logger.debug("get_review_trend error: %s", _trend_err)

            # ── 1. Behavioral ───────────────────────────────────────────────
            cached_b = self._b_cache.get(cache_key)
            if cached_b is not None:
                behavior_result = cached_b
                try:
                    from core.analytics.metrics import cache_requests_total
                    cache_requests_total.labels(cache_type="behavior", result="hit").inc()
                except Exception:
                    pass
            else:
                behavior_result = analyze_history(order, snapshots_raw or [])
                self._b_cache.set(cache_key, behavior_result)
                try:
                    from core.analytics.metrics import cache_requests_total
                    cache_requests_total.labels(cache_type="behavior", result="miss").inc()
                except Exception:
                    pass

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
                    try:
                        from core.analytics.metrics import cache_requests_total
                        cache_requests_total.labels(cache_type="identity", result="hit").inc()
                    except Exception:
                        pass
                else:
                    id_result = analyze_identity(order, twins_raw or [])
                    self._id_cache.set(cache_key, id_result)
                    try:
                        from core.analytics.metrics import cache_requests_total
                        cache_requests_total.labels(cache_type="identity", result="miss").inc()
                    except Exception:
                        pass

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
                regex_score        = order.regex_score,
                behavior_score     = behavior_score,
                review_neg_pct     = review_neg_pct,
                llm_verdict        = "UNKNOWN",
                is_twin            = is_twin,
                finish_rate        = order.finish_rate_pct,
                order_count        = order.month_order_count,
                review_text_score  = review_text_score,
                review_trend_penalty = review_trend_penalty,
            )
            order.composite_score = composite_score   # зберігаємо на ордері

            # ── Матриця доказів ─────────────────────────────────────────────
            has_exact_limits   = any(f.startswith("EXACT_LIMITS") or f.startswith("STATIC_DROP") for f in behavior_flags)
            has_cross_bot      = any(f.startswith("CROSS_EXCHANGE_BOT") for f in behavior_flags)
            has_api_replenish  = any(f.startswith("API_REPLENISH") for f in behavior_flags)
            has_bad_reviews    = any(f.startswith("BADREVIEWS") for f in review_flags)
            has_anomalous_behavior = has_exact_limits or has_cross_bot or has_api_replenish

            # ── v2: Verdict freshness — інвалідуємо застарілий кеш ─────────
            is_recheck = False  # чи це перепровірка (вердикт був, але інвалідований)
            if cached_verdict and _is_verdict_stale(verdict_ts):
                logger.debug(
                    "🔄 Verdict freshness: %s [%s] вердикт старший %dd, перераховуємо",
                    order.merchant_name, exchange, VERDICT_MAX_AGE_DAYS,
                )
                cached_verdict = None
                is_recheck = True

            # Cache override при поведінкових аномаліях
            if has_anomalous_behavior and cached_verdict == "OK":
                cached_verdict = None
                is_recheck = True
                logger.debug("💥 Cache Override: аномалії для %s", order.merchant_name)

            # ── v2.2: Інвалідація вердикту коли відгуки стали доступні ────
            # Якщо вердикт був зроблений БЕЗ текстів відгуків (NO_SESSION/estimated),
            # а тепер вони доступні — LLM має перепроаналізувати з реальними текстами.
            is_pending = False
            if self._llm:
                cache_key = f"{exchange}:{mid}"
                is_in_pending = hasattr(self._llm, "_pending") and (exchange, mid) in self._llm._pending
                is_seen_recent = hasattr(self._llm, "_recent_calls") and hasattr(self._llm._recent_calls, "seen") and self._llm._recent_calls.seen(cache_key)
                if is_in_pending or is_seen_recent:
                    is_pending = True

            # v2.3: якщо БД вже містить RECHECKING — LLM вже запущений (або недавно завершився
            # але TTL _recent_calls ще не вичерпався). Не запускаємо повторно.
            if current_rec == "RECHECKING":
                is_pending = True

            # Мінімальний поріг між оновленням відгуків і вердиктом — 60 секунд.
            # Без цього порогу LLM завершує роботу, зберігає updated_at=T,
            # але rev_updated може бути T-10s або T+5s — і умова rev_updated>verdict_ts
            # одразу спрацьовує знову на наступному циклі.
            REV_VERDICT_MIN_DELTA = 60.0

            if cached_verdict is not None and rev_summary and not is_pending:
                rev_updated = float(rev_summary.get("updated_at", 0) or 0)
                rev_status = rev_summary.get("status", "")
                has_real_texts = bool(bad_texts)  # bad_texts вже визначено вище
                # Вердикт зроблено ДО оновлення відгуків — тексти з'явились після
                # Перевіряємо мінімальну різницю щоб уникнути повторних тригерів
                if (
                    rev_updated > 0
                    and verdict_ts > 0
                    and rev_updated > verdict_ts + REV_VERDICT_MIN_DELTA
                ):
                    if has_real_texts:
                        logger.info(
                            "🔄 Review upgrade: %s [%s] — вердикт від %.0fs ago, "
                            "відгуки оновлені %.0fs ago з %d bad_texts → перерахунок LLM",
                            order.merchant_name, exchange,
                            time.time() - verdict_ts, time.time() - rev_updated,
                            len(bad_texts),
                        )
                        cached_verdict = None
                        is_recheck = True
                    elif rev_status == "OK" and not rev_summary.get("estimated_from_stats"):
                        # Навіть без bad_texts — якщо статус змінився на OK
                        # (сесія з'явилась, відгуки перевірені), перераховуємо
                        logger.info(
                            "🔄 Review status upgrade: %s [%s] — "
                            "відгуки тепер OK (раніше вердикт без текстів) → перерахунок LLM",
                            order.merchant_name, exchange,
                        )
                        cached_verdict = None
                        is_recheck = True

            # ── 3. Повернення з кешу ────────────────────────────────────────
            if score >= 80 and cached_verdict == "OK":
                flags = _dedupe_flags(["HIGH_RISK_SCORE"] + review_flags + behavior_flags)
                order.risk_flag = _join_flags(flags) or "HIGH_RISK_SCORE"
                return

            if cached_verdict is not None:
                self._db_hits += 1
                cached_flag   = await _build_cached_flag(cached_verdict, exchange, mid, self._db)

                block_review = _pick_llm_review(review_flags)
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
            # (regex_result вже розраховано на кроці 1.1)
            has_soft_regex         = order.regex_score > 0

            # Regex BLOCK → NEEDS_LLM з підвищеним пріоритетом
            # Регекс може тільки ПІДОЗРЮВАТИ, фінальне рішення — за LLM
            if regex_result.verdict == "BLOCK":
                logger.debug(
                    "🔍 Regex escalate→LLM: %s [%s] %s",
                    order.merchant_name, exchange, regex_result.risk_type,
                )
                regex_result.verdict   = "NEEDS_LLM"
                regex_result.needs_llm = True
                # needs_llm=True → LLM буде викликаний нижче

            # Bad reviews → додаємо до контексту LLM, не блокуємо одразу
            # LLM побачить відгуки і сам вирішить (BLOCK/SUSPICIOUS/OK)
            review_llm_flag = _pick_llm_review(review_flags)
            if review_llm_flag:
                # Якщо є погані відгуки — обов'язково через LLM
                if not regex_result.needs_llm:
                    regex_result.needs_llm = True
                    regex_result.verdict   = "NEEDS_LLM"
                logger.debug("📋 Bad reviews → escalate to LLM: %s [%s]", order.merchant_name, exchange)

            # Composite >= 80 → обов'язково через LLM (не автобан)
            # LLM отримає повний контекст: score, відгуки, умови, поведінку
            if composite_score >= 80:
                logger.debug(
                    "📊 Composite high score=%d → escalate to LLM: %s [%s]",
                    composite_score, order.merchant_name, exchange,
                )
                if not regex_result.needs_llm:
                    regex_result.needs_llm = True
                    regex_result.verdict   = "NEEDS_LLM"

            temp_regex_flags = []
            if regex_result.verdict == "NEEDS_LLM":
                temp_regex_flags.append(regex_result.risk_type)
            elif regex_result.reason:
                temp_regex_flags.append(regex_result.risk_type)

            synergy_flags = self._check_synergies(order, temp_regex_flags, behavior_flags)
            if synergy_flags:
                # Синергія → escalate to LLM з контекстом
                # LLM бачить синергійні флаги і підтверджує/спростовує
                logger.debug("🔗 Synergy detected → escalate to LLM: %s [%s] — %s",
                    order.merchant_name, exchange, synergy_flags[0])
                if not regex_result.needs_llm:
                    regex_result.needs_llm = True
                    regex_result.verdict   = "NEEDS_LLM"
                # Додаємо синергійні флаги до behavior щоб LLM їх бачив
                behavior_flags = _dedupe_flags(behavior_flags + synergy_flags)

            # COMPOSITE EVIDENCE → LLM (якщо немає синергії)
            composite_risk = ""
            if has_cross_bot and has_bad_reviews:
                composite_risk = "Мережа клонів + Негативні відгуки"

            # EXACT_LIMITS → обов'язково через LLM
            # min ≈ max — класичний бот-процесинг або трикутна схема
            if has_exact_limits and not regex_result.needs_llm:
                logger.debug(
                    "🎯 Exact limits → LLM: %s [%s] (%s–%s)",
                    order.merchant_name, exchange, order.min_limit, order.max_limit,
                )
                regex_result.needs_llm = True
                regex_result.verdict   = "NEEDS_LLM"
                if not regex_result.risk_type:
                    regex_result.risk_type = "EXACT_LIMITS"

            # WARN flags без вердикту → через LLM
            # Мерчанти з regex warn (квитанція, 3-ті особи тощо) мають пройти AI аналіз
            if (
                not regex_result.needs_llm
                and not behavior_needs_llm
                and cached_verdict is None
                and (regex_result.reason or behavior_flags)
                and self._llm
            ):
                logger.debug(
                    "🔎 No verdict + warn signals → LLM: %s [%s] flags=%s",
                    order.merchant_name, exchange,
                    ",".join(behavior_flags[:3]) or regex_result.risk_type or "WARN",
                )
                regex_result.needs_llm = True
                regex_result.verdict   = "NEEDS_LLM"
                if not regex_result.risk_type:
                    regex_result.risk_type = "SUSPICIOUS"

            # ── Проактивний скринінг: чистий мерчант без вердикту → LLM ──────
            # Якщо regex нічого не знайшов і поведінка чиста — це НЕ означає що
            # мерчант безпечний. LLM має проаналізувати trade_terms + відгуки
            # і дати явний verdict/trade_recommendation (APPROVE/CONDITIONAL/REJECT).
            # Без цього блоку мерчант отримує "OK" без перевірки, і trade_recommendation
            # залишається "PENDING" навічно.
            if (
                not regex_result.needs_llm
                and not behavior_needs_llm
                and cached_verdict is None
                and not regex_result.reason
                and not behavior_flags
                and self._llm
            ):
                if is_recheck:
                    logger.debug(
                        "🔄 Recheck → LLM: %s [%s] (verdict invalidated, re-analyzing)",
                        order.merchant_name, exchange,
                    )
                else:
                    logger.debug(
                        "🛡 Proactive screening → LLM: %s [%s] (no signals, no verdict)",
                        order.merchant_name, exchange,
                    )
                regex_result.needs_llm = True
                regex_result.verdict   = "NEEDS_LLM"
                regex_result.risk_type = "RECHECK" if is_recheck else "PROACTIVE"

            # ── 5. LLM ──────────────────────────────────────────────────────
            if (regex_result.needs_llm or behavior_needs_llm) and self._llm:
                trusted = _is_trusted_merchant(order, score)

                if trusted and has_anomalous_behavior:
                    logger.debug("🔍 Trusted immunity stripped for %s (anomaly)", order.merchant_name)
                    trusted = False

                # Trusted skip: пропускаємо LLM тільки якщо немає поганих відгуків
                # і це НЕ проактивний скринінг (перша перевірка — завжди потрібна)
                is_proactive = regex_result.risk_type in ("PROACTIVE", "RECHECK")
                has_review_concern = any("BADREVIEWS" in f for f in review_flags)
                if trusted and regex_result.score < TRUSTED_LLM_MIN_SCORE and not has_review_concern and not is_proactive:
                    logger.debug(
                        "✅ Trusted skip: %s [%s] score=%d, no bad reviews",
                        order.merchant_name, exchange, regex_result.score,
                    )
                    flags = list(review_flags) + list(behavior_flags)
                    if not flags and regex_result.reason:
                        flags.append(_build_weak_regex_flag(regex_result))
                    order.risk_flag = _join_flags(_dedupe_flags(flags)) or "OK"
                    return

                # Передаємо review_summary в LLM — відгуки є ключовим сигналом
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
                    review_summary    = review_summary_raw or {},  # ← відгуки явно
                )

                if scheduled:
                    # Якщо це перепровірка — оновлюємо rec в БД щоб алерт показав "AI перепровіряє"
                    if is_recheck:
                        await self._db.mark_rechecking(exchange, mid)

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
        LOW_STATS: статистика нижче порогових значень.
        PERFECT_RATING: підозріло високий рейтинг у неверифікованого.
        EXACT_LIMITS / SUSPICIOUS_LIMITS: min ≈ max.
        NARROW_SPREAD: аномально вузький діапазон.
        """
        flags = []
        exchange = getattr(order, "exchange", "Binance") or "Binance"

        # 1. LOW_STATS
        min_orders_threshold = MIN_ORDERS.get(exchange, 30)
        min_completion_threshold = MIN_COMPLETION.get(exchange, 90.0)
        if order.month_order_count < min_orders_threshold or order.finish_rate_pct < min_completion_threshold:
            flags.append("LOW_STATS")

        # 2. PERFECT_RATING
        if order.finish_rate_pct >= 99.9 and not order.is_verified and order.month_order_count >= 50:
            flags.append("PERFECT_RATING")

        # 3. Limits & Spread checks
        if order.min_limit > 0 and order.max_limit > 0:
            max_f = float(order.max_limit)
            min_f = float(order.min_limit)
            diff  = max_f - min_f
            is_exact = abs(diff) <= 5.0

            if is_exact:
                flags.append("EXACT_LIMITS")
                flags.append("SUSPICIOUS_LIMITS")
            else:
                spread = diff / max_f
                if spread < 0.02 and max_f > 500:
                    if not (order.is_verified or order.month_order_count > 1000):
                        flags.append("SUSPICIOUS_LIMITS")

                if spread <= 0.01 and max_f > 500:
                    if not (order.is_verified or order.month_order_count > 1000):
                        flags.append("NARROW_SPREAD")
        return flags

    def analyze_batch(self, orders: list[Order]) -> list[Order]:
        for order in orders:
            self.analyze(order)
        return orders

    async def analyze_for_spread(self, orders: list[Order]) -> None:
        """
        Аналізує ордери для спреду ПАРАЛЕЛЬНО і ЧЕКАЄ завершення.

        На відміну від analyze() (fire-and-forget), цей метод гарантує що
        risk_flag встановлено до повернення — кешований LLM вердикт буде
        у Telegram алерті (а не тільки в наступному циклі).

        LLM scheduling всередині — все ще async (fire and forget).
        Behavioral sync pre-check виконується синхронно як завжди.
        """
        tasks = []
        for order in orders:
            self._analyzed += 1
            behavior_flags = self._behavior(order)
            if self._db and order.merchant_id:
                # Встановлюємо початковий прапор (синхронно) — бачимо хоча б behavioral
                initial_flags = _dedupe_flags(behavior_flags)
                order.risk_flag = _join_flags(initial_flags) if initial_flags else "PENDING"
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
                    logger.error("analyze_for_spread помилка: %s", exc, exc_info=False)

        # ── Custom configurable blocks (FOP/TOV and Banka/Jar) ─────────
        from core.analysis.regex_analyzer import check_custom_blocks_metadata
        for order in orders:
            custom_flags = check_custom_blocks_metadata(order.trade_terms)
            if custom_flags:
                existing = [f.strip() for f in getattr(order, "risk_flag", "").split(",") if f.strip()]
                unique = []
                for f in existing + custom_flags:
                    if f not in unique:
                        if f in ("OK", "PENDING"):
                            continue
                        unique.append(f)
                order.risk_flag = ",".join(unique) if unique else "OK"

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