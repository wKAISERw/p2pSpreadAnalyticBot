# tests/test_terms_facts.py
"""
Етап 6: вижимка умов як перелік фактів із доказами.

Скарга, з якої це почалось: «аі вижимка трішки упускає важливі деталі в
умовах». Причина структурна, не в моделі. Коли просиш «опиши умови двома
реченнями», модель мусить обирати, що викинути, — і викидає те, що
вважає другорядним. Мерчант написав п'ять вимог, у вижимку влізло дві.

Друга звідти ж: «кидаю на монобанку і конверт приват» переказано як «кидає
на монобанк і приватбанк». Проти цього тут працює вимога **дослівної
цитати**: щоб написати «приватбанк», модель мусила б процитувати слово,
якого в тексті немає, — і звірка це показує.

Тести перевіряють розбір і звірку, а не поведінку моделі: змусити її
цитувати можна лише промптом, а от упіймати вигадку — механічно.
"""
from __future__ import annotations

import json
import unittest

from core.workers.terms_facts import (
    MIN_QUOTE_LEN, TermsFact, from_json, parse_facts, render_block, to_json, to_summary,
)

TERMS = "кидаю на монобанку і конверт приват. оплата 15 хв, чек обов'язково. без третіх осіб"


def _raw(**over):
    base = {"topic": "Куди платіж", "quote": "кидаю на монобанку", "meaning": "накопичувальний рахунок"}
    base.update(over)
    return [base]


class TestQuotesAreVerified(unittest.TestCase):
    def test_real_quote_is_verified(self):
        facts = parse_facts(_raw(), TERMS)
        self.assertTrue(facts[0].verified)

    def test_invented_quote_is_flagged(self):
        # Рівно та помилка з постановки: продукт перетворено на назву банку.
        facts = parse_facts(_raw(quote="приймає монобанк і приватбанк"), TERMS)
        self.assertFalse(facts[0].verified)

    def test_flagged_fact_is_kept_not_dropped(self):
        # Викидати було б самовпевнено: модель могла перефразувати відмінок,
        # і факт лишається слушним. Але мовчати теж не можна.
        facts = parse_facts(_raw(quote="вигадка якої немає"), TERMS)
        self.assertEqual(len(facts), 1)
        self.assertIn("⚠️", facts[0].render())

    def test_case_and_spacing_do_not_break_verification(self):
        facts = parse_facts(_raw(quote="Кидаю   НА  Монобанку"), TERMS)
        self.assertTrue(facts[0].verified)

    def test_too_short_quote_is_not_trusted(self):
        # «на» трапляється в будь-якому тексті випадково.
        facts = parse_facts(_raw(quote="на"), TERMS)
        self.assertFalse(facts[0].verified)
        self.assertLess(len("на"), MIN_QUOTE_LEN)

    def test_no_source_terms_means_nothing_is_verified(self):
        # Порожні умови не можуть підтвердити нічого — і не мають вдавати,
        # що можуть.
        facts = parse_facts(_raw(), "")
        self.assertFalse(facts[0].verified)


class TestStructureSurvivesTheModel(unittest.TestCase):
    def test_garbage_gives_no_facts(self):
        for raw in (None, "", "не json", {"topic": "x"}, 42, ["рядок"]):
            with self.subTest(raw=raw):
                self.assertEqual(parse_facts(raw, TERMS), [])

    def test_json_string_is_accepted(self):
        facts = parse_facts(json.dumps(_raw(), ensure_ascii=False), TERMS)
        self.assertEqual(len(facts), 1)

    def test_facts_without_topic_or_meaning_are_skipped(self):
        self.assertEqual(parse_facts([{"quote": "оплата 15 хв"}], TERMS), [])

    def test_round_trip_through_json(self):
        facts = parse_facts(_raw(), TERMS)
        restored = from_json(to_json(facts))
        self.assertEqual(len(restored), 1)
        self.assertEqual(restored[0].quote, facts[0].quote)
        self.assertEqual(restored[0].verified, facts[0].verified)

    def test_five_requirements_stay_five(self):
        """Головне: перелік не дає згорнути п'ять пунктів у два."""
        raw = [
            {"topic": f"Вимога {i}", "quote": "оплата 15 хв", "meaning": f"пункт {i}"}
            for i in range(5)
        ]
        self.assertEqual(len(parse_facts(raw, TERMS)), 5)


class TestBackwardCompatibility(unittest.TestCase):
    def test_summary_is_built_from_facts(self):
        # Дашборд — окремий репозиторій і читає terms_summary. Міняти під
        # ним формат на льоту означало б зламати його мовчки.
        summary = to_summary(parse_facts(_raw(), TERMS))
        self.assertIn("Куди платіж", summary)
        self.assertLessEqual(len(summary), 300)

    def test_empty_facts_give_empty_summary(self):
        self.assertEqual(to_summary([]), "")
        self.assertEqual(render_block([]), "")


class TestRendering(unittest.TestCase):
    def test_block_shows_quote_and_meaning(self):
        out = render_block(parse_facts(_raw(), TERMS))
        self.assertIn("Куди платіж", out)
        self.assertIn("кидаю на монобанку", out)

    def test_block_counts_unverified(self):
        facts = parse_facts(
            _raw() + [{"topic": "Банки", "quote": "приватбанк і монобанк", "meaning": "два банки"}],
            TERMS,
        )
        out = render_block(facts)
        self.assertIn("1 з 2", out)

    def test_all_verified_says_nothing_extra(self):
        out = render_block(parse_facts(_raw(), TERMS))
        self.assertNotIn("не знайдено", out)


class TestParserIntegration(unittest.TestCase):
    """Стик із відповіддю моделі."""

    def _answer(self, facts: list[dict]) -> str:
        return json.dumps({
            "thought_process": "міркування", "status": "SUSPICIOUS", "risk": "THIRD_PARTY_HINT",
            "reason": "є банка", "trade_recommendation": "CONDITIONAL",
            "reviews_analysis": "чисто", "terms_facts": facts,
        }, ensure_ascii=False)

    def test_facts_and_thoughts_reach_the_caller(self):
        from core.workers.llm_worker import _parse_json

        parsed = _parse_json(self._answer(_raw()), TERMS)
        self.assertEqual(parsed["thought_process"], "міркування")
        self.assertTrue(from_json(parsed["terms_facts"])[0].verified)

    def test_summary_is_filled_even_though_the_model_skipped_it(self):
        from core.workers.llm_worker import _parse_json

        parsed = _parse_json(self._answer(_raw()), TERMS)
        self.assertTrue(parsed["terms_summary"])

    def test_old_style_answer_still_parses(self):
        # Кешовані вердикти й старі відповіді без terms_facts не мають
        # ламати розбір.
        from core.workers.llm_worker import _parse_json

        old = json.dumps({"status": "OK", "risk": "NONE", "reason": "чисто",
                          "terms_summary": "тільки Моно"}, ensure_ascii=False)
        parsed = _parse_json(old, TERMS)
        self.assertEqual(parsed["terms_summary"], "тільки Моно")
        self.assertEqual(parsed["terms_facts"], "")


if __name__ == "__main__":
    unittest.main()
