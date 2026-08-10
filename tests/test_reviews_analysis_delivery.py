# tests/test_reviews_analysis_delivery.py
"""
AI-вижимка відгуків має доїжджати до людини — і мовчати, коли відгуків не видно.

Дві половини одного багу.

**Перша.** `llm_worker._parse_json` не повертав `reviews_analysis` зі свого
словника. Системний промпт вимагав це поле трьома окремими абзацами, модель
його генерувала, ми його парсили і викидали. `_process` читав звідти
порожній рядок, порожній рядок їхав у базу, звідти в алерт, і блок просто
не малювався. Тумблер `show_llm_summary` керував блоком, якого не існує.

У базі це було видно наочно: 1137 вердиктів із `terms_summary` і лише 14 з
`reviews_analysis` — та й ті 14 не від моделі, а захардкожені рядки з
`risk_engine`.

**Друга.** Поки поле було мертвим, ніхто не помітив, що для нього немає
захисту, який для умов існує (`_drop_blind_terms`). Вердикт живе в кеші
кілька днів; якщо відгуки за цей час стали недоступні, вижимка з кешу
описувала б те, чого ми зараз не бачимо, — і подавала б це як поточний стан.

Тому обидві половини лагодяться разом: полагодити першу без другої означає
проміняти мовчання на впевнену неправду.
"""
from __future__ import annotations

import time
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from api.verdict_refresh import _drop_blind_reviews, refresh_flags
from core.engine import reviews_status as rs
from core.storage.merchant_db import MerchantDB
from core.workers.llm_worker import _parse_json

ANALYSIS = "скам/рефанд: 2 скарги; повільно: 5 скарг"


def _summary(status: str, *, data_at: float | None = None, neg: int = 3) -> dict:
    return {
        "status": status,
        "positive": 97, "negative": neg, "neutral": 0,
        "bad_texts": [{"text": "кинув"}],
        "data_at": time.time() if data_at is None else data_at,
    }


class TestParserKeepsReviewsAnalysis(unittest.TestCase):
    RAW = (
        '{"thought_process":"аналіз","status":"SUSPICIOUS","risk":"TRIANGLE",'
        '"reason":"є скарги","trade_recommendation":"CONDITIONAL",'
        '"terms_summary":"тільки Моно","reviews_analysis":"' + ANALYSIS + '"}'
    )

    def test_reviews_analysis_survives_parsing(self):
        self.assertEqual(_parse_json(self.RAW)["reviews_analysis"], ANALYSIS)

    def test_missing_field_is_empty_not_absent(self):
        # Ключ має бути завжди — інакше споживач знову тихо отримає "".
        parsed = _parse_json('{"status":"OK","risk":"NONE","reason":"чисто"}')
        self.assertIn("reviews_analysis", parsed)
        self.assertEqual(parsed["reviews_analysis"], "")

    def test_long_analysis_is_capped(self):
        raw = '{"status":"OK","risk":"NONE","reason":"r","reviews_analysis":"%s"}' % ("я" * 900)
        self.assertLessEqual(len(_parse_json(raw)["reviews_analysis"]), 500)


class TestReviewsCoverage(unittest.TestCase):
    def test_no_feedback_is_a_fact_about_the_merchant_not_about_us(self):
        # Біржа відповіла: відгуків нема. Це не «не перевірили».
        self.assertFalse(rs.is_blind(rs.NO_FEEDBACK))
        self.assertFalse(rs.is_dark({"status": rs.NO_FEEDBACK, "data_at": 0}))

    def test_blind_without_data_is_dark(self):
        self.assertTrue(rs.is_dark({"status": rs.NO_SESSION, "data_at": 0}))
        self.assertTrue(rs.is_dark({"status": rs.API_ERROR, "data_at": 0}))

    def test_blind_with_stored_data_is_not_dark(self):
        # Свіжого не дістали, але вчорашнє знаємо — це третій стан.
        self.assertFalse(rs.is_dark(_summary(rs.NO_SESSION, data_at=time.time() - 30 * 3600)))

    def test_healthy_status_is_never_dark(self):
        self.assertFalse(rs.is_dark(_summary(rs.OK)))

    def test_every_blind_status_has_its_own_wording(self):
        # Кожен сліпий статус мусить мати власний рядок у LABELS, а не
        # провалюватись у загальне «у відповіді біржі відгуків не було».
        for status in rs.BLIND:
            with self.subTest(status=status):
                self.assertIn(status, rs.LABELS)
                self.assertTrue(rs.blind_label(status))

    def test_healthy_status_gets_no_excuse_line(self):
        self.assertEqual(rs.blind_label(rs.OK), "")
        self.assertEqual(rs.blind_label(rs.NO_FEEDBACK), "")


