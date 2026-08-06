"""
Регресії по продуктивності та по налаштуваннях, які движок ігнорував.
"""
from __future__ import annotations

import datetime
import time
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from core.storage.merchant_db import MerchantDB


class _DBCase(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self._tmp = TemporaryDirectory()
        self.db = MerchantDB(db_path=Path(self._tmp.name) / "t.db")
        await self.db.start()

    async def asyncTearDown(self):
        await self.db.stop()
        self._tmp.cleanup()

    async def _count(self, sql: str, *args) -> int:
        async with self.db._db.execute(sql, args) as cur:
            row = await cur.fetchone()
            return int(row[0])


class TestReviewSnapshotDedup(_DBCase):
    """
    Дефект: save_review_snapshot робив сліпий INSERT+commit на кожному аналізі.
    На бойовій базі це дало 67 209 рядків на 434 мерчантів (рекорд — 8 447
    ідентичних снапшотів одного мерчанта), плюс fsync на спільному з'єднанні.
    """

    async def test_identical_snapshots_are_not_written(self):
        for _ in range(20):
            await self.db.save_review_snapshot("Bybit", "m1", 100, 5, 4.7)

        n = await self._count("SELECT COUNT(*) FROM merchant_review_history")
        self.assertEqual(n, 1, "однакові снапшоти мали злитись в один рядок")

    async def test_changed_snapshot_is_written(self):
        await self.db.save_review_snapshot("Bybit", "m1", 100, 5, 4.7)
        await self.db.save_review_snapshot("Bybit", "m1", 100, 6, 5.6)   # neg змінився
        await self.db.save_review_snapshot("Bybit", "m1", 101, 6, 5.6)   # pos змінився

        n = await self._count("SELECT COUNT(*) FROM merchant_review_history")
        self.assertEqual(n, 3)

    async def test_trend_still_works_after_dedup(self):
        await self.db.save_review_snapshot("Bybit", "m1", 100, 1, 1.0)
        await self.db.save_review_snapshot("Bybit", "m1", 100, 20, 16.6)

        trend = await self.db.get_review_trend("Bybit", "m1", days=7)
        self.assertEqual(trend["trend"], "worsening")
        self.assertGreater(trend["delta"], 3.0)

    async def test_merchants_do_not_shadow_each_other(self):
        await self.db.save_review_snapshot("Bybit", "m1", 10, 1, 9.0)
        await self.db.save_review_snapshot("Bybit", "m2", 10, 1, 9.0)

        n = await self._count("SELECT COUNT(*) FROM merchant_review_history")
        self.assertEqual(n, 2, "дедуп має бути per-merchant, а не глобальний")


class TestSchemaTuning(_DBCase):
    """Індекси на гарячих шляхах + прибраний дублікат PK."""

    async def _indexes_on(self, table: str) -> set[str]:
        async with self.db._db.execute(
            "SELECT name FROM sqlite_master WHERE type='index' AND tbl_name=?", (table,)
        ) as cur:
            return {r[0] for r in await cur.fetchall()}

    async def test_hot_path_indexes_exist(self):
        self.assertIn("idx_trades_owner_status", await self._indexes_on("active_trades"))
        self.assertIn("idx_trades_session", await self._indexes_on("active_trades"))
        self.assertIn("idx_legs_card_status", await self._indexes_on("card_order_legs"))
        self.assertIn("idx_proposals_user_ts", await self._indexes_on("scanner_proposals"))

    async def test_redundant_index_dropped(self):
        # idx_verdict_lookup дублював PRIMARY KEY (exchange, merchant_id)
        self.assertNotIn("idx_verdict_lookup", await self._indexes_on("merchant_verdict"))

    async def test_busy_timeout_is_set(self):
        async with self.db._db.execute("PRAGMA busy_timeout") as cur:
            row = await cur.fetchone()
        self.assertGreater(int(row[0]), 0, "без busy_timeout блокування падає миттєво")


class TestCalendarMonthLimits(_DBCase):
    """
    Дефект: місячне використання рахувалось ковзними 30 днями, тоді як банк
    (і lazy_monthly_reset) працюють по календарному місяцю. 1-го числа банк
    давав чистий ліміт, а модель ще місяць вважала картку завантаженою.
    """

    async def _add_card(self) -> str:
        card_id = "card-1"
        await self.db.add_card({
            "id": card_id, "owner_id": 1, "bank_name": "monobank",
            "last_four": "1234", "label": "test", "balance": 50000.0,
        })
        return card_id

    async def _tx(self, card_id: str, amount: float, ts: float) -> None:
        await self.db._db.execute(
            "INSERT INTO card_transactions (id, card_id, amount, direction, type, source, timestamp) "
            "VALUES (?, ?, ?, 'out', 'work', 'test', ?)",
            (f"tx-{ts}-{amount}", card_id, amount, ts),
        )
        await self.db._db.commit()

    async def test_previous_month_is_not_counted(self):
        card_id = await self._add_card()
        now = datetime.datetime.now()
        month_start = datetime.datetime(now.year, now.month, 1).timestamp()

        # Транзакція за 3 дні ДО початку поточного місяця — банк її вже забув
        await self._tx(card_id, 30000.0, month_start - 3 * 86400)
        # І одна в поточному місяці
        await self._tx(card_id, 5000.0, month_start + 60)

        monthly = await self.db.get_monthly_used(card_id, "out")
        self.assertEqual(monthly, 5000.0, "у місячний ліміт потрапила транзакція минулого місяця")

    async def test_rolling_24h_unchanged(self):
        card_id = await self._add_card()
        now = time.time()
        await self._tx(card_id, 1000.0, now - 3600)
        await self._tx(card_id, 2000.0, now - 30 * 3600)  # старше за 24 години

        daily = await self.db.get_rolling_used(card_id, "out", hours=24)
        self.assertEqual(daily, 1000.0)


class TestActiveUsersColumns(_DBCase):
    """
    Дефект: buy_balance_mode / auto-scale читались у taker_scanner і в меню,
    але не були в SELECT — тобто налаштування зберігалось і ігнорувалось.
    """

    async def asyncSetUp(self):
        await super().asyncSetUp()
        await self.db.register_user(555, 555)

    async def test_buy_balance_mode_reaches_scanner(self):
        await self.db.update_buy_balance_mode(555, mode="MANUAL_STRICT", scale_down=0, scale_up=0)

        users = await self.db.get_active_users()
        user = next(u for u in users if u["user_id"] == 555)

        self.assertEqual(user["buy_balance_mode"], "MANUAL_STRICT")
        self.assertEqual(user["buy_auto_scale_down"], 0)
        self.assertEqual(user["buy_auto_scale_up"], 0)

    async def test_buy_balance_mode_reaches_menu(self):
        await self.db.update_buy_balance_mode(555, mode="AUTO_SCALE")

        user = await self.db.get_user_by_id(555)
        self.assertEqual(user["buy_balance_mode"], "AUTO_SCALE")

    async def test_sniper_rules_parse_after_alias_fix(self):
        await self.db.update_sniper_rules(555, [{"exchange": "Bybit", "direction": "BUY",
                                                 "min_spread": 1.0, "min_volume": 100.0}])
        users = await self.db.get_active_users()
        user = next(u for u in users if u["user_id"] == 555)
        self.assertEqual(len(user["sniper_rules"]), 1)
        self.assertEqual(user["sniper_rules"][0]["exchange"], "Bybit")


if __name__ == "__main__":
    unittest.main()
