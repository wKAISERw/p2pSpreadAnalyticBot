# core/risk_engine.py

import asyncio
import logging
from typing import Optional

from exchanges.base import Order
from core.regex_analyzer import analyze as regex_analyze, RegexResult
from core.merchant_db import MerchantDB

logger = logging.getLogger("RiskEngine")

MIN_ORDERS: dict[str, int] = {
    "Binance": 50,
    "Bybit": 30,
    "OKX": 30,
    "Wallet": 10,
    "MEXC": 20,
    "CryptoBot": 15,
}

MIN_COMPLETION: dict[str, float] = {
    "Binance": 95.0,
    "Bybit": 92.0,
    "OKX": 92.0,
    "Wallet": 88.0,
    "MEXC": 90.0,
    "CryptoBot": 85.0,
}

# ── Trusted merchant — знижений поріг для LLM ескалації ─────────────────────
# Якщо мерчант відповідає всім умовам "довіреного" — слабкі regex сигнали
# не ескалуються в LLM, щоб уникнути false positives
TRUSTED_MIN_ORDERS      = 500    # Мінімум угод
TRUSTED_MIN_COMPLETION  = 95.0   # Мінімум % виконання
TRUSTED_MAX_RISK_SCORE  = 30     # Накопичений ризик не більше цього
TRUSTED_LLM_MIN_SCORE   = 60     # Для trusted — ескалуємо тільки якщо regex score >= 60
# (звичайний поріг — 30, тобто для топ-мерчантів планку підіймаємо вдвічі)

REVIEW_WARN_NEG_PCT = 15.0
REVIEW_WARN_MIN_NEG = 3
REVIEW_BLOCK_NEG_PCT = 25.0
REVIEW_BLOCK_MIN_NEG = 5


