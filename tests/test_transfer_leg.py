"""
Крок між ногами зв'язки: чим і за скільки везти USDT на біржу продажу.

Комісія мережі й раніше сиділа в `net_spread`, тобто на профіт впливала.
Але сам крок ніде не показувався: на екрані дві ноги стояли поруч так,
ніби куплені монети опиняються на другій біржі самі собою. А це рівно та
дія, яку людина робить руками, поки йде таймер угоди.

Найважливіший тут не розмір комісії, а випадок «спільної мережі немає»:
такий маршрут виглядав звичайним, і людину відправляло переказувати те,
що не переказується.
"""
from __future__ import annotations

import unittest

from scanner import _transfer_leg


class _Order:
    def __init__(self, exchange: str, price: float = 41.0):
        self.exchange = exchange
        self.price = price


class TestTransferLeg(unittest.TestCase):
    def test_same_exchange_needs_no_transfer(self):
        # Внутрішній переказ безкоштовний і миттєвий — показувати нема чого.
        self.assertIsNone(_transfer_leg({}, _Order("Bybit"), _Order("Bybit")))

    def test_cross_exchange_returns_cheapest_common_network(self):
        leg = _transfer_leg({}, _Order("Bybit"), _Order("Binance"))
        self.assertIsNotNone(leg)
        self.assertEqual(leg["fromExchange"], "Bybit")
        self.assertEqual(leg["toExchange"], "Binance")
        self.assertFalse(leg["unroutable"])
        self.assertGreater(leg["feeUsdt"], 0)
        self.assertNotEqual(leg["network"], "UNKNOWN")

    def test_fee_is_converted_by_buy_price(self):
        leg = _transfer_leg({}, _Order("Bybit", price=50.0), _Order("Binance"))
        self.assertAlmostEqual(leg["feeUah"], leg["feeUsdt"] * 50.0, places=6)

    def test_missing_common_network_is_marked_unroutable(self):
        # Рушій віддає ("UNKNOWN", 999.0) — і 999 USDT комісії тут не сума,
        # а спосіб сказати «маршруту немає». Показати її як ціну переказу
        # означало б збрехати про вартість замість того, щоб попередити.
        leg = _transfer_leg({}, _Order("Bybit"), _Order("НевідомаБіржа"))
        self.assertIsNotNone(leg)
        self.assertTrue(leg["unroutable"])
        self.assertEqual(leg["network"], "UNKNOWN")
        self.assertEqual(leg["feeUsdt"], 0.0)
        self.assertEqual(leg["feeUah"], 0.0)

    def test_missing_exchange_name_is_not_a_crash(self):
        self.assertIsNone(_transfer_leg({}, _Order(""), _Order("Binance")))
        self.assertIsNone(_transfer_leg({}, _Order("Bybit"), _Order("")))


class TestNetworkOptions(unittest.TestCase):
    """Не лише найдешевша: возять тією мережею, якою звикли."""

    def test_all_common_networks_are_offered(self):
        leg = _transfer_leg({}, _Order("Bybit"), _Order("Binance"))
        names = [o["network"] for o in leg["options"]]

        # TRC20 дорожчий за TON у сто разів, але саме ним найчастіше й
        # возять — сховати його означало б вирішити за людину.
        self.assertIn("TRC20", names)
        self.assertIn(leg["network"], names)

    def test_options_are_sorted_by_price(self):
        leg = _transfer_leg({}, _Order("Bybit"), _Order("Binance"))
        fees = [o["feeUsdt"] for o in leg["options"]]
        self.assertEqual(fees, sorted(fees))
        # Перша опція — та сама, за якою сканер порахував спред.
        self.assertEqual(leg["options"][0]["feeUsdt"], leg["feeUsdt"])

    def test_unknown_is_never_offered_as_a_choice(self):
        # «UNKNOWN» — це відсутність маршруту, а не мережа, і потрапити в
        # список для вибору вона не має.
        leg = _transfer_leg({}, _Order("Bybit"), _Order("НевідомаБіржа"))
        self.assertEqual(leg["options"], [])

    def test_option_fee_in_uah_follows_buy_price(self):
        leg = _transfer_leg({}, _Order("Bybit", price=50.0), _Order("Binance"))
        for opt in leg["options"]:
            with self.subTest(network=opt["network"]):
                self.assertAlmostEqual(opt["feeUah"], opt["feeUsdt"] * 50.0, places=6)


if __name__ == "__main__":
    unittest.main()
