# core/engine/taker_scanner.py
"""
TakerScanner — пайплайн для режимів TAKER_BUY / TAKER_SELL.

Замість пошуку зв'язок (buy+sell), фільтрує ОДНУ сторону ордерів
під персональні фільтри юзера (банки, ліміти, ціновий діапазон, мерчант-фільтри).
"""
from __future__ import annotations
import logging
from exchanges.base import Order
from config.defaults import MIN_ORDERS, MIN_COMPLETION

logger = logging.getLogger("TakerScanner")


class TakerScanner:
    """
    Фільтрує ордери для taker-only режимів.
    TAKER_BUY: sell-ордери (хто продає USDT) — ти купуєш.
    TAKER_SELL: buy-ордери (хто купує USDT) — ти продаєш.
    """

    def find_orders_for_user(
        self, user: dict,
        buy_grouped: dict[str, list[Order]],
        sell_grouped: dict[str, list[Order]],
    ) -> list[Order]:
        """Знаходить підходящі ордери для тейкер-юзера."""
        mode = user.get("scanner_mode", "SPREAD")
        if mode not in ("TAKER_BUY", "TAKER_SELL"):
            return []

        # buy_grouped  = мерчанти ПРОДАЮТЬ USDT (side=1, user BUYS, low price)
        # sell_grouped = мерчанти КУПУЮТЬ USDT  (side=0, user SELLS, high price)
        if mode == "TAKER_BUY":
            source_grouped = buy_grouped   # user купує → ордери де мерчанти продають
            user_banks = set(user.get("buy_bank_codes") or user.get("bank_codes", []))
        else:
            source_grouped = sell_grouped  # user продає → ордери де мерчанти купують
            user_banks = set(user.get("sell_bank_codes") or user.get("bank_codes", []))

        capital = float(user.get("capital", 0))
        min_amount = float(user.get("min_amount", 0))
        mf = user.get("merchant_filters") or {}
        emf = user.get("exchange_merchant_filters") or {}

        seen_ids: set[str] = set()
        matched: list[Order] = []

        for bank_code, orders in source_grouped.items():
            if bank_code not in user_banks:
                continue
            for order in orders:
                if order.id in seen_ids:
                    continue
                if not (set(order.bank_codes) & user_banks):
                    continue
                order_max = float(order.max_limit)
                order_min = float(order.min_limit)
                if capital > 0 and order_min > capital:
                    continue
                if min_amount > 0 and order_max < min_amount:
                    continue

                # ── Фільтри TAKER_SELL (використовуємо обчислений min_sell_price) ──
                if mode == "TAKER_SELL":
                    strategy = user.get("taker_sell_price_strategy", "roi")
                    min_sell_price = float(user.get("taker_sell_min_price", 0))
                    price_to = float(user.get("taker_sell_price_to", 0))
                    t_amount = float(user.get("taker_sell_amount", 0))
                    t_speed = user.get("taker_sell_speed", "ANY")
                    order_price = float(order.price)

                    # ── Цінова стратегія ──────────────────────────────────
                    if strategy in ("roi", "min") and min_sell_price > 0:
                        if order_price < min_sell_price:
                            continue
                    elif strategy == "range":
                        if min_sell_price > 0 and order_price < min_sell_price:
                            continue
                        if price_to > 0 and order_price > price_to:
                            continue
                    elif strategy == "exact" and min_sell_price > 0:
                        if abs(order_price - min_sell_price) > 0.005:
                            continue
                    # "any" → без фільтру

                    # ── Ob'єм ─────────────────────────────────────────────
                    if t_amount > 0:
                        fiat_val = t_amount * order_price
                        if t_speed == "FAST":
                            if order_max < fiat_val or order_min > fiat_val:
                                continue
                        else:
                            if order_min > fiat_val:
                                continue

                # ── Фільтри TAKER_BUY ────────────────────────────────────────────────
                if mode == "TAKER_BUY":
                    strategy = user.get("taker_buy_price_strategy", "any")
                    price_to = float(user.get("taker_buy_max_price", 0))
                    price_from = float(user.get("taker_buy_price_from", 0))
                    t_limit_min = float(user.get("taker_buy_limit_min", 0))
                    t_limit_max = float(user.get("taker_buy_limit_max", 0))
                    t_speed = user.get("taker_buy_speed", "ANY")
                    order_price = float(order.price)

                    # ── Цінова стратегія ──────────────────────────────────
                    if strategy == "max" and price_to > 0:
                        if order_price > price_to:
                            continue
                    elif strategy == "range":
                        if price_from > 0 and order_price < price_from:
                            continue
                        if price_to > 0 and order_price > price_to:
                            continue
                    elif strategy == "exact" and price_to > 0:
                        if abs(order_price - price_to) > 0.005:  # допуск 0.5 копійки
                            continue
                    # "any" → без фільтру

                    # ── Фіатні ліміти ──────────────────────────────────────
                    if t_limit_min > 0 and order_max < t_limit_min:
                        continue
                    if t_limit_max > 0 and order_min > t_limit_max:
                        continue

                    # ── Швидкість (FAST = вимагаємо перекриття об'єму) ─────
                    if t_speed == "FAST":
                        t_amount = float(user.get("taker_buy_amount", 0))
                        if t_amount > 0:
                            fiat_needed = t_amount * order_price
                            if order_max < fiat_needed:
                                continue

                if not self._merchant_ok(order, mf, emf):
                    continue
                if "BLOCK" in (getattr(order, "risk_flag", "") or ""):
                    continue
                seen_ids.add(order.id)
                matched.append(order)

        # BUY → найнижча ціна спершу, SELL → найвища
        if mode == "TAKER_BUY":
            matched.sort(key=lambda o: float(o.price))
        else:
            matched.sort(key=lambda o: float(o.price), reverse=True)
        return matched[:20]

    @staticmethod
    def _merchant_ok(order: Order, mf: dict, emf: dict) -> bool:
        """Перевіряє фільтри мерчанта (global + per-exchange)."""
        ex_name = order.exchange
        ex_filters = emf.get(ex_name, {})
        min_orders = float(
            ex_filters.get("min_orders", 0) or mf.get("min_orders", 0)
            or MIN_ORDERS.get(ex_name, 0)
        )
        min_rate = float(
            ex_filters.get("min_rate", 0.0) or mf.get("min_rate", 0.0)
            or MIN_COMPLETION.get(ex_name, 0.0)
        )
        if min_orders > 0 and order.month_order_count < min_orders:
            return False
        if min_rate > 0 and order.finish_rate_pct < min_rate:
            return False
        return True