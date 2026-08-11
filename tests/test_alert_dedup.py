# tests/test_alert_dedup.py
"""
Той самий спред не має прилітати щоциклу.

Дедуп ключувався ціною — а в P2P ціна тікає щосекунди. Мерчант переставив
45.44 → 45.45, ключ став іншим, і для дедупу це «новий спред», хоча пара
мерчантів та сама. У чат летіло

    CHRØME HEARTS → Saint_Frank  3.52%
    CHRØME HEARTS → Saint_Frank  3.49%
    CHRØME HEARTS → Saint_Frank  3.54%

Фільтр стабільності не рятував: він теж ключується ціною
(`core/engine/stability.py:29`), тож нова ціна просто починала лічити свої
два хіти заново.

Тепер тотожність — пара мерчантів, а ціна стала приводом, а не ключем.
"""
from __future__ import annotations

import unittest
from types import SimpleNamespace

from core.engine.alert_dedup import DEFAULT_IMPROVEMENT_PP, AlertGate, pair_key
from core.utils.cache import TTLCache


def _opp(spread: float, price: str = "45.44",
         buy_id: str = "b1", sell_id: str = "s1") -> dict:
    return {
        "buy_order": SimpleNamespace(exchange="Binance", merchant_id=buy_id, price=price),
        "sell_order": SimpleNamespace(exchange="OKX", merchant_id=sell_id, price=price),
        "route_type": "CROSS",
        "net_spread_pct": spread,
    }


class TestPairIdentity(unittest.TestCase):
    def test_price_is_not_part_of_identity(self):
        self.assertEqual(pair_key(_opp(3.5, "45.44")), pair_key(_opp(3.5, "45.45")))

    def test_different_merchants_are_different_pairs(self):
        self.assertNotEqual(pair_key(_opp(3.5)), pair_key(_opp(3.5, sell_id="s2")))

    def test_route_type_matters(self):
        cross = _opp(3.5)
        intra = {**_opp(3.5), "route_type": "INTRA"}
        self.assertNotEqual(pair_key(cross), pair_key(intra))


class TestGate(unittest.TestCase):
    def setUp(self):
        self.gate = AlertGate(cache=TTLCache(ttl_seconds=600.0, max_size=100))

    def test_first_time_is_always_shown(self):
        allowed, _ = self.gate.allow(_opp(2.0))
        self.assertTrue(allowed)

    def test_price_tick_does_not_re_alert(self):
        self.gate.allow(_opp(3.52, "45.44"))
        for spread, price in ((3.49, "45.45"), (3.54, "45.43"), (3.51, "45.46")):
            with self.subTest(spread=spread):
                allowed, reason = self.gate.allow(_opp(spread, price))
                self.assertFalse(allowed, f"дублікат пройшов: {reason}")

    def test_material_improvement_is_shown_again(self):
        self.gate.allow(_opp(2.0))
        allowed, reason = self.gate.allow(_opp(2.0 + DEFAULT_IMPROVEMENT_PP))
        self.assertTrue(allowed)
        self.assertIn("виріс", reason)

    def test_improvement_is_measured_from_the_last_shown(self):
        # 2.0 показали → 2.4 ні → 2.6 теж ні (від 2.0 це лише +0.6? ні, +0.6 ≥ 0.5)
        self.gate.allow(_opp(2.0))
        self.assertFalse(self.gate.allow(_opp(2.4))[0])
        self.assertTrue(self.gate.allow(_opp(2.6))[0])
        # тепер база 2.6, і 2.9 замало
        self.assertFalse(self.gate.allow(_opp(2.9))[0])

    def test_a_drop_lowers_the_bar(self):
        # Без цього після падіння 3.4 → 2.0 ми б чекали 3.9, щоб сказати
        # про 2.0 — тобто мовчали б, поки спред не перевищить старий пік.
        self.gate.allow(_opp(3.4))
        self.assertFalse(self.gate.allow(_opp(2.0))[0])
        self.assertTrue(self.gate.allow(_opp(2.5))[0])

    def test_other_pairs_are_independent(self):
        self.gate.allow(_opp(3.0))
        allowed, _ = self.gate.allow(_opp(3.0, sell_id="s2"))
        self.assertTrue(allowed)

    def test_broken_spread_value_does_not_crash(self):
        opp = _opp(0.0)
        opp["net_spread_pct"] = None
        self.assertTrue(self.gate.allow(opp)[0])


if __name__ == "__main__":
    unittest.main()
