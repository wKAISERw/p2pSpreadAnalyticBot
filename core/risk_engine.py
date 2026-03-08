# core/risk_engine.py
"""
Risk Engine — оркестратор антифрод-системи.

Pipeline на кожен ордер:
  1. SQLite кеш → якщо є свіжий вердикт і хеш не змінився → одразу
  2. Regex → критичне → BLOCK одразу, пишемо в БД
  3. Regex → підозріло → NEEDS_LLM → schedule в LLMWorkerPool (неблокуючий)
  4. order.risk_flag = поточний стан (може бути PENDING поки LLM думає)
  5. BehaviorAnalyzer — статистика (LOW_STATS, SUSPICIOUS_LIMITS)
"""

import asyncio
import logging
from typing import Optional

from exchanges.base import Order
from core.regex_analyzer import analyze as regex_analyze, RegexResult
from core.merchant_db import MerchantDB

logger = logging.getLogger("RiskEngine")

# ── Плаваючі пороги ───────────────────────────────────────────────────────────
MIN_ORDERS: dict[str, int] = {
    "Binance":   50,
    "Bybit":     30,
    "OKX":       30,
    "Wallet":    10,
    "MEXC":      20,
    "CryptoBot": 15,
}

MIN_COMPLETION: dict[str, float] = {
    "Binance":   95.0,
    "Bybit":     92.0,
    "OKX":       92.0,
    "Wallet":    88.0,
    "MEXC":      90.0,
    "CryptoBot": 85.0,
}


