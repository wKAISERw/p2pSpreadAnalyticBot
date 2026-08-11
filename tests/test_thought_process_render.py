# tests/test_thought_process_render.py
"""
Хід думок моделі в алерті — під власним перемикачем.

`reason` каже, ЩО вирішено. Ланцюжок каже, ЧОМУ саме так: які умови
переважили, що було у відгуках, які прогалини на це вплинули. Модель його
й раніше писала (`thought_process` у промпті), але він нікуди не йшов —
розбирався й викидався.

Окремий перемикач, а не `show_ai_logic`, з простої арифметики: у парному
алерті блок вердикту йде двічі, і 600 символів на бік — це +1200 у
повідомленні з лімітом 4096. Тому вимкнено за замовчуванням.
"""
from __future__ import annotations

import unittest

from bot.formatters import _llm_verdict_block

THOUGHT = "Умови: тільки своя картка. Відгуки: 3 негативних про затримки. Висновок: обережно."


class TestThoughtIsOptional(unittest.TestCase):
    def test_hidden_by_default(self):
        out = _llm_verdict_block("Buy", "APPROVE", "чисто", thought_process=THOUGHT)
        self.assertNotIn("Хід думок", out)

    def test_shown_when_enabled(self):
        out = _llm_verdict_block(
            "Buy", "APPROVE", "чисто", thought_process=THOUGHT, show_ai_thoughts=True
        )
        self.assertIn("Хід думок", out)
        self.assertIn("3 негативних про затримки", out)

    def test_independent_from_show_ai_logic(self):
        # Хтось хоче ланцюжок, але не хоче короткого reason — і навпаки.
        out = _llm_verdict_block(
            "Buy", "APPROVE", "короткий висновок",
            show_ai_logic=False, thought_process=THOUGHT, show_ai_thoughts=True,
        )
        self.assertIn("Хід думок", out)
        self.assertNotIn("короткий висновок", out)

    def test_nothing_rendered_when_model_said_nothing(self):
        out = _llm_verdict_block("Buy", "APPROVE", "чисто", thought_process="   ",
                                 show_ai_thoughts=True)
        self.assertNotIn("Хід думок", out)

    def test_pending_verdict_has_no_thoughts_yet(self):
        # PENDING означає, що модель ще не відповідала: показувати тут
        # ланцюжок від попереднього вердикту — брехати про свіжість.
        out = _llm_verdict_block("Buy", "PENDING", "", thought_process=THOUGHT,
                                 show_ai_thoughts=True)
        self.assertNotIn("Хід думок", out)


class TestThoughtIsSafeToRender(unittest.TestCase):
    def test_html_is_escaped(self):
        out = _llm_verdict_block(
            "Buy", "APPROVE", "чисто", show_ai_thoughts=True,
            thought_process="<b>умови</b> & <script>alert(1)</script>",
        )
        self.assertNotIn("<script>", out)
        self.assertIn("&lt;script&gt;", out)

    def test_long_thought_is_capped(self):
        out = _llm_verdict_block(
            "Buy", "APPROVE", "чисто", show_ai_thoughts=True,
            thought_process="я" * 5000,
        )
        self.assertLess(len(out), 1200)

    def test_block_is_collapsible(self):
        out = _llm_verdict_block("Buy", "APPROVE", "чисто", thought_process=THOUGHT,
                                 show_ai_thoughts=True)
        self.assertIn("<blockquote expandable>🧩", out)


class TestSettingIsWiredEndToEnd(unittest.TestCase):
    def test_default_is_off_everywhere(self):
        """Одне значення за замовчуванням у чотирьох місцях, де воно є."""
        import bot.alert_builder as ab
        import bot.taker_builder as tb
        import inspect

        for mod in (ab, tb):
            with self.subTest(module=mod.__name__):
                src = inspect.getsource(mod)
                self.assertIn('"show_ai_thoughts": False', src)
                self.assertIn('ds.get("show_ai_thoughts", False)', src)

    def test_menu_knows_the_toggle(self):
        from bot.handlers.filters import _DISPLAY_DESCRIPTIONS, _DISPLAY_LABELS

        self.assertIn("show_ai_thoughts", _DISPLAY_LABELS)
        self.assertIn("show_ai_thoughts", _DISPLAY_DESCRIPTIONS)

    def test_keyboard_renders_off_state(self):
        from bot.keyboards.filters import display_settings_kb

        kb = display_settings_kb({})
        buttons = [b.text for row in kb.inline_keyboard for b in row]
        thought = [b for b in buttons if "Хід думок" in b]
        self.assertEqual(len(thought), 1)
        # Порожній dict = нічого не налаштовано. `_icon` за замовчуванням
        # ставить ✅, і перемикач мовчки з'явився б увімкненим.
        self.assertTrue(thought[0].startswith("❌"))

    def test_keyboard_renders_on_state(self):
        from bot.keyboards.filters import display_settings_kb

        kb = display_settings_kb({"show_ai_thoughts": True})
        buttons = [b.text for row in kb.inline_keyboard for b in row]
        self.assertTrue(any(b.startswith("✅") and "Хід думок" in b for b in buttons))


if __name__ == "__main__":
    unittest.main()
