# tests/test_llm_prompt_honesty.py
"""
Промпт не має наказувати моделі стверджувати те, чого ми не знаємо.

Для Binance/Bybit профіль часто віддає нулі, і `risk_engine` підставляє
замість них оцінку: кількість УГОД за місяць, перераховану через
`positive_rate`. Позначка «(оцінено зі статистики профілю)» поруч була, але
далі ця оцінка йшла в ту саму гілку, що й справжні відгуки, і при neg == 0
модель отримувала пряму директиву:

    ⬆️ ВІДГУКИ ПОВНІСТЮ ЧИСТІ: Жодного негативного відгуку! Мейкер має
    бездоганну репутацію. Обов'язково вкажи у thought_process, reason та
    reviews_analysis: «відгуки чисті, негативні відгуки відсутні…»

Тобто наказ описати бездоганну репутацію мерчанта, чиїх відгуків ніхто не
бачив. Директива сильніша за примітку в дужках, і модель слухалась саме її.
"""
from __future__ import annotations

import time
import unittest

from core.analysis.regex_analyzer import RegexResult
from core.workers.llm_worker import LLMTask, _build_prompt


def _task(**over) -> LLMTask:
    base = dict(
        exchange="Binance", merchant_id="m1", merchant_name="Тест",
        trade_terms="тільки з власної картки",
        regex_result=RegexResult("OK"),
        finish_rate=99.3, month_order_count=972, is_verified=True,
        min_limit=1000.0, max_limit=50000.0,
    )
    base.update(over)
    return LLMTask(**base)


class TestEstimatedStatsAreNotCalledReviews(unittest.TestCase):
    ESTIMATED = {
        "status": "OK", "positive": 965, "negative": 7, "neutral": 0,
        "bad_texts": [], "estimated_from_stats": True, "data_at": time.time(),
    }

    def test_estimate_is_never_presented_as_clean_reviews(self):
        prompt = _build_prompt(_task(), self.ESTIMATED)
        self.assertNotIn("ВІДГУКИ ПОВНІСТЮ ЧИСТІ", prompt)
        self.assertNotIn("бездоганну репутацію", prompt)

    def test_estimate_is_labelled_as_trade_statistics(self):
        prompt = _build_prompt(_task(), self.ESTIMATED)
        self.assertIn("ОЦІНКА ЗІ СТАТИСТИКИ УГОД", prompt)
        self.assertIn("не відгуки", prompt.lower())

    def test_zero_negative_estimate_does_not_claim_spotless(self):
        # Найнебезпечніший випадок: оцінка дала neg=0.
        summary = {**self.ESTIMATED, "positive": 972, "negative": 0}
        prompt = _build_prompt(_task(), summary)
        self.assertNotIn("бездоганну репутацію", prompt)
        self.assertNotIn("репутація чиста", prompt)


class TestRealReviewsKeepTheirWording(unittest.TestCase):
    def test_genuinely_clean_reviews_still_say_so(self):
        summary = {
            "status": "OK", "positive": 500, "negative": 0, "neutral": 0,
            "bad_texts": [], "data_at": time.time(),
        }
        prompt = _build_prompt(_task(), summary)
        self.assertIn("репутація чиста", prompt)


class TestStaleReviewsAreAnnouncedToTheModel(unittest.TestCase):
    def test_model_is_told_the_data_is_old(self):
        summary = {
            "status": "NO_SESSION", "positive": 90, "negative": 10, "neutral": 0,
            "bad_texts": [], "data_at": time.time() - 30 * 3600,
        }
        prompt = _build_prompt(_task(), summary)
        self.assertIn("НЕ ОНОВЛЮВАЛИСЬ", prompt)
        self.assertIn("30 год", prompt)

    def test_fresh_reviews_carry_no_stale_warning(self):
        summary = {
            "status": "OK", "positive": 90, "negative": 10, "neutral": 0,
            "bad_texts": [], "data_at": time.time(),
        }
        self.assertNotIn("НЕ ОНОВЛЮВАЛИСЬ", _build_prompt(_task(), summary))


if __name__ == "__main__":
    unittest.main()
