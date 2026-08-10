# tests/test_reviews_not_wiped.py
"""
Невдала спроба оновити відгуки не має стирати вже відомі.

Раніше кожен технічний збій ішов через `save_reviews(..., 0, 0, 0, [])`, а це
upsert по всіх колонках. Мерчант, чиї 500 відгуків і тексти скарг ми успішно
зібрали вчора, після однієї невдалої спроби сьогодні лишався з нулями. У
базі це видно як 2257 рядків NO_SESSION — усі з нульовими лічильниками й без
жодного тексту.

Ми не просто не дізнавались нового. Ми знищували те, що знали, — і далі
показували цей нуль як факт про мерчанта.
"""
from __future__ import annotations

import shutil
import tempfile
import unittest
from pathlib import Path
from unittest.mock import AsyncMock

from core.storage.merchant_db import MerchantDB
from core.workers.review_fetcher import ReviewFetcher


class _ReviewsCase(unittest.IsolatedAsyncioTestCase):
    EX, MID = "Binance", "merchant-1"

    async def asyncSetUp(self):
        self._dir = Path(tempfile.mkdtemp(prefix="reviews_test_"))
        self.db = MerchantDB(self._dir / "m.db")
        await self.db.start()
        # Успішний збір: 97 позитивних, 3 негативних, один текст скарги.
        await self.db.save_reviews(
            self.EX, self.MID, 97, 3, 0,
            [{"text": "кинув на 5000", "score": 100, "categories": ["TRIANGLE"]}],
            status="OK",
        )

    async def asyncTearDown(self):
        await self.db.stop()
        shutil.rmtree(self._dir, ignore_errors=True)


class TestFailureKeepsKnownReviews(_ReviewsCase):
    async def test_marking_unavailable_keeps_counters_and_texts(self):
        await self.db.mark_reviews_unavailable(self.EX, self.MID, "NO_SESSION", "сесія впала")

        summary = await self.db.get_reviews_summary(self.EX, self.MID)
        self.assertEqual(summary["positive"], 97)
        self.assertEqual(summary["negative"], 3)
        self.assertEqual(len(summary["bad_texts"]), 1, "тексти скарг стерто")
        self.assertEqual(summary["status"], "NO_SESSION")
        self.assertEqual(summary["error_reason"], "сесія впала")

    async def test_data_at_marks_when_reviews_were_really_collected(self):
        before = await self.db.get_reviews_summary(self.EX, self.MID)
        await self.db.mark_reviews_unavailable(self.EX, self.MID, "API_ERROR")
        after = await self.db.get_reviews_summary(self.EX, self.MID)

        # updated_at рухається (на ньому тримається бекоф), data_at — ні.
        self.assertGreaterEqual(after["updated_at"], before["updated_at"])
        self.assertEqual(after["data_at"], before["data_at"])

    async def test_unknown_merchant_gets_honest_zeroes(self):
        # Мерчанта не бачили жодного разу — тут нулі правдиві, і data_at=0
        # це прямо каже.
        await self.db.mark_reviews_unavailable(self.EX, "never-seen", "NO_SESSION")
        summary = await self.db.get_reviews_summary(self.EX, "never-seen")
        self.assertEqual(summary["positive"], 0)
        self.assertEqual(summary["data_at"], 0)

    async def test_successful_refetch_replaces_data_and_bumps_data_at(self):
        old = await self.db.get_reviews_summary(self.EX, self.MID)
        await self.db.save_reviews(self.EX, self.MID, 120, 1, 0, [], status="OK")
        new = await self.db.get_reviews_summary(self.EX, self.MID)
        self.assertEqual(new["positive"], 120)
        self.assertEqual(new["bad_texts"], [])
        self.assertGreaterEqual(new["data_at"], old["data_at"])


class TestFetcherReturnsLastKnown(_ReviewsCase):
    async def _fetcher(self) -> ReviewFetcher:
        return ReviewFetcher(self.db)

    async def test_no_session_returns_stored_reviews_not_zeroes(self):
        f = await self._fetcher()
        f._binance = object()          # клієнт є, сесії немає
        result = await f.fetch_now(self.EX, self.MID)

        self.assertEqual(result["status"], "NO_SESSION")
        self.assertEqual(result["negative"], 3, f"повернуто нулі замість відомого: {result}")
        self.assertEqual(len(result["bad_texts"]), 1)
        self.assertGreater(result["data_at"], 0)

    async def test_offline_does_not_touch_the_database(self):
        import state as state_mod

        f = await self._fetcher()
        original = state_mod.state.stats.get("internet_connected", True)
        state_mod.state.stats["internet_connected"] = False
        try:
            result = await f.fetch_now(self.EX, self.MID)
        finally:
            state_mod.state.stats["internet_connected"] = original

        self.assertEqual(result["status"], "UNAVAILABLE")
        self.assertEqual(result["negative"], 3)
        # Стан бази не змінився: відсутність інтернету — факт про нас.
        stored = await self.db.get_reviews_summary(self.EX, self.MID)
        self.assertEqual(stored["status"], "OK")

    async def test_api_error_keeps_texts_for_the_verdict(self):
        f = await self._fetcher()
        f._binance = object()
        # Сесія є, але фетч падає з API_ERROR.
        self.db.get_auth_session = AsyncMock(return_value=({"h": "1"}, {"c": "1"}, 0))
        f._fetch_binance = AsyncMock(side_effect=RuntimeError("API_ERROR: 500"))

        result = await f.fetch_now(self.EX, self.MID)
        self.assertEqual(result["status"], "API_ERROR")
        self.assertEqual(len(result["bad_texts"]), 1, "скарга загубилась при збої API")

        stored = await self.db.get_reviews_summary(self.EX, self.MID)
        self.assertEqual(stored["negative"], 3, "збій API стер лічильники в базі")


if __name__ == "__main__":
    unittest.main()