class RiskEngine:
    def __init__(self, db: Optional[MerchantDB] = None, llm_pool=None):
        self._db       = db
        self._llm      = llm_pool   # LLMWorkerPool
        self._analyzed = 0
        self._db_hits  = 0

    def analyze(self, order: Order) -> Order:
        """
        Синхронна точка входу — не блокує.
        Важкі операції (LLM) відправляються у фон через asyncio.ensure_future.
        """
        self._analyzed += 1

        # ── 1. BehaviorAnalyzer — завжди, незалежно від кешу ─────────────────
        behavior_flags = self._behavior(order)

        # ── 2. Перевірка кешу (синхронно через ensure_future) ────────────────
        if self._db and order.merchant_id:
            asyncio.ensure_future(
                self._async_analyze(order, behavior_flags)
            )
            # Поки LLM думає — ставимо поточний стан
            if behavior_flags:
                order.risk_flag = ",".join(behavior_flags)
            else:
                order.risk_flag = "PENDING"
        else:
            # Без БД — тільки regex + behavior
            result = regex_analyze(
                order.trade_terms,
                order.finish_rate_pct,
                order.month_order_count,
                order.is_verified,
            )
            flags = []
            if result.verdict == "BLOCK":
                flags.append(f"BLOCK:{result.risk_type}:{result.reason}")
            elif result.verdict == "NEEDS_LLM":
                flags.append(result.risk_type or "SUSPICIOUS")
            flags.extend(behavior_flags)
            order.risk_flag = ",".join(flags) if flags else "OK"

        return order

    async def _async_analyze(self, order: Order, behavior_flags: list[str]) -> None:
        """Фоновий аналіз — DB кеш → Regex → LLM schedule."""
        try:
            exchange    = order.exchange
            mid         = order.merchant_id
            terms       = order.trade_terms

            # ── Крок 0: Global Blacklist — найшвидша перевірка ─────────────
            is_bl, bl_reason = await self._db.is_blacklisted(exchange, mid)
            if is_bl:
                order.risk_flag = f"BLOCK:BLACKLIST:{bl_reason}"
                logger.warning("🚫 Blacklist: %s [%s] — %s",
                               order.merchant_name, exchange, bl_reason)
                return

            # ── Крок 1: Кеш ──────────────────────────────────────────────────
            cached_verdict = await self._db.get_verdict(exchange, mid, terms)

            if cached_verdict and cached_verdict not in ("NEEDS_LLM",):
                self._db_hits += 1
                # Якщо risk_score > 80 навіть при OK — показуємо попередження
                score = await self._db.get_risk_score(exchange, mid)
                if score >= 80 and cached_verdict == "OK":
                    order.risk_flag = "HIGH_RISK_SCORE"
                    return
                order.risk_flag = _build_flag(cached_verdict, exchange, mid,
                                              behavior_flags, self._db)
                return

            # ── Крок 2: Regex ─────────────────────────────────────────────────
            regex_result = regex_analyze(
                terms,
                order.finish_rate_pct,
                order.month_order_count,
                order.is_verified,
            )

            if regex_result.verdict == "BLOCK":
                # Критичне — зберігаємо одразу, LLM не потрібна
                await self._db.save_verdict(
                    exchange, mid, order.merchant_name, terms,
                    "BLOCK", regex_result.risk_type, regex_result.reason, "regex"
                )
                flag = f"BLOCK:{regex_result.risk_type}:{regex_result.reason}"
                all_flags = [flag] + behavior_flags
                order.risk_flag = ",".join(all_flags)
                logger.warning("🚫 Regex BLOCK: %s [%s] %s — %s",
                               order.merchant_name, exchange,
                               regex_result.risk_type, regex_result.reason)
                return

            # ── Крок 3: Schedule LLM (якщо потрібно) ─────────────────────────
            if regex_result.needs_llm and self._llm:
                scheduled = self._llm.schedule(
                    exchange, mid, order.merchant_name, terms, regex_result
                )
                if scheduled:
                    # Показуємо PENDING поки LLM думає
                    pending_flag = "LLM_PENDING"
                    all_flags = [pending_flag] + behavior_flags
                    order.risk_flag = ",".join(all_flags) if all_flags else pending_flag
                    return

            # ── Крок 4: Все чисто ─────────────────────────────────────────────
            order.risk_flag = ",".join(behavior_flags) if behavior_flags else "OK"

        except Exception as e:
            logger.error("RiskEngine async помилка для %s: %s",
                         order.merchant_name, e, exc_info=True)

    def _behavior(self, order: Order) -> list[str]:
        """Статистичний аналіз — завжди синхронний."""
        flags = []
        exchange = order.exchange

        min_ord  = MIN_ORDERS.get(exchange, 30)
        min_comp = MIN_COMPLETION.get(exchange, 90.0)

        if order.month_order_count < min_ord or order.finish_rate_pct < min_comp:
            flags.append("LOW_STATS")

        # --- НОВА МИТТЄВА ПЕРЕВІРКА НА ПІДОЗРІЛИЙ ІДЕАЛ ---
        if order.month_order_count >= 50 and order.finish_rate_pct >= 99.9 and not order.is_verified:
            flags.append("PERFECT_RATING")
        # --------------------------------------------------

        if order.min_limit > 0 and order.max_limit > 0:
            spread = float(order.max_limit - order.min_limit) / float(order.max_limit)
            if spread < 0.02 and float(order.max_limit) > 500:
                if not (order.is_verified or order.month_order_count > 1000):
                    flags.append("SUSPICIOUS_LIMITS")

        return flags

        return flags

    def analyze_batch(self, orders: list[Order]) -> list[Order]:
        for order in orders:
            self.analyze(order)
        return orders

    def stats(self) -> str:
        return (f"RiskEngine: {self._analyzed} analyzed, "
                f"{self._db_hits} db hits")


def _build_flag(verdict: str, exchange: str, merchant_id: str,
                behavior_flags: list[str], db: MerchantDB) -> str:
    """Будує risk_flag з кешованого вердикту."""
    flags = []
    if verdict == "BLOCK":
        # reason підтягнемо асинхронно — поки просто BLOCK
        flags.append("BLOCK:CACHED")
    elif verdict == "SUSPICIOUS":
        flags.append("LLM_SUSPICIOUS")
    elif verdict == "UNKNOWN":
        flags.append("LLM_UNKNOWN")
    flags.extend(behavior_flags)
    return ",".join(flags) if flags else "OK"