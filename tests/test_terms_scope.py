# tests/test_terms_scope.py
"""
Умови угоди судять правила для умов, а не правила для відгуків.

`review_only` — прапорець на правилі: це написано під мову ВІДГУКІВ.
«Кинув», «шахрай», «дроп» у відгуку пише потерпілий, і вага 100 там
доречна. Те саме слово в умовах пише сам мерчант — найчастіше щоб від
цього відхреститись.

`review_fetcher` цей прапорець поважав завжди, `regex_analyzer` — ні. З 25
SOFT-правил 18 мають review_only=True, серед них сім із вагою 100 при порозі
ескалації 30. Одне випадкове спрацювання давало score 100, verdict
NEEDS_LLM і risk_type TRIANGLE — і LLM отримувала промпт, у якому вже
написано «Regex main risk: TRIANGLE».

Другий файл про те саме з іншого боку: службове речення про НАС не має
підставлятись у поле, де мають бути слова МЕРЧАНТА.
"""
from __future__ import annotations

import unittest

from core.analysis import regex_analyzer as ra
from core.analysis.rules import HARD_RULES, SAFE_RULES, SOFT_RULES, WARN_RULES


class TestReviewRulesStayOutOfTerms(unittest.TestCase):
    def test_no_review_only_rule_can_judge_terms(self):
        for name, rules in (
            ("HARD", ra.TERMS_HARD_RULES),
            ("SOFT", ra.TERMS_SOFT_RULES),
            ("SAFE", ra.TERMS_SAFE_RULES),
            ("WARN", ra.TERMS_WARN_RULES),
        ):
            with self.subTest(layer=name):
                leaked = [r.id for r in rules if r.review_only]
                self.assertEqual(leaked, [], f"{name}: review-правила судять умови: {leaked}")

    def test_the_filter_actually_removes_something(self):
        # Якщо колись усі review_only приберуть із SOFT_RULES, цей тест
        # нагадає, що фільтр став беззмістовним — а не мовчки зеленітиме.
        self.assertLess(len(ra.TERMS_SOFT_RULES), len(SOFT_RULES))
        self.assertLess(len(ra.TERMS_SAFE_RULES), len(SAFE_RULES))

    def test_terms_only_rules_are_all_kept(self):
        for name, full, kept in (
            ("HARD", HARD_RULES, ra.TERMS_HARD_RULES),
            ("SOFT", SOFT_RULES, ra.TERMS_SOFT_RULES),
            ("SAFE", SAFE_RULES, ra.TERMS_SAFE_RULES),
            ("WARN", WARN_RULES, ra.TERMS_WARN_RULES),
        ):
            with self.subTest(layer=name):
                expected = [r.id for r in full if not r.review_only]
                self.assertEqual([r.id for r in kept], expected)


class TestRealTermsDoNotBlowUp(unittest.TestCase):
    """Формулювання, які пишуть звичайні мерчанти, не мають давати BLOCK."""

    CLEAN = (
        "оплата тільки з власної картки монобанк приват",
        "працюю з дропами? ні, дропи заборонені",
        "оплата 15 хв, чек обов'язково, піб має збігатись",
        "без третіх осіб, тільки власник картки",
    )

    def test_clean_terms_stay_clean(self):
        for text in self.CLEAN:
            with self.subTest(text=text):
                r = ra.analyze(text, 99.0, 500, True)
                self.assertNotEqual(r.verdict, "BLOCK", f"{text!r} → {r.risk_type}")

    def test_hard_blocks_still_fire(self):
        # Фільтр не має послабити те, що й мало блокувати.
        r = ra.analyze("пишіть в телеграм перед оплатою @merchant", 99.0, 500, True)
        self.assertEqual(r.verdict, "BLOCK")
        self.assertEqual(r.risk_type, "EXTERNAL_LINK")

    def test_weak_signal_still_reaches_the_llm(self):
        # Прибравши review-правила, ми втратили їхню вагу 100 на умовах. Але
        # м'який сигнал лишається — і саме він відкриває шлях до LLM у
        # risk_engine (гілка "reason або behavior_flags → NEEDS_LLM").
        # Регекс має ПІДОЗРЮВАТИ, вирок — за моделлю.
        r = ra.analyze("приймаю оплату від третіх осіб, знайомих", 99.0, 500, True)
        self.assertTrue(r.reason, "слабкий сигнал зник — ордер піде повз LLM")
        self.assertEqual(r.risk_type, "THIRD_PARTY_HINT")


if __name__ == "__main__":
    unittest.main()
