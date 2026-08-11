# tests/test_llm_coverage_block.py
"""
Етап 6: моделі показують ту саму межу видимості, що й людині.

Досі про це розповідали дев'ять взаємовиключних гілок по `rev_status`,
дописуваних у різний час. Вони встигли розійтись: та сама протухла сесія в
одній гілці була «технічною помилкою нашої системи», а в іншій мовчки
ставала «відгуків немає» — і модель писала про чисту репутацію мерчанта,
чиїх відгуків ніхто не бачив.

Тепер джерело одне — `RiskCoverage`, той самий об'єкт, що йде в алерт.
Тести стережуть межу, за яку не можна заходити: сліпоту не можна показувати
як чистоту, а чистоту не можна ховати за сліпотою.
"""
from __future__ import annotations

import time
import unittest

from core.analysis.regex_analyzer import RegexResult
from core.engine import reviews_status, terms_status
from core.engine.risk_coverage import RiskCoverage
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


FULL = RiskCoverage(
    terms=terms_status.OK, reviews=reviews_status.OK,
    snapshots=12, identity_checked=True, review_texts=True,
)


class TestGapsAreNamed(unittest.TestCase):
    def test_every_gap_reaches_the_prompt(self):
        cov = RiskCoverage(
            terms=terms_status.NO_SESSION, reviews=reviews_status.API_ERROR,
            snapshots=1, identity_checked=False,
        )
        prompt = _build_prompt(_task(coverage=cov), {"status": "API_ERROR"})
        for gap in cov.gaps():
            with self.subTest(gap=gap):
                self.assertIn(gap, prompt)

    def test_full_coverage_says_so_instead_of_listing_nothing(self):
        # Порожній список прогалин мовчки читався б як «все гаразд» —
        # рівно та двозначність, яку етап 0 і прибирав.
        summary = {"status": "OK", "positive": 50, "negative": 1,
                   "bad_texts": [{"text": "затримка"}], "data_at": time.time()}
        prompt = _build_prompt(_task(coverage=FULL), summary)
        self.assertIn("Прогалин немає", prompt)

    def test_gap_is_framed_as_our_blindness_not_merchant_fault(self):
        cov = RiskCoverage(terms=terms_status.OK, reviews=reviews_status.NO_SESSION)
        prompt = _build_prompt(_task(coverage=cov), {"status": "NO_SESSION"})
        self.assertIn("межа НАШОЇ видимості", prompt)
        self.assertIn("не називай непереверене чистим", prompt)

    def test_behaviour_blindness_is_visible(self):
        # Поведінка мовчала при <3 снапшотах, і промпт про це не казав нічого:
        # «історії ще немає» виглядало як «поведінка нормальна».
        prompt = _build_prompt(_task(coverage=RiskCoverage(snapshots=1)), {})
        self.assertIn("замало історії", prompt)

    def test_twins_blindness_is_visible(self):
        prompt = _build_prompt(_task(coverage=RiskCoverage(identity_checked=False)), {})
        self.assertIn("клонів на інших біржах не шукали", prompt)


class TestBlindReviewsAreNotCleanReviews(unittest.TestCase):
    """Скарга з постановки: бот каже «немає відгуків», коли не було сесії."""

    DARK = {"status": "NO_SESSION", "positive": 0, "negative": 0, "data_at": 0}

    def test_session_failure_is_not_reported_as_absence_of_reviews(self):
        prompt = _build_prompt(_task(), self.DARK)
        self.assertIn("НЕ БАЧИЛИ ЖОДНОГО РАЗУ", prompt)
        self.assertIn("немає сесії біржі", prompt)

    def test_dark_reviews_forbid_any_reputation_claim(self):
        prompt = _build_prompt(_task(), self.DARK)
        self.assertIn("ЖОДНИХ висновків", prompt)
        self.assertNotIn("репутація чиста", prompt)

    def test_dark_reviews_do_not_ask_for_trade_statistics_either(self):
        # Раніше сюди підмішувалась оцінка з completion rate — і виходило,
        # що про невидимі відгуки модель усе-таки щось писала.
        prompt = _build_prompt(_task(), self.DARK)
        self.assertNotIn("успішних /", prompt)

    def test_no_feedback_is_a_fact_about_the_merchant(self):
        # NO_FEEDBACK — успішна відповідь біржі, а не наша сліпота.
        prompt = _build_prompt(_task(), {"status": "NO_FEEDBACK", "data_at": time.time()})
        self.assertIn("біржа відповіла успішно", prompt)
        self.assertNotIn("НЕ БАЧИЛИ ЖОДНОГО РАЗУ", prompt)

    def test_stale_data_is_shown_but_dated(self):
        summary = {"status": "NO_SESSION", "positive": 90, "negative": 10,
                   "bad_texts": [], "data_at": time.time() - 30 * 3600}
        prompt = _build_prompt(_task(), summary)
        self.assertIn("НЕ ОНОВЛЮВАЛИСЬ 30 год", prompt)
        self.assertIn("pos=90", prompt)      # старе показуємо
        self.assertNotIn("НЕ БАЧИЛИ ЖОДНОГО РАЗУ", prompt)


class TestContradictionsAreGone(unittest.TestCase):
    """Дев'ять гілок могли спрацювати разом і суперечити одна одній."""

    def test_blind_status_never_coexists_with_a_cleanliness_claim(self):
        for status in sorted(reviews_status.BLIND):
            with self.subTest(status=status):
                prompt = _build_prompt(
                    _task(), {"status": status, "positive": 0, "negative": 0, "data_at": 0}
                )
                self.assertNotIn("репутація чиста", prompt)
                self.assertNotIn("Переважно чисті", prompt)

    def test_exactly_one_verdict_about_reviews(self):
        # Взаємовиключні твердження раніше могли потрапити в промпт разом.
        summary = {"status": "OK", "positive": 500, "negative": 0,
                   "bad_texts": [], "data_at": time.time()}
        prompt = _build_prompt(_task(), summary)
        claims = sum(
            1 for phrase in ("НЕ БАЧИЛИ ЖОДНОГО РАЗУ", "мерчант новий",
                             "ЦЕ НЕ ВІДГУКИ", "біржа відповіла успішно")
            if phrase in prompt
        )
        self.assertEqual(claims, 0)

    def test_wallet_blindness_is_called_permanent(self):
        prompt = _build_prompt(_task(exchange="Wallet"), {"status": "NOT_SUPPORTED"})
        self.assertIn("не має API відгуків", prompt)


class TestFallbackCoverage(unittest.TestCase):
    """Прямі виклики (тести, `risk_probe`) покриття не передають."""

    def test_missing_coverage_does_not_claim_we_checked(self):
        prompt = _build_prompt(_task(), {"status": "OK", "positive": 5, "data_at": time.time()})
        self.assertIn("НЕ шукали", prompt)
        self.assertIn("замало історії", prompt)

    def test_empty_terms_are_reported_as_unseen(self):
        prompt = _build_prompt(_task(trade_terms=""), {})
        self.assertIn("Умови: НЕ бачили", prompt)

    def test_present_terms_are_reported_as_seen(self):
        prompt = _build_prompt(_task(), {})
        self.assertIn("Умови: бачили", prompt)


if __name__ == "__main__":
    unittest.main()
