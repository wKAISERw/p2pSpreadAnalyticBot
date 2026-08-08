"""
«Не змогли перевірити» ≠ «безпечно».

Коли відгуки недоступні — протухла сесія, біржа не віддає API, немає
інтернету — ризик-движок раніше просто не додавав жодного прапорця. Ордер
отримував risk_flag="OK", а в базу лягав вердикт «ризиків не виявлено» з
trade_recommendation="APPROVE". Далі цей вердикт брався з кешу як доведено
чистий, хоча перевірки не було взагалі.

Це найгірша форма помилки в ризиках: невідоме подається як перевірене.
"""
from __future__ import annotations

import unittest

from bot.formatters import format_risk_line, risk_badge
from core.engine.risk_engine import _build_review_flags_from_summary

TECHNICAL_STATUSES = ("UNAVAILABLE", "NOT_SUPPORTED", "NO_AUTH", "NO_SESSION", "UNKNOWN")


class TestReviewGapsAreVisible(unittest.TestCase):
    def test_technical_failure_is_flagged_not_silently_clean(self):
        for status in TECHNICAL_STATUSES:
            with self.subTest(status=status):
                flags = _build_review_flags_from_summary({"status": status})
                self.assertTrue(flags, f"{status} не лишив жодного сліду")
                self.assertTrue(
                    any(f.startswith("UNKNOWN:REVIEWS") for f in flags),
                    f"{status} має давати UNKNOWN:REVIEWS, отримано {flags}",
                )

    def test_unknown_never_blocks(self):
        # Фільтри в alert_dispatcher/taker_scanner шукають підрядок "BLOCK".
        # Якщо він тут з'явиться, без сесій перестане працювати все.
        for status in TECHNICAL_STATUSES:
            with self.subTest(status=status):
                flags = _build_review_flags_from_summary({"status": status})
                self.assertNotIn("BLOCK", ",".join(flags))

    def test_real_reviews_still_produce_normal_flags(self):
        # Успішна перевірка з чистим результатом не має позначатись як UNKNOWN.
        flags = _build_review_flags_from_summary(
            {"status": "OK", "positive": 500, "negative": 0, "neutral": 0, "bad_texts": []}
        )
        self.assertFalse(
            any(f.startswith("UNKNOWN") for f in flags),
            f"чисті відгуки позначено як неперевірені: {flags}",
        )


class TestUnknownIsReadable(unittest.TestCase):
    def test_badge_marks_unknown_separately_from_risk(self):
        self.assertEqual(risk_badge("UNKNOWN:REVIEWS:NO_SESSION"), "❔")
        self.assertNotEqual(risk_badge("BLOCK:TRIANGLE"), "❔")

    def test_line_explains_why_there_is_no_verdict(self):
        line = format_risk_line("UNKNOWN:REVIEWS:NO_SESSION")
        self.assertIn("НЕ ПЕРЕВІРЕНО", line)
        self.assertIn("немає сесії", line)
        # Формулювання не має звучати як звинувачення мерчанта.
        self.assertNotIn("РИЗИК", line.upper())

    def test_several_gaps_are_listed_together(self):
        line = format_risk_line("UNKNOWN:REVIEWS:NO_SESSION,UNKNOWN:TERMS:EMPTY")
        self.assertIn("відгуки", line)
        self.assertIn("умови угоди", line)

    def test_clean_order_stays_silent(self):
        self.assertEqual(format_risk_line("OK"), "")
        self.assertEqual(format_risk_line(""), "")

    def test_real_risk_is_not_swallowed_by_unknown_branch(self):
        line = format_risk_line("BLOCK:TRIANGLE")
        self.assertIn("TRIANGLE", line)
        self.assertNotIn("НЕ ПЕРЕВІРЕНО", line)


if __name__ == "__main__":
    unittest.main()
