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
    """
    Інваріант той самий, але живе він тепер не в списках усередині
    аналізатора, а в полі `scope` самого сигналу (етап 1). Область — це
    властивість правила, а не домовленість між двома модулями, які можуть
    розійтись.
    """

    def setUp(self):
        from core.risk.registry import builtin_registry

        self.reg = builtin_registry()

    def test_no_review_signal_can_judge_terms(self):
        from core.risk.signals import LAYER_HARD, LAYER_SAFE, LAYER_SOFT, LAYER_WARN, SCOPE_TERMS

        for layer in (LAYER_HARD, LAYER_SOFT, LAYER_WARN, LAYER_SAFE):
            with self.subTest(layer=layer):
                leaked = [
                    s.key for s in self.reg.for_scope(SCOPE_TERMS, layer)
                    if s.scope == "reviews"
                ]
                self.assertEqual(leaked, [], f"{layer}: review-сигнали судять умови: {leaked}")

    def test_the_split_actually_separates_something(self):
        # Якщо колись усі сигнали стануть однієї області, цей тест нагадає,
        # що поділ став беззмістовним — а не мовчки зеленітиме.
        from core.risk.signals import SCOPE_REVIEWS, SCOPE_TERMS

        terms = {s.key for s in self.reg.signals if s.applies_to(SCOPE_TERMS)}
        reviews = {s.key for s in self.reg.signals if s.applies_to(SCOPE_REVIEWS)}
        self.assertTrue(terms - reviews, "жоден сигнал не є суто термовим")
        self.assertTrue(reviews - terms, "жоден сигнал не є суто відгуковим")

    def test_scope_matches_the_generated_review_only_flag(self):
        # Джерело правди про область — досі `review_only` у rules.py.
        # Реєстр мусить його переносити один в один, без самодіяльності.
        for rules in (HARD_RULES, SOFT_RULES, WARN_RULES, SAFE_RULES):
            for rule in rules:
                with self.subTest(rule=rule.id):
                    signal = self.reg.by_key(rule.id)
                    self.assertIsNotNone(signal, f"{rule.id} не потрапив у реєстр")
                    expected = "reviews" if rule.review_only else "terms"
                    self.assertEqual(signal.scope, expected)


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
