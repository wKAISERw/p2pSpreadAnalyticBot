# core/engine/taker_scanner.py
"""
TakerScanner — пайплайн для режимів TAKER_BUY / TAKER_SELL.

Замість пошуку зв'язок (buy+sell), фільтрує ОДНУ сторону ордерів
під персональні фільтри юзера (банки, ліміти, ціновий діапазон, мерчант-фільтри).
"""
from __future__ import annotations
import logging
from typing import Optional
from exchanges.base import Order
from config.defaults import MIN_ORDERS, MIN_COMPLETION
from core.storage.merchant_db import MerchantDB

logger = logging.getLogger("TakerScanner")


class TakerScanner:
    """
    Фільтрує ордери для taker-only режимів.
    TAKER_BUY: sell-ордери (хто продає USDT) — ти купуєш.
    TAKER_SELL: buy-ордери (хто купує USDT) — ти продаєш.
    """

    def __init__(self, db: Optional[MerchantDB] = None):
        self.db = db
        if db:
            from core.engine.card_matching_engine import CardMatchingEngine
            self.card_engine = CardMatchingEngine(db)
        else:
            self.card_engine = None

    async def find_orders_for_user(
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
        # Preload used subsidies for per-exchange filtering
        used_subs: dict[str, list[str]] = {}
        if self.db:
            uid = user.get("user_id", 0)
            used_subs = await self.db.get_used_subsidies(uid) if uid else {}

        # ── Smart Card Pre-filtering Setup ──
        def _normalize_bank(name: str) -> str:
            if not name:
                return ""
            name_low = str(name).strip().lower()
            name_map = {
                "43": "monobank", "mono": "monobank", "monobank": "monobank", "моно": "monobank", "монобанк": "monobank",
                "14": "privatbank", "pb": "privatbank", "privat": "privatbank", "privatbank": "privatbank", "приват": "privatbank", "приватбанк": "privatbank",
                "64": "pumb", "pumb": "pumb", "пумб": "pumb",
                "48": "a-bank", "abank": "a-bank", "a-bank": "a-bank", "абанк": "a-bank", "а-банк": "a-bank",
                "553": "izibank", "izi": "izibank", "izibank": "izibank", "ізі": "izibank", "ізібанк": "izibank",
                "328": "sense", "sense": "sense", "sensebank": "sense", "сенс": "sense", "сенсбанк": "sense"
            }
            return name_map.get(name_low, name_low)

        card_matching_active = False
        user_cards_by_bank = {}
        if self.card_engine and self.db:
            try:
                card_settings = await self.db.get_user_card_settings(user["user_id"])
                if card_settings and card_settings.get("card_module_mode") != "off" and card_settings.get("enable_in_single_modes"):
                    card_matching_active = True
                    raw_cards = await self.db.get_cards(user["user_id"], status="active")
                    import time
                    now_epoch = time.time()
                    for c in raw_cards:
                        if float(c.get("cooldown_until", 0.0)) > now_epoch:
                            continue
                        b_name = _normalize_bank(c.get("bank_name", ""))
                        if b_name not in user_cards_by_bank:
                            user_cards_by_bank[b_name] = []
                        user_cards_by_bank[b_name].append(c)
            except Exception as e:
                logger.warning("Error initializing card pre-filtering: %s", e)

        seen_ids: set[str] = set()
        candidates: list[Order] = []

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

                order_price = float(order.price)

                # ── In-Memory Card Pre-filtering ──
                if card_matching_active:
                    from bot.formatters import _bank_code_to_db
                    card_bank_db = _normalize_bank(_bank_code_to_db(bank_code))
                    if not card_bank_db or card_bank_db not in user_cards_by_bank:
                        continue
                    
                    # Calculate target UAH amount for card limit checks
                    target_uah = order_min
                    if mode == "TAKER_SELL":
                        t_amount = float(user.get("taker_sell_amount", 0))
                        if t_amount > 0:
                            target_uah = t_amount * order_price
                    elif mode == "TAKER_BUY":
                        t_amount = float(user.get("taker_buy_amount", 0))
                        if t_amount > 0:
                            target_uah = t_amount * order_price

                    # For TAKER_BUY we need enough total UAH balance across active cards
                    if mode == "TAKER_BUY":
                        total_bal = 0.0
                        for c in user_cards_by_bank[card_bank_db]:
                            card_id = c["id"]
                            pending_out = 0.0
                            async with self.db._db.execute(
                                """
                                SELECT SUM(l.amount) as total
                                FROM card_order_legs l
                                JOIN card_orders o ON l.order_id = o.id
                                WHERE l.card_id = ? AND l.leg_status = 'pending' AND o.direction = 'out'
                                """,
                                (card_id,)
                            ) as cur:
                                row = await cur.fetchone()
                                if row and row["total"]:
                                    pending_out = float(row["total"])
                            total_bal += max(0.0, float(c.get("balance", 0.0)) - pending_out)
                        if total_bal < target_uah:
                            continue

                # ── Фільтри TAKER_SELL (використовуємо обчислений min_sell_price) ──
                if mode == "TAKER_SELL":
                    strategy = user.get("taker_sell_price_strategy", "roi")
                    min_sell_price = float(user.get("taker_sell_min_price", 0))
                    price_to = float(user.get("taker_sell_price_to", 0))
                    t_amount = float(user.get("taker_sell_amount", 0))
                    t_speed = user.get("taker_sell_speed", "ANY")

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

                    # ── Об'єм ─────────────────────────────────────────────
                    t_amount = float(user.get("taker_buy_amount", 0))
                    if t_amount > 0:
                        fiat_needed = t_amount * order_price
                        if t_speed == "FAST":
                            if order_max < fiat_needed or order_min > fiat_needed:
                                continue
                        else:
                            if order_min > fiat_needed:
                                continue

                if not self._merchant_ok(order, mf, emf, used_subs):
                    continue
                if "BLOCK" in (getattr(order, "risk_flag", "") or ""):
                    continue
                
                seen_ids.add(order.id)
                candidates.append(order)

        # ── Final Card Matching & Filtering on candidates only (N+1 avoidance) ──
        matched: list[Order] = []
        for order in candidates:
            if card_matching_active:
                order_bank_code = order.bank_codes[0] if order.bank_codes else ""
                from bot.formatters import _bank_code_to_db
                card_bank_db = _bank_code_to_db(order_bank_code)
                
                target_uah = float(order.min_limit)
                order_price = float(order.price)
                if mode == "TAKER_SELL":
                    t_amount = float(user.get("taker_sell_amount", 0))
                    if t_amount > 0:
                        target_uah = t_amount * order_price
                elif mode == "TAKER_BUY":
                    t_amount = float(user.get("taker_buy_amount", 0))
                    if t_amount > 0:
                        target_uah = t_amount * order_price

                card_direction = "buy" if mode == "TAKER_BUY" else "sell"
                
                match_res = await self.card_engine.run(
                    user_id=user["user_id"],
                    bank=card_bank_db,
                    amount=target_uah,
                    direction=card_direction,
                    crypto_available=True
                )
                if match_res.status not in ("success", "needs_split"):
                    continue
            matched.append(order)

        # BUY → найнижча ціна спершу, SELL → найвища
        if mode == "TAKER_BUY":
            matched.sort(key=lambda o: float(o.price))
        else:
            matched.sort(key=lambda o: float(o.price), reverse=True)
        return matched[:20]

    @staticmethod
    def _merchant_ok(order: Order, mf: dict, emf: dict, used_subs: dict[str, list[str]] | None = None) -> bool:
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
        # Per-exchange subsidy filter: hide only used subsidies
        if getattr(order, "is_new_user_subsidy", False) and used_subs:
            ex_used = used_subs.get(ex_name, [])
            if "new_user" in ex_used:
                return False

        if min_orders > 0 and order.month_order_count < min_orders:
            return False
        if min_rate > 0 and order.finish_rate_pct < min_rate:
            return False

        # --- Reliability Filters ---
        # 1. Verification status ("all", "verified", "unverified")
        verified_filter = ex_filters.get("verified_filter") or mf.get("verified_filter") or "all"
        if verified_filter == "verified" and not order.is_verified:
            return False
        if verified_filter == "unverified" and order.is_verified:
            return False

        # 2. Account age (days) - only filter if exchange provides positive age > 0
        min_age = int(ex_filters.get("min_account_age_days") or mf.get("min_account_age_days") or 0)
        if min_age > 0:
            age = getattr(order, "account_age_days", 0)
            if age > 0 and age < min_age:
                return False

        # 3. Positive review rate (%)
        min_pos_rate = float(ex_filters.get("min_positive_rate") or mf.get("min_positive_rate") or 0.0)
        if min_pos_rate > 0.0:
            pos_rate = getattr(order, "positive_rate", 0.0)
            if pos_rate > 0.0:
                actual_pct = pos_rate * 100.0 if pos_rate <= 1.0 else pos_rate
                if actual_pct < min_pos_rate:
                    return False

        # 4. Max offline minutes
        max_offline = int(ex_filters.get("max_offline_mins") or mf.get("max_offline_mins") or 0)
        if max_offline > 0:
            last_online = getattr(order, "last_online_mins", None)
            if last_online is not None and last_online > max_offline:
                return False

        return True