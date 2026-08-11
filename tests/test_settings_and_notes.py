# tests/test_settings_and_notes.py
"""
Група 0.e: налаштування, які нічим не керували, і тексти, які вигадували.

**Налаштування.** `config/runtime.ALLOWED_KEYS` пускав у меню сім ризикових
ключів. Працювали лише ваги. `behavior_alert_score`, `velocity_spike_per_hour`
і `sticky_min_chain` мали пункти меню, зберігались у `bot_settings` — і не
читались ніде: константи тягнулись із `config.defaults` **на рівні модуля**,
тобто фіксувались при імпорті. `block_fop_tov` і `block_banka_jar` не мали
навіть пункту меню, а `risk_mode` керував класом, який спрацьовував ДО
аналізу ризику й у дефолтному режимі пропускав усе.

**Тексти.** `trusted_skip` писав у базу «Відгуки чисті, без скарг на
шахрайство» — і гілка спрацьовувала навіть тоді, коли відгуків не бачили
жодного разу: `has_review_concern` шукає підрядок "BADREVIEWS", а прапор
UNKNOWN:REVIEWS його не містить.
"""
from __future__ import annotations

import time
import unittest
from decimal import Decimal

from config.runtime import ALLOWED_KEYS
from core.engine import reviews_status as rs
from core.engine import terms_status as ts
from core.engine.risk_engine import _reviews_note, _terms_note, _trusted_reason
from exchanges.base import Order


class TestEveryAllowedKeyHasAConsumer(unittest.TestCase):
    """
    Інвентаризація: кожен ключ, який меню дозволяє змінювати, мусить мати
    доведеного читача. Інакше ми знову продаємо людині кнопку, що нічого
    не робить.
    """

    def test_removed_keys_are_gone(self):
        for key in ("risk_mode", "block_fop_tov", "block_banka_jar"):
            with self.subTest(key=key):
                self.assertNotIn(key, ALLOWED_KEYS)

    def test_behaviour_thresholds_are_read_at_decision_time(self):
        import core.analysis.behavioral_analyzer as ba
        import core.engine.risk_engine as re_mod
        from config.runtime import runtime_config

        original = dict(runtime_config._cache)
        try:
            runtime_config._cache["behavior_alert_score"] = "42"
            runtime_config._cache["sticky_min_chain"] = "7"
            runtime_config._cache["velocity_spike_per_hour"] = "5.5"

            self.assertEqual(re_mod._behavior_alert_score(), 42)
            self.assertEqual(ba._tuned("sticky_min_chain", 3, int), 7)
            self.assertEqual(ba._tuned("velocity_spike_per_hour", 20.0, float), 5.5)
        finally:
            runtime_config._cache = original

    def test_broken_value_falls_back_instead_of_crashing(self):
        import core.analysis.behavioral_analyzer as ba
        from config.runtime import runtime_config

        original = dict(runtime_config._cache)
        try:
            runtime_config._cache["sticky_min_chain"] = "не число"
            self.assertEqual(ba._tuned("sticky_min_chain", 3, int), 3)
        finally:
            runtime_config._cache = original

    def test_merchant_filter_module_is_gone(self):
        # Клас із NameError у єдиній нетривіальній гілці, який до того ж
        # стояв до аналізу ризику. Видалено разом із пунктом меню.
        with self.assertRaises(ImportError):
            __import__("filters.merchant_filter")