def _is_trusted_merchant(order, risk_score: int = 0) -> bool:
    """
    Повертає True якщо мерчант вважається довіреним.
    Для таких мерчантів поріг LLM ескалації підвищується вдвічі.
    """
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

    def analyze(self, order: Order) -> Order:
        self._analyzed += 1
        behavior_flags = self._behavior(order)

        if self._db and order.merchant_id:
            asyncio.ensure_future(self._async_analyze(order, behavior_flags))
            order.risk_flag = ",".join(behavior_flags) if behavior_flags else "PENDING"
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
        order.risk_flag = ",".join(flags) if flags else "OK"
        return order

    async def _async_analyze(self, order: Order, behavior_flags: list[str]) -> None:
        try:
            exchange = order.exchange
            mid = order.merchant_id
            terms = order.trade_terms

            is_bl, bl_reason = await self._db.is_blacklisted(exchange, mid)
            if is_bl:
                order.risk_flag = f"BLOCK:BLACKLIST:{bl_reason}"
                logger.warning(
                    "🚫 Blacklist: %s [%s] — %s",
                    order.merchant_name, exchange, bl_reason
                )
                return

            review_flags = await self._build_review_flags(exchange, mid)

            cached_verdict = await self._db.get_verdict(exchange, mid, terms)
            if cached_verdict and cached_verdict not in ("NEEDS_LLM",):
                self._db_hits += 1

            score = await self._db.get_risk_score(exchange, mid)
            if score >= 80 and cached_verdict == "OK":
                flags = ["HIGH_RISK_SCORE"] + review_flags + behavior_flags
                order.risk_flag = _join_flags(flags) or "HIGH_RISK_SCORE"
                return

            if cached_verdict is not None:
                cached_flag = await _build_cached_flag(
                    cached_verdict, exchange, mid, self._db
                )

                block_review = _pick_block_review(review_flags)
                if block_review:
                    order.risk_flag = block_review
                    return

                flags: list[str] = []
                if cached_flag and cached_flag != "OK":
                    flags.append(cached_flag)
                flags.extend(review_flags)
                flags.extend(behavior_flags)

                order.risk_flag = _join_flags(flags) or "OK"
                return

            regex_result = regex_analyze(
                terms,
                order.finish_rate_pct,
                order.month_order_count,
                order.is_verified,
            )

            order.regex_warn_flags = list(getattr(regex_result, "warn_flags", []) or [])
            order.regex_score = int(getattr(regex_result, "score", 0) or 0)

            if regex_result.verdict == "BLOCK":
                await self._db.save_verdict(
                    exchange,
                    mid,
                    order.merchant_name,
                    terms,
                    "BLOCK",
                    regex_result.risk_type,
                    regex_result.reason,
                    "regex",
                )
                block_flag = f"BLOCK:{regex_result.risk_type}:{regex_result.reason}"

                flags = [block_flag] + review_flags + behavior_flags
                order.risk_flag = _join_flags(flags) or block_flag

                logger.warning(
                    "🚫 Regex BLOCK: %s [%s] %s — %s",
                    order.merchant_name,
                    exchange,
                    regex_result.risk_type,
                    regex_result.reason,
                )
                return

            if regex_result.needs_llm and self._llm:
                # ── Trusted merchant threshold ────────────────────────────────
                # Для топ-мерчантів слабкі сигнали не ескалуємо — уникаємо false positives
                risk_score = await self._db.get_risk_score(exchange, mid)
                trusted = _is_trusted_merchant(order, risk_score)

                llm_min_score = TRUSTED_LLM_MIN_SCORE if trusted else 0

                if regex_result.score < llm_min_score:
                    block_review = _pick_block_review(review_flags)
                    if block_review:
                        order.risk_flag = block_review
                        return

                    logger.debug(
                        "✅ Trusted skip: %s [%s] score=%d < %d",
                        order.merchant_name,
                        exchange,
                        regex_result.score,
                        llm_min_score,
                    )

                    flags: list[str] = []
                    flags.extend(review_flags)
                    flags.extend(behavior_flags)

                    if not flags and regex_result.reason:
                        flags.append(_build_weak_regex_flag(regex_result))

                    order.risk_flag = _join_flags(flags) or "OK"
                    return

                if trusted:
                    logger.debug(
                        "🔍 Trusted але score=%d >= %d → LLM: %s [%s]",
                        regex_result.score, llm_min_score,
                        order.merchant_name, exchange,
                    )

                scheduled = self._llm.schedule(
                    exchange, mid, order.merchant_name, terms, regex_result
                )
                if scheduled:
                    flags = [_build_pending_flag(regex_result)] + review_flags + behavior_flags
                    order.risk_flag = _join_flags(flags) or _build_pending_flag(regex_result)
                    return

            block_review = _pick_block_review(review_flags)
            if block_review:
                order.risk_flag = block_review
                return

            flags: list[str] = []

            if regex_result.reason:
                flags.append(_build_weak_regex_flag(regex_result))

            flags.extend(review_flags)
            flags.extend(behavior_flags)

            order.risk_flag = _join_flags(flags) or "OK"

        except Exception as e:
            logger.error(
                "RiskEngine async помилка для %s: %s",
                order.merchant_name, e, exc_info=True
            )

    async def _build_review_flags(self, exchange: str, merchant_id: str) -> list[str]:
        if not self._db or not merchant_id:
            return []

        summary = await self._db.get_reviews_summary(exchange, merchant_id)

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

    def _behavior(self, order: Order) -> list[str]:
        flags = []
        exchange = order.exchange

        min_ord = MIN_ORDERS.get(exchange, 30)
        min_comp = MIN_COMPLETION.get(exchange, 90.0)

        if order.month_order_count < min_ord or order.finish_rate_pct < min_comp:
            flags.append("LOW_STATS")

        if (
            order.month_order_count >= 50
            and order.finish_rate_pct >= 99.9
            and not order.is_verified
        ):
            flags.append("PERFECT_RATING")

        if order.min_limit > 0 and order.max_limit > 0:
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


async def _build_cached_flag(
    verdict: str,
    exchange: str,
    merchant_id: str,
    db: MerchantDB,
) -> str:
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