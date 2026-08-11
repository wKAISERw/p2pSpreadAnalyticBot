# tests/test_risk_coverage.py
"""
Етап 2: межа нашої видимості — одним об'єктом.

Покриття було розсіяне по чотирьох джерелах і трьох формах: умови в полі
`terms_status`, відгуки в статусі зведення плюс прапори, а поведінка й
пошук клонів **не повідомляли про свою сліпоту взагалі**. `analyze_history`
мовчки повертає порожній результат, коли снапшотів менше трьох, і в алерті
«поведінка нормальна» виглядало так само, як «історії ще немає».

Через це правило «не знаю ≠ безпечно» доводилось повторювати в кожному
місці окремо, і два з чотирьох джерел про нього не знали.
"""
from __future__ import annotations

import unittest

from core.engine import reviews_status as rs
from core.engine import terms_status as ts
from core.engine.risk_coverage import BEHAVIOR_MIN_SNAPSHOTS, RiskCoverage

FULL = RiskCoverage(ts.OK, rs.OK, snapshots=5, identity_checked=True, review_texts=True)


class TestWhatCounts(unittest.TestCase):
    def test_full_coverage_has_no_gaps(self):
        self.assertTrue(FULL.is_full)
        self.assertEqual(FULL.gaps(), [])
        self.assertEqual(FULL.summary(), "")

    def test_blind_terms_are_a_gap(self):
        cov = RiskCoverage(ts.NO_SESSION, rs.OK, 5, True, True)
        self.assertFalse(cov.terms_seen)
        self.assertTrue(any("умов" in g for g in cov.gaps()))

    def test_blind_reviews_are_a_gap(self):
        cov = RiskCoverage(ts.OK, rs.API_ERROR, 5, True, True)
        self.assertFalse(cov.reviews_seen)
        self.assertTrue(any("відгук" in g for g in cov.gaps()))

    def test_thin_history_is_a_gap(self):
        """Те, про що досі не казав ніхто."""
        cov = RiskCoverage(ts.OK, rs.OK, snapshots=1, identity_checked=True, review_texts=True)
        self.assertFalse(cov.behavior_seen)
        self.assertTrue(any("замало історії" in g for g in cov.gaps()))

    def test_enough_history_is_not_a_gap(self):
        cov = RiskCoverage(ts.OK, rs.OK, BEHAVIOR_MIN_SNAPSHOTS, True, True)
        self.assertTrue(cov.behavior_seen)
        self.assertFalse(any("історії" in g for g in cov.gaps()))

    def test_unsearched_twins_are_a_gap(self):
        cov = RiskCoverage(ts.OK, rs.OK, 5, identity_checked=False, review_texts=True)
        self.assertTrue(any("клон" in g for g in cov.gaps()))

    def test_counters_without_texts_are_a_gap(self):
        # Статус OK, але самих текстів ми не бачили — висновок про ЗМІСТ
        # скарг зробити нема з чого.
        cov = RiskCoverage(ts.OK, rs.OK, 5, True, review_texts=False)
        self.assertTrue(any("лічильники" in g for g in cov.gaps()))

    def test_no_feedback_is_not_blindness(self):
        # Біржа відповіла: відгуків немає. Це факт про мерчанта.
        cov = RiskCoverage(ts.OK, rs.NO_FEEDBACK, 5, True, False)
        self.assertTrue(cov.reviews_seen)

    def test_fully_blind_when_neither_terms_nor_reviews(self):
        cov = RiskCoverage(ts.NO_SESSION, rs.NO_SESSION, 5, True, False)
        self.assertTrue(cov.is_blind)
        self.assertFalse(FULL.is_blind)


class TestWording(unittest.TestCase):
    """Формулювання читає людина в алерті — воно має бути зрозумілим."""

    def test_subject_is_not_repeated(self):
        # Мітка вже каже «умов не видно»; префікс «умови — » задвоїв би.
        gaps = RiskCoverage(ts.NO_SESSION, rs.OK, 5, True, True).gaps()
        self.assertEqual(gaps, ["немає сесії біржі — умов не видно"])

    def test_ambiguous_label_gets_a_subject(self):
        # «біржа не відповідає» однаково пасує до умов і до відгуків.
        gaps = RiskCoverage(ts.OK, rs.UNAVAILABLE, 5, True, True).gaps()
        self.assertEqual(gaps, ["відгуки — біржа не відповідає"])

    def test_terms_and_reviews_come_before_the_rest(self):
        cov = RiskCoverage(ts.NO_SESSION, rs.NO_SESSION, 0, False, False)
        gaps = cov.gaps()
        self.assertIn("умов", gaps[0])
        self.assertIn("відгук", gaps[1])

    def test_summary_joins_everything(self):
        cov = RiskCoverage(ts.OK, rs.OK, 1, False, True)
        self.assertIn(";", cov.summary())


class TestItStaysInSyncWithTheBehaviourLayer(unittest.TestCase):
    def test_snapshot_threshold_matches_the_analyzer(self):
        # Значення дубльоване навмисно (імпорт створив би цикл), тож
        # розходження має ловити саме цей тест.
        from core.analysis.behavioral_analyzer import MIN_SNAPSHOTS

        self.assertEqual(BEHAVIOR_MIN_SNAPSHOTS, MIN_SNAPSHOTS)


class TestRenderedInTheAlert(unittest.TestCase):
    def test_gaps_reach_the_live_renderer(self):
        from bot.formatters import _risk_badge

        class _Order:
            risk_flag = "LOW_STATS"
            trade_terms = "умови"
            risk_coverage = RiskCoverage(ts.OK, rs.OK, 1, False, True)

        out = _risk_badge(_Order())
        self.assertIn("НЕ ПЕРЕВІРЕНО", out)
        self.assertIn("замало історії", out)
        # Знайдений ризик лишається першим.
        self.assertLess(out.index("МАЛО УГОД"), out.index("НЕ ПЕРЕВІРЕНО"))

    def test_full_coverage_adds_nothing(self):
        from bot.formatters import _risk_badge

        class _Order:
            risk_flag = "LOW_STATS"
            trade_terms = "умови"
            risk_coverage = FULL

        self.assertNotIn("НЕ ПЕРЕВІРЕНО", _risk_badge(_Order()))


if __name__ == "__main__":
    unittest.main()
