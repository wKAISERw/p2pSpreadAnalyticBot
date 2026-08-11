# tests/test_signal_registry.py
"""
Етап 1: реєстр сигналів.

Правило перестало бути парою «регекс + вага» і стало сигналом, який знає
про себе все: де йому дозволено дивитись, чому це ризик, коли це НЕ ризик,
як воно звучить у житті.

Три речі, яких раніше не існувало:

1. **Заперечення як поле сигналу.** Досі воно жило в `suppress_hard` як
   набір вгаданих наперед фраз. «не працюю з дропами, не приймаю обнал»
   давало BLOCK FINCRIME, бо в списку були «без обналу» і «не обнал», а
   «не приймаю обнал» ніхто не передбачив.
2. **Область (`scope`) як властивість правила**, а не домовленість між
   модулями, які розійшлись (баг 2.2).
3. **`PAYMENT_TARGET`** — накопичувальні рахунки, які досі жили двома
   захардкодженими патернами поза реєстром і не ловили найчастіші
   написання.
"""
from __future__ import annotations

import unittest

from core.analysis.rules import ALL_RULES
from core.risk.matcher import match_text, normalize
from core.risk.registry import CATEGORY_TITLES, builtin_registry
from core.risk.signals import (
    LAYER_HARD, LAYER_SAFE, SCOPE_REVIEWS, SCOPE_TERMS,
    compile_phrases, is_negated,
)


class TestRegistryCarriesKnowledge(unittest.TestCase):
    def setUp(self):
        self.reg = builtin_registry()

    def test_every_generated_rule_became_a_signal(self):
        keys = {s.key for s in self.reg.signals}
        for rule in ALL_RULES:
            with self.subTest(rule=rule.id):
                self.assertIn(rule.id, keys)

    def test_patterns_are_carried_over_untouched(self):
        # Патерни перевірені живим потоком. Переписувати їх заразом зі
        # зміною архітектури означало б змішати два ризики в одній зміні.
        for rule in ALL_RULES:
            with self.subTest(rule=rule.id):
                self.assertIs(self.reg.by_key(rule.id).pattern, rule.pattern)

    def test_json_metadata_reached_the_signals(self):
        # Ці знання лежали в tools/antifrod_*.json з самого початку і
        # втрачались при генерації: build_rules брав лише регекс і вагу.
        fincrime = self.reg.by_key("GEN_S02_FINCRIME")
        self.assertIn("відмиван", fincrime.why.lower())
        self.assertIn("без обналу", fincrime.negations)
        self.assertGreater(fincrime.confidence, 0.5)

    def test_every_category_has_a_human_title(self):
        for category in self.reg.categories():
            with self.subTest(category=category):
                self.assertIn(category, CATEGORY_TITLES)

    def test_safe_signals_suppress_categories_not_phrases(self):
        # `suppress_hard` мав два різні змісти в одному полі: у SAFE це
        # назви категорій, у HARD/SOFT — фрази-заперечення.
        safe = [s for s in self.reg.signals if s.layer == LAYER_SAFE]
        self.assertTrue(safe)
        for s in safe:
            with self.subTest(signal=s.key):
                for cat in s.suppresses:
                    self.assertEqual(cat, cat.upper(), f"{cat} схоже на фразу, не категорію")


