# tests/test_quiet_logs.py
"""
Лог, який можна читати.

Скарга з бою: «досі спам в логах є». Дві причини, обидві однієї природи —
беззастережний лог у коді, який виконується на кожному циклі, а цикл іде
раз на три секунди:

    🎯 Аномалії (mad, buy): відкинуто 5 із 204 — ринок ≈ 45.44 ₴, допуск ±1.36 ₴
    🎯 Аномалії (mad, buy): відкинуто 5 із 204 — ринок ≈ 45.44 ₴, допуск ±1.36 ₴
    🐌 CryptoBot фетч зайняв 1.10s!
    🐌 CryptoBot фетч зайняв 1.32s!

Ті самі числа, бо за три секунди ринок не рухається, а CryptoBot стабільно
повільний. Це не косметика: лог читають, коли щось зламалось, і сто
однакових рядків на хвилину ховають той єдиний, заради якого його відкрили.

Обидва місця тепер дедуплять по сигнатурі — той самий прийом, що вже
працює для підозри на бота (`_should_log_behavior_alert`).
"""
from __future__ import annotations

import logging
import unittest

from exchanges.base import Order
from filters.anomaly_filter import LOG_REPEAT_SEC, AnomalyFilter


def _order(price: float) -> Order:
    return Order(
        id=str(price), price=price, available_amount=100.0,
        min_limit=1000.0, max_limit=50000.0,
        merchant_id=f"m{price}", merchant_name="M",
        month_order_count=100, finish_rate_pct=99.0, exchange="Binance",
    )


def _book(base: float) -> list[Order]:
    """Щільний стакан плюс одна явна аномалія."""
    return [_order(base + i * 0.01) for i in range(20)] + [_order(base - 5.0)]


class TestAnomalyFilterIsQuiet(unittest.TestCase):
    def setUp(self):
        AnomalyFilter._log_cache.clear()

    def _run(self, book, times=1, side="buy", label=""):
        with self.assertLogs("AnomalyFilter", level="DEBUG") as captured:
            for _ in range(times):
                # Новий екземпляр щоразу — саме так робить сканер, тож
                # пам'ять на екземплярі не дедуплювала б нічого.
                AnomalyFilter().analyze(book, side, label=label)
        return [r for r in captured.records if r.levelno == logging.INFO]

    def test_drifting_book_size_does_not_break_the_dedup(self):
        """
        Найпідступніший випадок: розмір стакану пливе на один ордер.

        Перша спроба дедупу ключувалась розміром стакану, і 204 → 205 → 204
        читалось як три різні картини — двадцять циклів давали двадцять
        рядків, просто з іншим числом. Дедуп, який не дедуплює, гірший за
        його відсутність: він створює враження, що проблему вирішено.
        """
        with self.assertLogs("AnomalyFilter", level="DEBUG") as captured:
            for cycle in range(20):
                AnomalyFilter().analyze(_book(45.4)[: 21 - cycle % 2], "buy", label="mono")
        infos = [r for r in captured.records if r.levelno == logging.INFO]
        self.assertEqual(len(infos), 1)

    def test_different_banks_are_logged_apart(self):
        # Стакани різних банків — різні ринки з різними цінами. Один не має
        # заглушувати інший, і з рядка має бути видно, про який ідеться.
        mono = self._run(_book(45.4), label="mono")
        privat = self._run(_book(46.9), label="privat")
        self.assertEqual(len(mono), 1)
        self.assertEqual(len(privat), 1)
        self.assertIn("mono", mono[0].getMessage())
        self.assertIn("privat", privat[0].getMessage())

    def test_unchanged_picture_is_logged_once(self):
        infos = self._run(_book(45.4), times=10)
        self.assertEqual(len(infos), 1)

    def test_a_real_change_speaks_immediately(self):
        self._run(_book(45.4), times=3)
        infos = self._run(_book(46.4), times=1)
        self.assertEqual(len(infos), 1)

    def test_the_first_line_still_carries_the_numbers(self):
        infos = self._run(_book(45.4))
        self.assertIn("ринок", infos[0].getMessage())
        self.assertIn("допуск", infos[0].getMessage())

    def test_repeats_are_kept_at_debug_not_dropped(self):
        # Тиша в INFO не означає, що інформації немає: у DEBUG вона є, і
        # при діагностиці її можна ввімкнути.
        with self.assertLogs("AnomalyFilter", level="DEBUG") as captured:
            for _ in range(3):
                AnomalyFilter().analyze(_book(45.4), "buy")
        debug = [r for r in captured.records if r.levelno == logging.DEBUG]
        self.assertTrue(any("без змін" in r.getMessage() for r in debug))

    def test_buy_and_sell_are_counted_apart(self):
        # Один бік не має заглушувати інший: це різні картини ринку.
        self._run(_book(45.4), side="buy")
        infos = self._run([_order(45.4 + i * 0.01) for i in range(20)] + [_order(50.4)],
                          side="sell")
        self.assertEqual(len(infos), 1)

    def test_state_survives_between_instances(self):
        self.assertIsNot(AnomalyFilter()._log_cache, None)
        self.assertIs(AnomalyFilter()._log_cache, AnomalyFilter()._log_cache)

    def test_repeat_window_is_not_forever(self):
        # Рядок, який не з'являвся годину, читається як «фільтр вимкнувся».
        self.assertLessEqual(LOG_REPEAT_SEC, 600.0)
        self.assertGreaterEqual(LOG_REPEAT_SEC, 60.0)


class TestSlowFetchWarningIsQuiet(unittest.TestCase):
    def setUp(self):
        from scanner import _slow_fetch_log

        _slow_fetch_log.clear()

    def test_stable_slowness_warns_once(self):
        from scanner import _should_warn_slow

        results = [_should_warn_slow("CryptoBot", d) for d in (1.1, 1.2, 1.15, 1.3, 1.05)]
        self.assertEqual(results.count(True), 1)
        self.assertTrue(results[0])

    def test_getting_worse_is_news(self):
        from scanner import _should_warn_slow

        _should_warn_slow("CryptoBot", 1.1)
        self.assertTrue(_should_warn_slow("CryptoBot", 2.4))

    def test_getting_better_is_not_news(self):
        from scanner import _should_warn_slow

        _should_warn_slow("CryptoBot", 2.4)
        self.assertFalse(_should_warn_slow("CryptoBot", 1.2))

    def test_each_exchange_is_tracked_apart(self):
        from scanner import _should_warn_slow

        _should_warn_slow("CryptoBot", 1.1)
        self.assertTrue(_should_warn_slow("OKX", 1.1))


if __name__ == "__main__":
    unittest.main()
