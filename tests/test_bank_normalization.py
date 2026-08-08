"""
Нормалізація назв банків — одна на всю систему.

Та сама мапа («43» → monobank, «моно» → monobank, «pb» → privatbank…)
лежала скопійованою в чотирьох файлах: alert_dispatcher, taker_scanner,
card_repo і formatters. Копії вже почали розходитись: альтернативні коди
61 / 80 / 1 знав лише formatters, тож для решти системи ордер із таким
кодом банку був невідомим банком.

Цей файл фіксує, що джерело одне і всі споживачі дають однаковий результат.
"""
from __future__ import annotations

import unittest

from bot.formatters import _bank_code_to_db
from config.banks import BANKS, normalize_bank, normalize_banks
from core.engine.alert_dispatcher import AlertDispatcher


class TestCanonicalNames(unittest.TestCase):
    def test_codes_map_to_slugs_used_across_the_engine(self):
        expected = {
            "43": "monobank",
            "14": "privatbank",
            "64": "pumb",
            "48": "a-bank",
            "553": "izibank",
            "328": "sense",
        }
        for code, slug in expected.items():
            with self.subTest(code=code):
                self.assertEqual(normalize_bank(code), slug)

    def test_alternative_exchange_codes_are_understood(self):
        # Раніше їх знав тільки formatters — движок бачив «61» як невідомий банк.
        self.assertEqual(normalize_bank("61"), "a-bank")
        self.assertEqual(normalize_bank("80"), "pumb")
        self.assertEqual(normalize_bank("1"), "monobank")

    def test_human_spellings_collapse_to_one_name(self):
        for value in ("Monobank", "monobank", "МОНО", "монобанк", "mono", "43"):
            with self.subTest(value=value):
                self.assertEqual(normalize_bank(value), "monobank")

    def test_unknown_value_survives_lowercased(self):
        # Невідоме не ковтаємо: інакше зникне сам факт, що банк був.
        self.assertEqual(normalize_bank("НовийБанк"), "новийбанк")
        self.assertEqual(normalize_bank(""), "")

    def test_every_bank_in_the_registry_has_a_slug(self):
        for bank in BANKS:
            with self.subTest(bank=bank.name):
                slug = normalize_bank(bank.internal_code)
                self.assertNotEqual(
                    slug, bank.internal_code,
                    f"{bank.name} ({bank.internal_code}) не має канонічної назви",
                )


class TestConsumersAgree(unittest.TestCase):
    """Усі точки входу мають давати той самий результат."""

    def test_formatters_and_config_agree(self):
        for code in ("43", "14", "64", "48", "61", "80", "1"):
            with self.subTest(code=code):
                self.assertEqual(_bank_code_to_db(code), normalize_bank(code))

    def test_dispatcher_parses_dirty_sqlite_values(self):
        clean = AlertDispatcher._clean_and_normalize_banks

        # Значення приходять і списком, і CSV, і як repr списку.
        self.assertEqual(clean(["43", "14"]), {"monobank", "privatbank"})
        self.assertEqual(clean("43,14"), {"monobank", "privatbank"})
        self.assertEqual(clean("['43', '64']"), {"monobank", "pumb"})
        self.assertEqual(clean(None), set())

    def test_dispatcher_agrees_with_plain_normalization(self):
        self.assertEqual(
            AlertDispatcher._clean_and_normalize_banks(["43", "64"]),
            normalize_banks("43,64"),
        )

    def test_hyphenated_slug_survives_tokenizing(self):
        # a-bank містить дефіс: наївний токенайзер розбив би його на два слова.
        self.assertEqual(AlertDispatcher._clean_and_normalize_banks(["48"]), {"a-bank"})
        self.assertEqual(AlertDispatcher._clean_and_normalize_banks("a-bank"), {"a-bank"})


if __name__ == "__main__":
    unittest.main()