class TestNegationIsGeneral(unittest.TestCase):
    """Заперечення має працювати на формулюваннях, яких ніхто не вгадував."""

    BLOCKED = "обнал приймаю, пишіть в тг"
    DENIED = (
        "не приймаю обнал",
        "не працюю з дропами, не приймаю обнал, чисті кошти тільки",
        "без обналу",
        "обнал не беру",
        "не беру обнал",
    )

    def test_denials_are_not_blocks(self):
        for text in self.DENIED:
            with self.subTest(text=text):
                found = match_text(text, SCOPE_TERMS)
                self.assertIsNone(found.hard, f"{text!r} прийнято за ризик")

    def test_the_real_thing_still_blocks(self):
        self.assertIsNotNone(match_text(self.BLOCKED, SCOPE_TERMS).hard)

    def test_denial_does_not_leak_across_a_sentence(self):
        # «дропи заборонені» стосується дропів, не наступного речення.
        found = match_text("дропи заборонені. обнал приймаю", SCOPE_TERMS)
        self.assertIsNotNone(found.hard)

    def test_safe_signals_are_not_negated_themselves(self):
        # SAFE-сигнал сам є запереченням. Проганяти його через перевірку
        # заперечень означало б заперечити заперечення.
        found = match_text("не приймаю від третіх осіб", SCOPE_TERMS)
        self.assertTrue(
            any(m.signal.layer == LAYER_SAFE for m in found.matches),
            f"захисне формулювання загубилось: {found.negated}",
        )

    def test_explicit_phrase_beats_the_general_rule(self):
        self.assertTrue(is_negated("чисті кошти тільки обнал", 20, 25, ("чисті кошти тільки",)))


class TestScopeSeparatesTermsFromReviews(unittest.TestCase):
    def test_review_language_does_not_judge_terms(self):
        # «кинув», «шахрай» у відгуку пише потерпілий; у полі умов те саме
        # слово пише сам мерчант.
        terms = match_text("не кидаю, не шахрай, працюю чесно", SCOPE_TERMS)
        self.assertEqual(terms.score, 0)

    def test_review_language_works_in_reviews(self):
        found = match_text("кинув на 5000, шахрай", SCOPE_REVIEWS)
        self.assertGreater(found.score, 0)

    def test_denial_inside_a_review_is_respected_too(self):
        found = match_text("мерчант не кидає, все чесно", SCOPE_REVIEWS)
        self.assertEqual(found.score, 0)


class TestPaymentTargetVocabulary(unittest.TestCase):
    """
    Розділ 3 плану: «монобанка» — накопичувальний рахунок, а не банк.
    Старі патерни ловили лише частину написань.
    """

    CAUGHT = (
        "кидаю на монобанку і конверт приват",
        "приймаю на монобанку",            # злите написання — раніше мимо
        "а-банк збір, приват конверт",     # «збір» не було в патерні взагалі
        "накопичувальний збір абанк",
        "оплата на скарбничку",
        "кидати на копилку",
        "оплата на банку моно",
        "send.monobank.ua/jar/xxx",
    )
    IGNORED = (
        "тільки на картку, без банок",
        "працюю з банками україни",        # межі слова: не «банка»
        "переказ між банками",
        "збірка документів",               # не «збір»
        "оплата тільки з власної картки монобанк приват",   # назви банків
        # Омонімія, знайдена на живих умовах із бази: «банку» — і давальний
        # від «банка» (скарбничка), і родовий від «банк» (установа).
        # Розрізняє прийменник напрямку: у скарбничку кидають, з банку платять.
        "можлива оплата з іншого банку 🏦, або оплата в кілька платежів",
        "принимаем с любого банка, работаем быстро",
        "оплата з банку по реквізитах",
    )

    def _jar(self, text: str) -> bool:
        return any(
            m.signal.key == "PAY_JAR"
            for m in match_text(text, SCOPE_TERMS).matches
        )

    def test_jars_are_recognised(self):
        for text in self.CAUGHT:
            with self.subTest(text=text):
                self.assertTrue(self._jar(text), f"{text!r} не розпізнано")

    def test_ordinary_bank_talk_is_left_alone(self):
        for text in self.IGNORED:
            with self.subTest(text=text):
                self.assertFalse(self._jar(text), f"{text!r} — хибне спрацювання")

    def test_flags_keep_their_old_names(self):
        # Ці рядки читають alert_builder, alert_dispatcher, monitoring і
        # formatters. Перейменування зламало б персональні фільтри.
        from core.analysis.regex_analyzer import check_custom_blocks_metadata

        self.assertEqual(check_custom_blocks_metadata("приймаю на монобанку"), ["BANKA_JAR_BLOCKED"])
        self.assertEqual(check_custom_blocks_metadata("оплата на рахунок ФОП"), ["FOP_TOV_BLOCKED"])
        self.assertEqual(check_custom_blocks_metadata("тільки фізособа, не фоп"), [])


