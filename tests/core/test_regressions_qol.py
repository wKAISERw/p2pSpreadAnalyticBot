"""
Регресійні тести на баги, знайдені під час аудиту (гілка quality-of-life).

Кожен тест прив'язаний до конкретного дефекту — якщо котрийсь із них знову
почервоніє, значить фікс відкотили.
"""
from __future__ import annotations

import asyncio
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from core.engine.maker_ad_monitor import MakerAdMonitor
from core.storage.merchant_db import MerchantDB
# Імпортуємо на рівні модуля: інші тести підміняють core.utils у sys.modules,
# тож пізній імпорт усередині тесту повернув би MagicMock.
from core.utils.tasks import spawn, pending_count


# ─────────────────────────────────────────────────────────────────────────────
class TestSeenOrdersFifo(unittest.TestCase):
    """
    Баг: витіснення кешу побачених ордерів робилось через set.pop(), який
    викидає ДОВІЛЬНИЙ елемент — міг викинути щойно доданий order_id, після
    чого монітор слав по ньому повторну нотифікацію.
    """

    def _monitor(self) -> MakerAdMonitor:
        return MakerAdMonitor(db=None)

    def test_newest_survives_eviction(self):
        mon = self._monitor()
        overflow = mon.MAX_ORDERS_CACHE + 50
        for i in range(overflow):
            mon._mark_seen(f"order-{i}")

        self.assertEqual(len(mon._seen_order_ids), mon.MAX_ORDERS_CACHE)
        # Найновіші лишились...
        self.assertIn(f"order-{overflow - 1}", mon._seen_order_ids)
        self.assertIn(f"order-{overflow - mon.MAX_ORDERS_CACHE}", mon._seen_order_ids)
        # ...а витіснились саме найстаріші.
        self.assertNotIn("order-0", mon._seen_order_ids)

    def test_remark_refreshes_position(self):
        mon = self._monitor()
        mon._mark_seen("keep-me")
        for i in range(mon.MAX_ORDERS_CACHE - 1):
            mon._mark_seen(f"filler-{i}")
        # Повторна поява ордера має підняти його в кінець черги,
        # інакше активний ордер витісниться і задублюється.
        mon._mark_seen("keep-me")
        for i in range(10):
            mon._mark_seen(f"late-{i}")

        self.assertIn("keep-me", mon._seen_order_ids)


# ─────────────────────────────────────────────────────────────────────────────
class TestMakerMonitorExchangeGuard(unittest.IsolatedAsyncioTestCase):
    """
    Баг: _poll_user завжди створював BybitP2PClient, тож оголошення на
    OKX/Binance «моніторились» чужими ключами і не бачили жодного ордера.
    """

    async def test_unsupported_exchange_is_rejected(self):
        mon = MakerAdMonitor(db=None)
        await mon.start_watching(user_id=1, chat_id=1, exchange="OKX", ad_id="ad-1")
        self.assertEqual(mon._watches, {})
        self.assertIsNone(mon._global_poll_task)


# ─────────────────────────────────────────────────────────────────────────────
class TestRiskScoreScale(unittest.IsolatedAsyncioTestCase):
    """
    Баг: save_verdict робив risk_score = MIN(старий + новий, 200), тобто скор
    ріс від самого факту перевірки. Будь-який мерчант рано чи пізно ставав
    «високоризиковим» і назавжди втрачав trusted-імунітет.
    """

    async def asyncSetUp(self):
        self._tmp = TemporaryDirectory()
        self.db = MerchantDB(db_path=Path(self._tmp.name) / "test.db")
        await self.db.start()

    async def asyncTearDown(self):
        await self.db.stop()
        self._tmp.cleanup()

    async def _save(self, verdict: str, terms: str = "умови"):
        await self.db.save_verdict(
            "Bybit", "m-1", "Merchant", terms, verdict, "NONE", "reason", "test",
        )

    async def test_repeated_ok_does_not_inflate_score(self):
        for _ in range(10):
            await self._save("OK")
        self.assertEqual(await self.db.get_risk_score("Bybit", "m-1"), 0)

    async def test_repeated_suspicious_stays_at_verdict_scale(self):
        for _ in range(10):
            await self._save("SUSPICIOUS")
        # Раніше тут було б 200 (стеля), тепер — скор самого вердикту.
        self.assertEqual(await self.db.get_risk_score("Bybit", "m-1"), 30)

    async def test_block_history_decays_instead_of_sticking(self):
        await self._save("BLOCK")
        self.assertEqual(await self.db.get_risk_score("Bybit", "m-1"), 100)

        # Одна OK-перевірка не робить вчорашнього скамера чистим...
        await self._save("OK")
        after_one = await self.db.get_risk_score("Bybit", "m-1")
        self.assertGreater(after_one, 0)
        self.assertLess(after_one, 100)

        # ...але й не тримає його на стелі вічно.
        for _ in range(12):
            await self._save("OK")
        self.assertEqual(await self.db.get_risk_score("Bybit", "m-1"), 0)


# ─────────────────────────────────────────────────────────────────────────────
class TestSpawnHelper(unittest.IsolatedAsyncioTestCase):
    """
    spawn() має тримати сильне посилання на таск (інакше GC може прибрати
    його посеред виконання) і логувати виняток, а не ковтати його.
    """

    async def test_keeps_reference_until_done(self):
        started = asyncio.Event()
        release = asyncio.Event()

        async def _work():
            started.set()
            await release.wait()

        before = pending_count()
        task = spawn(_work(), "test-work")
        await started.wait()
        self.assertEqual(pending_count(), before + 1)

        release.set()
        await task
        await asyncio.sleep(0)  # даємо done-callback відпрацювати
        self.assertEqual(pending_count(), before)

    async def test_exception_is_logged_not_swallowed(self):
        async def _boom():
            raise ValueError("навмисна помилка")

        task = spawn(_boom(), "test-boom")
        with self.assertLogs("BackgroundTasks", level="ERROR") as captured:
            await asyncio.gather(task, return_exceptions=True)
            await asyncio.sleep(0)
        self.assertTrue(any("test-boom" in line for line in captured.output))


if __name__ == "__main__":
    unittest.main()
