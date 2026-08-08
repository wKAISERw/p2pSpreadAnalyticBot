"""
Фільтр цінових аномалій.

Перша версія модуля була написана, але ніде не імпортувалась — і на те була
причина: фільтр був симетричним. Він однаково відкидав і підозріло вигідні,
і підозріло невигідні ціни, тобто міг викинути найкращу пропозицію в
стакані — рівно те, за чим сканер існує.

Тепер фільтр односторонній: аномалією вважається лише «занадто добре, щоб
бути правдою», бо саме там ховаються заманухи й помилки в ціні.
"""
from __future__ import annotations

import unittest
from decimal import Decimal

from filters.anomaly_filter import MIN_SAMPLE, AnomalyFilter
from exchanges.base import Order


def _order(price: str, name: str = "M") -> Order:
    return Order(
        id=f"o-{price}", price=Decimal(price), available_amount=Decimal("100"),
        min_limit=Decimal("500"), max_limit=Decimal("10000"),
        merchant_id=f"m-{price}", merchant_name=name, month_order_count=100,
        finish_rate_pct=99.0, exchange="Bybit", bank_codes=["43"],
    )


def _market(*prices: str) -> list[Order]:
    return [_order(p) for p in prices]


class TestOneSidedness(unittest.TestCase):
    """Головне: фільтр не має різати вигідні ціни."""

    def setUp(self):
        self.f = AnomalyFilter(method="mad", multiplier=2.0)

    def test_buy_side_drops_suspiciously_cheap(self):
        # Ти купуєш: продавець із ціною значно нижчою за ринок підозрілий.
        orders = _market("41.00", "41.05", "41.10", "41.02", "41.08", "30.00")
        result = self.f.analyze(orders, "buy")

        self.assertEqual(len(result.rejected), 1)
        self.assertEqual(float(result.rejected[0].price), 30.00)

    def test_buy_side_keeps_expensive_orders(self):
        # Дорогий продавець — не аномалія, а просто погана ціна: її відсіють
        # звичайні пороги, а не цей фільтр.
        orders = _market("41.00", "41.05", "41.10", "41.02", "41.08", "55.00")
        result = self.f.analyze(orders, "buy")

        self.assertEqual(result.rejected, [], "дорогий ордер не має вважатись аномалією")

    def test_sell_side_drops_suspiciously_expensive(self):
        # Ти продаєш: покупець із ціною значно вищою за ринок підозрілий.
        orders = _market("41.00", "41.05", "41.10", "41.02", "41.08", "55.00")
        result = self.f.analyze(orders, "sell")

        self.assertEqual(len(result.rejected), 1)
        self.assertEqual(float(result.rejected[0].price), 55.00)

    def test_sell_side_keeps_cheap_orders(self):
        orders = _market("41.00", "41.05", "41.10", "41.02", "41.08", "30.00")
        result = self.f.analyze(orders, "sell")
        self.assertEqual(result.rejected, [])

    def test_best_normal_price_always_survives(self):
        # Найкраща ціна в межах ринку — те, за чим ми й прийшли.
        orders = _market("41.00", "41.05", "41.10", "41.02", "41.08", "40.90")
        kept = self.f.filter_orders(orders, "buy")
        self.assertIn(40.90, [float(o.price) for o in kept])


class TestSafetyRails(unittest.TestCase):
    def test_small_sample_is_left_untouched(self):
        # На чотирьох ордерах медіана — це шум, різати за нею небезпечно.
        orders = _market("41.00", "30.00", "41.10", "41.05")
        self.assertLess(len(orders), MIN_SAMPLE)

        result = AnomalyFilter().analyze(orders, "buy")
        self.assertEqual(len(result.kept), len(orders))
        self.assertFalse(result.is_active)
        self.assertIn("замала", result.describe())

    def test_identical_prices_produce_no_rejections(self):
        # Нульовий розкид: відхилятись нема від чого, ділення на нуль теж.
        orders = _market("41.00", "41.00", "41.00", "41.00", "41.00", "41.00")
        result = AnomalyFilter().analyze(orders, "buy")
        self.assertEqual(result.rejected, [])
        self.assertEqual(len(result.kept), 6)

    def test_unknown_method_falls_back_instead_of_crashing(self):
        f = AnomalyFilter(method="нонсенс", multiplier=2.0)
        self.assertEqual(f.method, "mad")
        # І далі працює як звичайний mad.
        result = f.analyze(_market("41.00", "41.05", "41.10", "41.02", "41.08", "30.00"), "buy")
        self.assertEqual(len(result.rejected), 1)


class TestMultiplierMeansTheSameEverywhere(unittest.TestCase):
    """
    У першій версії multiplier означав різні речі: для median це були
    відсотки (2.5 = 2.5%), для mad і mean — множник розкиду. Одне число з
    трьома значеннями неможливо налаштувати свідомо.
    """

    ORDERS = _market("41.00", "41.05", "41.10", "41.02", "41.08", "35.00")

    def test_bigger_multiplier_is_always_more_permissive(self):
        for method in ("mad", "median", "mean"):
            with self.subTest(method=method):
                strict = AnomalyFilter(method=method, multiplier=1.0).analyze(self.ORDERS, "buy")
                loose = AnomalyFilter(method=method, multiplier=50.0).analyze(self.ORDERS, "buy")

                self.assertGreaterEqual(
                    len(loose.kept), len(strict.kept),
                    f"{method}: більший multiplier має пропускати не менше",
                )

    def test_absurd_multiplier_disables_the_filter(self):
        # Окремо від монотонності: множник у різних методах масштабується
        # по-різному (MAD на щільному стакані — копійки, stdev — гривні),
        # тому «вимкнути фільтр» вимагає завідомо великого числа.
        for method in ("mad", "median", "mean"):
            with self.subTest(method=method):
                result = AnomalyFilter(method=method, multiplier=1000.0).analyze(
                    self.ORDERS, "buy"
                )
                self.assertEqual(result.rejected, [])


class TestExplanations(unittest.TestCase):
    def test_rejection_says_what_and_why(self):
        result = AnomalyFilter(method="mad", multiplier=2.0).analyze(
            _market("41.00", "41.05", "41.10", "41.02", "41.08", "30.00"), "buy"
        )
        reason = result.rejected[0].reason

        # Причина має бути читабельною людині, а не дампом чисел.
        self.assertIn("нижча за ринок", reason)
        self.assertIn("30.00", reason)

    def test_describe_names_the_method_in_words(self):
        result = AnomalyFilter(method="mad", multiplier=2.0).analyze(
            _market("41.00", "41.05", "41.10", "41.02", "41.08", "30.00"), "buy"
        )
        self.assertIn("медіанне відхилення", result.describe())


if __name__ == "__main__":
    unittest.main()