class TestDirectScamAccusations(unittest.TestCase):
    """
    Прогалина, знайдена на 2978 збережених текстах відгуків: сигналу на саме
    слово «скам» не було ЖОДНОГО. Відгук «не скинув гроші) дуже довго. Скам»
    отримував FINCRIME — але не за «скам», а помилково, за «скинув гроші» в
    патерні про брудні кошти. Прибравши хибне спрацювання, ми лишились би з
    нулем на прямому звинуваченні.
    """

    def _cats(self, text: str) -> list[str]:
        return [m.category for m in match_text(text, SCOPE_REVIEWS).matches]

    def test_direct_accusation_is_caught(self):
        for text in ("скам", "це шахрай", "кидала, не повернув кошти",
                     "розвів на гроші", "scammer"):
            with self.subTest(text=text):
                self.assertIn("SCAM_REPORT", self._cats(text))

    def test_the_review_that_exposed_the_gap(self):
        text = "Позначив ордер як оплачений, не скинув гроші) дуже довго. Скам"
        self.assertIn("SCAM_REPORT", self._cats(text))

    def test_denial_of_scam_is_not_an_accusation(self):
        for text in ("не скам, все ок", "мерчант не шахрай", "чесний, не кидала"):
            with self.subTest(text=text):
                self.assertNotIn("SCAM_REPORT", self._cats(text))

    def test_slow_service_is_not_fraud(self):
        for text in ("повільно відповідає", "довго закривав ордер",
                     "некомпетентний продавець, не вміє рахувати"):
            with self.subTest(text=text):
                self.assertEqual(self._cats(text), [])

    def test_accusations_do_not_leak_into_terms(self):
        # Мерчант у власних умовах пише «не скам» — це не звинувачення.
        self.assertNotIn("SCAM_REPORT", [
            m.category for m in match_text("я не скам, працюю чесно", SCOPE_TERMS).matches
        ])


class TestPhraseCompiler(unittest.TestCase):
    """Користувач у боті пише фрази, а не регекси (етап 4)."""

    def test_phrases_become_a_working_pattern(self):
        p = compile_phrases(["кидаю на банку", "конверт приват"])
        self.assertTrue(p.search("я кидаю на банку завжди"))
        self.assertTrue(p.search("конверт-приват"))

    def test_word_boundaries_are_enforced(self):
        p = compile_phrases(["банка"])
        self.assertIsNone(p.search("працюю з банками"))
        self.assertTrue(p.search("на банка"))

    def test_empty_input_gives_no_pattern(self):
        # Патерн із порожнього списку збігався б із будь-чим.
        self.assertIsNone(compile_phrases([]))
        self.assertIsNone(compile_phrases(["", "   "]))


class TestMatcherBasics(unittest.TestCase):
    def test_homoglyphs_do_not_hide_a_block(self):
        # Латинська «o» замість кирилиці — класичне маскування.
        self.assertIsNotNone(match_text("oбнал приймаю", SCOPE_TERMS).hard)

    def test_hard_match_stops_the_scan(self):
        found = match_text("обнал приймаю, казино, дропи", SCOPE_TERMS)
        self.assertEqual(len([m for m in found.matches if m.signal.layer == LAYER_HARD]), 1)

    def test_empty_text_is_not_a_signal(self):
        self.assertEqual(match_text("", SCOPE_TERMS).matches, [])
        self.assertEqual(match_text("   ", SCOPE_TERMS).matches, [])

    def test_every_match_carries_its_evidence(self):
        for m in match_text("обнал приймаю", SCOPE_TERMS).matches:
            with self.subTest(signal=m.signal.key):
                self.assertTrue(m.excerpt, "збіг без цитати — нічим пояснити людині")

    def test_normalize_collapses_noise(self):
        self.assertEqual(normalize("  ОБНАЛ​   приймаю "), "обнал приймаю")


if __name__ == "__main__":
    unittest.main()
