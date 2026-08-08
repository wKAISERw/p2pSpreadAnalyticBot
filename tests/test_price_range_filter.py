"""
Ціновий фільтр входу у спред-режимі.

Меню «💲 Фільтр ціни» в боті існувало давно і справно писало
price_range_json, але PriceRangeFilter не імпортувався ніде — тобто фільтр
зберігався, бот казав «збережено», а жоден ордер за ним не відсіювався.

Тепер він працює там, де мав сенс від початку: у спред-режимі, де власного
обмеження по ціні не було взагалі. Тейкер-режими його не використовують —
у них свої taker_*_price_strategy.
"""
from __future__ import annotations

import unittest
from decimal import Decimal
from unittest.mock import MagicMock

from core.engine.alert_dispatcher import AlertDispatcher
from exchanges.base import Order


def _order(price: str) -> Order:
    return Order(
        id=f"o-{price}", price=Decimal(price), available_amount=Decimal("100"),
        min_limit=Decimal("500"), max_limit=Decimal("10000"),
        merchant_id="m1", merchant_name="M", month_order_count=100,
        finish_rate_pct=99.0, exchange="Bybit", bank_codes=["43"],
    )


def _user(price_range: dict | None) -> dict:
    return {
        "user_id": 5150, "capital": 50000.0, "min_spread": 0.5,
        "bank_codes": ["43"], "buy_bank_codes": ["43"], "sell_bank_codes": ["43"],
        "merchant_filters": {},
        "scanner_mode": "SPREAD", "scanner_modes": ["SPREAD"],
        "price_range": price_range or {},
    }


def _opp(buy_price: str) -> dict:
    return {
        "buy_order": _order(buy_price), "sell_order": _order("44.00"),
        "actual_entry_uah": 1000.0, "net_spread_pct": 2.0,
        "buy_bank": "43", "sell_bank": "43",
        "buy_banks_fit": ["43"], "sell_banks_fit": ["43"],
    }


class TestPriceRangeIsActuallyApplied(unittest.TestCase):
    def setUp(self):
        self.dispatcher = AlertDispatcher(db=MagicMock(), notifier=MagicMock())

    def _wants(self, price_range, buy_price):
        ok, reason, _ = self.dispatcher._user_wants(_user(price_range), _opp(buy_price))
        return ok, reason

    def test_empty_config_lets_everything_through(self):
        ok, _ = self._wants({}, "41.00")
        self.assertTrue(ok)

    def test_max_mode_cuts_expensive_entries(self):
        cheap, _ = self._wants({"mode": "max", "value": 42.0}, "41.00")
        self.assertTrue(cheap)

        pricey, reason = self._wants({"mode": "max", "value": 42.0}, "43.00")
        self.assertFalse(pricey, "ордер дорожчий за поріг мав відсіятись")
        self.assertIn("Ціна входу", reason)

    def test_min_mode_cuts_cheap_entries(self):
        ok, _ = self._wants({"mode": "min", "value": 42.0}, "43.00")
        self.assertTrue(ok)
        self.assertFalse(self._wants({"mode": "min", "value": 42.0}, "41.00")[0])

    def test_range_mode_keeps_only_the_window(self):
        cfg = {"mode": "range", "min": 41.0, "max": 42.0}
        self.assertTrue(self._wants(cfg, "41.50")[0])
        self.assertFalse(self._wants(cfg, "40.50")[0])
        self.assertFalse(self._wants(cfg, "42.50")[0])

    def test_exact_mode_has_a_small_tolerance(self):
        cfg = {"mode": "exact", "value": 42.0}
        self.assertTrue(self._wants(cfg, "42.00")[0])
        self.assertTrue(self._wants(cfg, "42.01")[0], "допуск ±0.01 має працювати")
        self.assertFalse(self._wants(cfg, "42.50")[0])

    def test_reason_names_the_filter_so_it_is_debuggable(self):
        _, reason = self._wants({"mode": "max", "value": 40.0}, "43.00")
        # У причині має бути видно і що спрацювало, і які межі стояли.
        self.assertIn("40.00", reason)


if __name__ == "__main__":
    unittest.main()