class TestSiteDoesNotShowAnalysisOfUnseenReviews(unittest.TestCase):
    AI = {"recommendation": "CONDITIONAL", "reason": "є скарги", "reviewsAnalysis": ANALYSIS}

    def test_analysis_is_dropped_when_reviews_are_dark(self):
        out = _drop_blind_reviews(self.AI, {"status": rs.NO_SESSION, "data_at": 0})
        self.assertEqual(out["reviewsAnalysis"], "")
        # Решта вердикту лишається: причина стосується не лише відгуків.
        self.assertEqual(out["reason"], "є скарги")

    def test_analysis_survives_when_stored_reviews_exist(self):
        out = _drop_blind_reviews(self.AI, _summary(rs.NO_SESSION, data_at=time.time() - 30 * 3600))
        self.assertEqual(out["reviewsAnalysis"], ANALYSIS)

    def test_analysis_survives_normal_state(self):
        self.assertEqual(
            _drop_blind_reviews(self.AI, _summary(rs.OK))["reviewsAnalysis"], ANALYSIS
        )

    def test_original_dict_is_not_mutated(self):
        _drop_blind_reviews(self.AI, {"status": rs.NO_SESSION, "data_at": 0})
        self.assertEqual(self.AI["reviewsAnalysis"], ANALYSIS)


class TestWorkerActuallyStoresIt(unittest.IsolatedAsyncioTestCase):
    """
    Точка, де вижимка губилась: `_process` читав `reviews_analysis` зі
    словника, якого парсер не наповнював. Тут перевіряємо саме стик, а не
    парсер окремо.
    """

    EX, MID = "OKX", "m2"

    async def asyncSetUp(self):
        self._tmp = TemporaryDirectory()
        self.db = MerchantDB(db_path=Path(self._tmp.name) / "t.db")
        await self.db.start()

    async def asyncTearDown(self):
        await self.db.stop()
        self._tmp.cleanup()

    async def test_model_output_lands_in_the_database(self):
        from core.analysis.regex_analyzer import RegexResult
        from core.workers.llm_worker import LLMTask, LLMWorkerPool

        pool = LLMWorkerPool(self.db)

        # Підміняємо саме мережу, а не розбір відповіді: інакше тест перевіряв
        # би власний мок. Сирий JSON проходить через справжній `_parse_json` —
        # тобто через те місце, де поле й губилось.
        async def _fake_call(task, review_summary):
            parsed = _parse_json(TestParserKeepsReviewsAnalysis.RAW)
            parsed["source"] = "groq_test"
            return parsed

        pool._call_with_fallback = _fake_call

        await pool._process(LLMTask(
            exchange=self.EX, merchant_id=self.MID, merchant_name="Merchant",
            trade_terms="умови", regex_result=RegexResult("OK"),
            finish_rate=99.0, month_order_count=100, is_verified=True,
            min_limit=1000.0, max_limit=5000.0,
            review_summary=_summary(rs.OK),
        ))

        _, _, _, _, stored = await self.db.get_trade_recommendation_full(self.EX, self.MID)
        self.assertEqual(stored, ANALYSIS, "вижимка відгуків знову загубилась по дорозі в базу")


class TestRoundTripToTheFeed(unittest.IsolatedAsyncioTestCase):
    """Наскрізь: вердикт із вижимкою → база → видача сайту."""

    EX, MID = "Bybit", "m1"

    async def asyncSetUp(self):
        self._tmp = TemporaryDirectory()
        self.db = MerchantDB(db_path=Path(self._tmp.name) / "t.db")
        await self.db.start()
        await self.db.save_verdict(
            self.EX, self.MID, "Merchant", "умови", "SUSPICIOUS", "TRIANGLE",
            "є скарги", "groq", trade_recommendation="CONDITIONAL",
            terms_summary="тільки Моно", reviews_analysis=ANALYSIS,
        )

    async def asyncTearDown(self):
        await self.db.stop()
        self._tmp.cleanup()

    def _order(self) -> dict:
        return {
            "exchange": self.EX, "merchantId": self.MID, "merchantName": "Merchant",
            "riskFlag": "LLM_SUSPICIOUS", "tradeTerms": "умови", "termsStatus": "OK",
        }

    async def test_analysis_reaches_the_feed(self):
        await self.db.save_reviews(self.EX, self.MID, 97, 3, 0, [{"text": "кинув"}], status="OK")
        orders = [self._order()]
        await refresh_flags(self.db, orders)
        self.assertEqual(orders[0]["ai"]["reviewsAnalysis"], ANALYSIS)

    async def test_analysis_is_hidden_when_reviews_became_invisible(self):
        # Вердикт зроблено вчора з відгуками; сьогодні сесії немає і
        # збереженого теж (мерчанта ще не встигли зібрати).
        await self.db.mark_reviews_unavailable(self.EX, self.MID, rs.NO_SESSION, "сесія впала")
        orders = [self._order()]
        await refresh_flags(self.db, orders)
        self.assertEqual(orders[0]["ai"]["reviewsAnalysis"], "")
        # Але сам вердикт нікуди не дівається.
        self.assertEqual(orders[0]["ai"]["recommendation"], "CONDITIONAL")

    async def test_stored_reviews_keep_the_analysis_visible(self):
        await self.db.save_reviews(self.EX, self.MID, 97, 3, 0, [{"text": "кинув"}], status="OK")
        await self.db.mark_reviews_unavailable(self.EX, self.MID, rs.NO_SESSION, "сесія впала")
        orders = [self._order()]
        await refresh_flags(self.db, orders)
        self.assertEqual(orders[0]["ai"]["reviewsAnalysis"], ANALYSIS)


if __name__ == "__main__":
    unittest.main()