class TestVerdictTextsDescribeWhatWeChecked(unittest.TestCase):
    def _order(self, terms_state: str = ts.OK) -> Order:
        return Order(
            id="o1", price=Decimal("41"), available_amount=Decimal("100"),
            min_limit=Decimal("1000"), max_limit=Decimal("5000"),
            merchant_id="m1", merchant_name="Merchant",
            month_order_count=900, finish_rate_pct=99.5,
            exchange="Bybit", terms_status=terms_state,
        )

    DARK = {"status": rs.NO_SESSION, "positive": 0, "negative": 0,
            "neutral": 0, "bad_texts": [], "data_at": 0}
    CLEAN = {"status": rs.OK, "positive": 500, "negative": 0,
             "neutral": 0, "bad_texts": [], "data_at": time.time()}

    def test_unseen_reviews_are_never_called_clean(self):
        note = _reviews_note(self.DARK)
        self.assertIn("не бачили", note)
        self.assertNotIn("чист", note.lower())

    def test_clean_reviews_are_stated_with_numbers(self):
        note = _reviews_note(self.CLEAN)
        self.assertIn("500", note)
        self.assertNotIn("не бачили", note)

    def test_stale_reviews_are_marked_as_not_refreshed(self):
        stale = {**self.CLEAN, "status": rs.NO_SESSION,
                 "data_at": time.time() - 30 * 3600, "negative": 2}
        self.assertIn("не оновлювались", _reviews_note(stale))

    def test_no_feedback_is_a_fact_not_an_excuse(self):
        empty = {"status": rs.NO_FEEDBACK, "positive": 0, "negative": 0,
                 "neutral": 0, "bad_texts": [], "data_at": time.time()}
        self.assertIn("немає", _reviews_note(empty))
        self.assertNotIn("не бачили", _reviews_note(empty))

    def test_unseen_terms_are_not_called_standard(self):
        note = _terms_note("", ts.NO_SESSION)
        self.assertIn("не бачили", note)
        self.assertNotIn("стандартн", note.lower())

    def test_genuinely_empty_terms_describe_the_merchant(self):
        self.assertIn("не вказав", _terms_note("", ts.EMPTY))

    def test_trusted_reason_admits_the_gaps(self):
        # Прив'язуємось до змісту, а не до слів: формулювання прогалин
        # тепер приходить із `risk_coverage`/`terms_status`, і переписати
        # мітку там не має ламати цей тест.
        reason = _trusted_reason(self._order(ts.NO_SESSION), self.DARK, "", ts.NO_SESSION)
        self.assertIn("відгук", reason)
        self.assertIn("умов", reason)
        self.assertIn("неповний", reason)

    def test_missing_coverage_is_not_read_as_all_clear(self):
        """
        Найнебезпечніший стан: покриття не порахували взагалі.

        Порожній список прогалин тут означав би «усе перевірено» — тобто
        рівно ту підміну «не знаю» на «безпечно», проти якої весь етап 0.
        Тому за відсутності покриття причини виводяться з аргументів.
        """
        order = self._order(ts.NO_SESSION)
        self.assertIsNone(getattr(order, "risk_coverage", None))

        reason = _trusted_reason(order, self.DARK, "", ts.NO_SESSION)
        self.assertIn("неповний", reason)
        self.assertNotIn("ризиків не виявлено", reason)

    def test_trusted_reason_stays_positive_when_everything_was_checked(self):
        reason = _trusted_reason(self._order(), self.CLEAN, "тільки своя картка", ts.OK)
        self.assertIn("ризиків не виявлено", reason)
        self.assertNotIn("не бачили", reason)

    def test_trusted_reason_never_claims_a_deep_check_happened(self):
        # Мерчанта свідомо не віддали моделі — текст не має вдавати інше.
        reason = _trusted_reason(self._order(), self.CLEAN, "умови", ts.OK)
        self.assertIn("не запускалась", reason)


class TestReviewRefetchBacksOff(unittest.IsolatedAsyncioTestCase):
    """Технічний збій не має означати запит кожного циклу назавжди."""

    async def asyncSetUp(self):
        from pathlib import Path
        from tempfile import TemporaryDirectory

        from core.storage.merchant_db import MerchantDB

        self._tmp = TemporaryDirectory()
        self.db = MerchantDB(db_path=Path(self._tmp.name) / "t.db")
        await self.db.start()

    async def asyncTearDown(self):
        await self.db.stop()
        self._tmp.cleanup()

    async def test_fresh_failure_is_not_retried_immediately(self):
        await self.db.mark_reviews_unavailable("Bybit", "m1", rs.API_ERROR, "500")
        self.assertFalse(await self.db.needs_review_fetch("Bybit", "m1"))

    async def test_old_failure_is_retried(self):
        from core.storage.merchant_repo import RETRY_AFTER_FAILURE_SEC

        await self.db.mark_reviews_unavailable("Bybit", "m1", rs.API_ERROR, "500")
        await self.db._db.execute(
            "UPDATE merchant_reviews SET updated_at = ? WHERE merchant_id = 'm1'",
            (time.time() - RETRY_AFTER_FAILURE_SEC - 10,),
        )
        await self.db._db.commit()
        self.assertTrue(await self.db.needs_review_fetch("Bybit", "m1"))

    async def test_no_feedback_is_not_hammered_every_cycle(self):
        await self.db.save_reviews("MEXC", "m2", 0, 0, 0, [], status=rs.NO_FEEDBACK)
        self.assertFalse(await self.db.needs_review_fetch("MEXC", "m2"))

    async def test_unseen_merchant_is_always_fetched(self):
        self.assertTrue(await self.db.needs_review_fetch("Bybit", "never-seen"))


if __name__ == "__main__":
    unittest.main()
