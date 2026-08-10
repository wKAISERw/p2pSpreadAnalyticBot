"""
Вердикт на сайті мусить дозрівати так само, як у Telegram.

У чаті це працює давно: `llm_worker` і `review_fetcher`, закінчивши аналіз,
кличуть `redraw_alerts_for_merchant()`, і надіслане повідомлення
редагується — «🔍 AI аналізує…» перетворюється на конкретне рішення.

На сайті ордер лишався з тим вердиктом, який був на момент циклу сканера.
Якщо ядро зупинене або мерчант зник зі стакану, «AI аналізує» висіло
назавжди, хоча вердикт давно лежав у базі. Найгірше — так виглядав і
відсіяний BLOCK: людина бачила «ще думаємо» там, де рішення вже було
«не торгувати».

Тести фіксують обидва боки: готове рішення підставляється, а те, що ще
не дозріло або зіпсувалось по дорозі, не перетворюється на вигадку.
"""
from __future__ import annotations

import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from api.verdict_refresh import is_pending, refresh_flags, refresh_opportunity_flags
from core.storage.merchant_db import MerchantDB


def _order(risk_flag: str, merchant_id: str = "m1", terms: str = "умови") -> dict:
    return {
        "exchange": "Bybit",
        "merchantId": merchant_id,
        "merchantName": "Merchant",
        "riskFlag": risk_flag,
        "tradeTerms": terms,
    }


class TestPendingDetection(unittest.TestCase):
    def test_only_unfinished_states_are_pending(self):
        for flag in ("PENDING", "NEEDS_LLM:BADREVIEWS:текст", "LLM_PENDING:COOLDOWN:C42",
                     "RECHECKING"):
            with self.subTest(flag=flag):
                self.assertTrue(is_pending(flag))

    def test_finished_verdicts_are_left_alone(self):
        # Готове рішення перепитувати нема сенсу: воно вже найсвіжіше.
        for flag in ("OK", "BLOCK:MIDDLEMAN:текст", "LLM_SUSPICIOUS:ANONYMOUS:текст",
                     "UNKNOWN:REVIEWS:NO_SESSION", "", None):
            with self.subTest(flag=flag):
                self.assertFalse(is_pending(flag))


class TestRefresh(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self._tmp = TemporaryDirectory()
        self.db = MerchantDB(db_path=Path(self._tmp.name) / "t.db")
        await self.db.start()

    async def asyncTearDown(self):
        await self.db.stop()
        self._tmp.cleanup()

    async def _save(self, verdict: str, merchant_id: str = "m1",
                    terms: str = "умови", reason: str = "пояснення"):
        await self.db.save_verdict(
            exchange="Bybit", merchant_id=merchant_id, merchant_name="Merchant",
            trade_terms=terms, verdict=verdict, risk_type="MIDDLEMAN", reason=reason,
        )

    async def test_ripe_verdict_replaces_pending(self):
        await self._save("BLOCK")
        orders = [_order("PENDING")]

        changed = await refresh_flags(self.db, orders)
        self.assertEqual(changed, 1)
        self.assertIn("BLOCK", orders[0]["riskFlag"])
        # Пояснення LLM мусить долетіти разом із вердиктом — заради нього
        # все це й робиться.
        self.assertIn("пояснення", orders[0]["riskFlag"])

    async def test_missing_verdict_leaves_pending_as_is(self):
        # Чесне «ще думаємо» краще за вигаданий вердикт.
        orders = [_order("PENDING")]
        self.assertEqual(await refresh_flags(self.db, orders), 0)
        self.assertEqual(orders[0]["riskFlag"], "PENDING")

    async def test_finished_flag_is_not_touched(self):
        await self._save("BLOCK")
        orders = [_order("LLM_SUSPICIOUS:ANONYMOUS:старий текст")]

        self.assertEqual(await refresh_flags(self.db, orders), 0)
        self.assertEqual(orders[0]["riskFlag"], "LLM_SUSPICIOUS:ANONYMOUS:старий текст")

    async def test_changed_terms_do_not_reuse_old_verdict(self):
        # Умови змінились — кеш недійсний, і підставляти старе рішення не
        # можна: саме на зміні умов мерчанти й міняють правила гри.
        await self._save("OK", terms="старі умови")
        orders = [_order("PENDING", terms="зовсім інші умови про третіх осіб")]

        self.assertEqual(await refresh_flags(self.db, orders), 0)
        self.assertEqual(orders[0]["riskFlag"], "PENDING")

    async def test_one_merchant_in_many_orders_is_queried_once(self):
        await self._save("BLOCK")
        orders = [_order("PENDING"), _order("PENDING"), _order("PENDING")]

        changed = await refresh_flags(self.db, orders)
        self.assertEqual(changed, 3)
        self.assertEqual(len({o["riskFlag"] for o in orders}), 1)

    async def test_broken_order_does_not_crash_the_feed(self):
        orders = [{"riskFlag": "PENDING"}, _order("PENDING")]
        await refresh_flags(self.db, orders)  # без exchange/merchantId — просто пропуск
        self.assertEqual(orders[0]["riskFlag"], "PENDING")

    async def test_blind_terms_drop_the_cached_summary(self):
        # Вердикт живе в кеші до 12 годин і не перераховується, поки хеш
        # умов не змінився. Але коли умов НЕ ВИДНО, хеш порожній і сталий —
        # тобто стара вижимка «Умови не вказані» переживе будь-яку кількість
        # циклів і виглядатиме як свіжий факт про мерчанта.
        await self.db.save_verdict(
            exchange="Bybit", merchant_id="m1", merchant_name="Merchant",
            trade_terms="", verdict="OK", reason="статистика в нормі",
            terms_summary="Умови не вказані.",
        )

        blind = _order("OK")
        blind["termsStatus"] = "NO_SESSION"
        await refresh_flags(self.db, [blind])

        self.assertEqual(blind["ai"]["termsSummary"], "")
        # Решта висновку лишається: він про статистику, а не про умови.
        self.assertIn("статистика", blind["ai"]["reason"])

    async def test_real_empty_terms_keep_the_summary(self):
        # Мерчант справді нічого не написав — вижимка про це чесна.
        await self.db.save_verdict(
            exchange="Bybit", merchant_id="m2", merchant_name="Merchant",
            trade_terms="", verdict="OK", reason="ок",
            terms_summary="Умови не вказані.",
        )

        order = _order("OK", merchant_id="m2")
        order["termsStatus"] = "EMPTY"
        await refresh_flags(self.db, [order])

        self.assertEqual(order["ai"]["termsSummary"], "Умови не вказані.")

    async def test_spread_legs_are_refreshed_both(self):
        await self._save("BLOCK", merchant_id="buy-m")
        await self._save("BLOCK", merchant_id="sell-m")

        opps = [{
            "id": "x",
            "buyOrder": _order("PENDING", merchant_id="buy-m"),
            "sellOrder": _order("NEEDS_LLM:BADREVIEWS:чекаємо", merchant_id="sell-m"),
        }]

        changed = await refresh_opportunity_flags(self.db, opps)
        self.assertEqual(changed, 2)
        self.assertIn("BLOCK", opps[0]["buyOrder"]["riskFlag"])
        self.assertIn("BLOCK", opps[0]["sellOrder"]["riskFlag"])


if __name__ == "__main__":
    unittest.main()
