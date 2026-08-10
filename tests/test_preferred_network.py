"""
Спред рахується мережею, якою людина возить, а не найдешевшою на папері.

Матчер один на всіх користувачів, тож бере найдешевшу спільну мережу — і
це правильний дефолт. Але TRC20 коштує 1 ₮ проти 0.01 у TON, і хто возить
лише ним, отримував алерти, які в його реальності порога не проходять:
на екрані 0.6%, а після переказу 0.4%.

Найважливіше тут — чого робити НЕ можна. Підставити ціну мережі, якої між
цими двома біржами немає, означає порахувати спред за маршрутом, яким
людина не проїде. Тому невідома пара мережа-біржі лишає базовий розрахунок.
"""
from __future__ import annotations

import unittest

from core.engine.alert_dispatcher import network_penalty_uah, personal_net_spread


class _Order:
    def __init__(self, exchange: str, price: float = 41.0):
        self.exchange = exchange
        self.price = price


def _opp(buy_ex: str, sell_ex: str, network: str, fee: float,
         spread: float = 3.0, entry: float = 10_000.0) -> dict:
    return {
        "buy_order": _Order(buy_ex),
        "sell_order": _Order(sell_ex),
        "network": {"name": network, "fee_usdt": fee},
        "net_spread_pct": spread,
        "actual_entry_uah": entry,
    }


class TestPenalty(unittest.TestCase):
    def test_no_preference_costs_nothing(self):
        opp = _opp("Bybit", "Binance", "SOL", 0.01)
        self.assertEqual(network_penalty_uah({}, opp), 0.0)
        self.assertEqual(network_penalty_uah({"preferred_network": ""}, opp), 0.0)

    def test_same_network_costs_nothing(self):
        opp = _opp("Bybit", "Binance", "SOL", 0.01)
        self.assertEqual(network_penalty_uah({"preferred_network": "SOL"}, opp), 0.0)

    def test_dearer_network_costs_the_difference(self):
        # TRC20 (1.00) проти SOL (0.01) = 0.99 ₮ різниці, у гривні за курсом
        # купівлі.
        opp = _opp("Bybit", "Binance", "SOL", 0.01)
        penalty = network_penalty_uah({"preferred_network": "TRC20"}, opp)
        self.assertAlmostEqual(penalty, 0.99 * 41.0, places=4)

    def test_cheaper_preference_is_not_a_bonus(self):
        # Матчер уже взяв найдешевшу; «дешевша за найдешевшу» означає
        # помилку в даних, і дарувати за неї профіт не можна.
        opp = _opp("Bybit", "Binance", "TRC20", 1.0)
        self.assertEqual(network_penalty_uah({"preferred_network": "SOL"}, opp), 0.0)

    def test_network_unavailable_between_these_exchanges_is_ignored(self):
        # Wallet уміє лише TON. Порахувати тут TRC20 означало б оцінити
        # маршрут, якого не існує.
        opp = _opp("Wallet", "CryptoBot", "TON", 0.01)
        self.assertEqual(network_penalty_uah({"preferred_network": "TRC20"}, opp), 0.0)

    def test_same_exchange_has_no_transfer(self):
        opp = _opp("Bybit", "Bybit", "INTRA", 0.0)
        self.assertEqual(network_penalty_uah({"preferred_network": "TRC20"}, opp), 0.0)

    def test_unroutable_pair_is_ignored(self):
        opp = _opp("Wallet", "MEXC", "UNKNOWN", 999.0)
        self.assertEqual(network_penalty_uah({"preferred_network": "TRC20"}, opp), 0.0)


class TestPersonalSpread(unittest.TestCase):
    def test_spread_drops_by_the_penalty_share(self):
        opp = _opp("Bybit", "Binance", "SOL", 0.01, spread=3.0, entry=10_000.0)
        spread = personal_net_spread({"preferred_network": "TRC20"}, opp)

        expected = 3.0 - (0.99 * 41.0 / 10_000.0) * 100.0
        self.assertAlmostEqual(spread, expected, places=6)
        self.assertLess(spread, 3.0)

    def test_without_preference_spread_is_untouched(self):
        opp = _opp("Bybit", "Binance", "SOL", 0.01, spread=3.0)
        self.assertEqual(personal_net_spread({}, opp), 3.0)

    def test_zero_entry_does_not_divide(self):
        opp = _opp("Bybit", "Binance", "SOL", 0.01, spread=3.0, entry=0.0)
        self.assertEqual(personal_net_spread({"preferred_network": "TRC20"}, opp), 3.0)

    def test_missing_network_info_falls_back_to_base(self):
        # Старі зв'язки з кешу можуть не мати поля network — вони не
        # повинні ні падати, ні втрачати спред.
        opp = _opp("Bybit", "Binance", "SOL", 0.01, spread=3.0)
        del opp["network"]
        self.assertEqual(personal_net_spread({"preferred_network": "TRC20"}, opp), 3.0)


if __name__ == "__main__":
    unittest.main()
