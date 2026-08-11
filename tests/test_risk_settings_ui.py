# tests/test_risk_settings_ui.py
"""
Етап 4: конфігуратор ріск-енджину в боті.

Те, з чого починався реворк: «дуже потужний конфігуратор власних проблем,
по категоріях, зі своїми правилами».

Тести тут не про красу екранів, а про три речі, які мовчки ламають меню:
неробочі кнопки (callback_data, який ніхто не ловить), перевищення ліміту
Telegram у 64 байти, і розходження між тим, що показано, і тим, що
збережено.
"""
from __future__ import annotations

import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from bot.keyboards.risk import (
    ACTION_LABELS, ACTION_ORDER, CATEGORY_GROUPS,
    risk_categories_kb, risk_custom_kb, risk_group_kb, risk_main_kb,
    risk_profile_kb, risk_signal_kb,
)
from core.risk.policy import ACTIONS, BLOCK, PROFILES, SIDE_BUY, SIDE_SELL, WARN
from core.risk.registry import CATEGORY_TITLES, builtin_registry
from core.storage.merchant_db import MerchantDB


def _all_callbacks(kb) -> list[str]:
    return [b.callback_data for row in kb.inline_keyboard for b in row if b.callback_data]


def _all_texts(kb) -> list[str]:
    return [b.text for row in kb.inline_keyboard for b in row]


class TestCallbacksFitTelegram(unittest.TestCase):
    """Telegram ріже callback_data на 64 байтах — мовчки."""

    def test_every_signal_action_fits(self):
        for signal in builtin_registry().signals:
            for side in (SIDE_BUY, SIDE_SELL):
                for action in ACTIONS:
                    data = f"risk:act:{signal.key}:{side}:{action}"
                    with self.subTest(data=data):
                        self.assertLessEqual(len(data.encode()), 64)

    def test_signal_screen_callbacks_fit(self):
        for signal in builtin_registry().signals:
            kb = risk_signal_kb(signal.key, WARN, BLOCK, True, False, True)
            for data in _all_callbacks(kb):
                with self.subTest(data=data):
                    self.assertLessEqual(len(data.encode()), 64)


class TestEveryButtonIsHandled(unittest.TestCase):
    """
    Кнопка без обробника — це мовчазний глухий кут: людина тисне, нічого не
    стається, і зрозуміти чому вона не може.
    """

    PREFIXES = (
        "risk:main", "risk:prof", "risk:cats", "risk:grp:", "risk:sig:",
        "risk:act:", "risk:tog:", "risk:rst:", "risk:del:", "risk:mine",
        "risk:add", "risk:test", "risk:reset_all", "risk:noop",
        "menu:settings",
        # Свої ключі до моделей — окремий роутер (bot/handlers/byok.py),
        # підключений у bot/handlers/__init__.py.
        "byok:",
    )

    def test_byok_router_is_actually_registered(self):
        # Префікс у списку вище нічого не доводить: він лише каже, що
        # кнопку хтось мав би ловити. Перевіряємо, що роутер реально
        # підключений — інакше кнопка веде в тишу.
        from bot.handlers import byok, get_router

        names = {r.name for r in get_router().sub_routers}
        self.assertIn(byok.router.name, names)

    def _check(self, kb):
        for data in _all_callbacks(kb):
            with self.subTest(data=data):
                self.assertTrue(
                    any(data == p or data.startswith(p) for p in self.PREFIXES),
                    f"кнопка {data!r} нікуди не веде",
                )

    def test_main_menu(self):
        self._check(risk_main_kb("balanced", 2, 3))

    def test_profile_menu(self):
        self._check(risk_profile_kb("careful"))

    def test_categories_menu(self):
        self._check(risk_categories_kb({"third": 2}))

    def test_group_menu(self):
        self._check(risk_group_kb("third", [("GEN_H02_TRIANGLE", "Треті особи", WARN, BLOCK)]))

    def test_signal_menu(self):
        self._check(risk_signal_kb("GEN_H02_TRIANGLE", WARN, BLOCK, True, True, True))

    def test_custom_menu(self):
        self._check(risk_custom_kb([("u_test_1", "Моє правило")]))


class TestScreensSayWhatMatters(unittest.TestCase):
    def test_group_row_shows_both_directions(self):
        # Купівля й продаж коштують різного — показувати одну дію означало
        # б сховати половину сенсу.
        kb = risk_group_kb("third", [("K", "Треті особи", WARN, BLOCK)])
        text = _all_texts(kb)[0]
        self.assertIn("купівля", text)
        self.assertIn("продаж", text)

    def test_signal_screen_offers_every_action(self):
        kb = risk_signal_kb("K", WARN, BLOCK, True, False, False)
        callbacks = " ".join(_all_callbacks(kb))
        for action in ACTION_ORDER:
            with self.subTest(action=action):
                self.assertIn(f":{SIDE_BUY}:{action}", callbacks)
                self.assertIn(f":{SIDE_SELL}:{action}", callbacks)

    def test_reset_appears_only_when_tuned(self):
        self.assertIn("risk:rst:K", _all_callbacks(risk_signal_kb("K", WARN, BLOCK, True, False, True)))
        self.assertNotIn("risk:rst:K", _all_callbacks(risk_signal_kb("K", WARN, BLOCK, True, False, False)))

    def test_delete_appears_only_for_custom_signals(self):
        self.assertIn("risk:del:K", _all_callbacks(risk_signal_kb("K", WARN, BLOCK, True, True, False)))
        self.assertNotIn("risk:del:K", _all_callbacks(risk_signal_kb("K", WARN, BLOCK, True, False, False)))

    def test_every_action_has_a_human_label(self):
        for action in ACTIONS:
            with self.subTest(action=action):
                self.assertIn(action, ACTION_LABELS)

    def test_every_profile_has_a_title_and_hint(self):
        for key, meta in PROFILES.items():
            with self.subTest(profile=key):
                self.assertTrue(meta.get("title"))
                self.assertTrue(meta.get("hint"))


class TestGroupsCoverTheRegistry(unittest.TestCase):
    def test_no_category_is_left_out_of_the_menu(self):
        """
        Категорія без групи не показується ніде — сигнал працює, а
        налаштувати його неможливо.
        """
        grouped = {c for _, _, cats in CATEGORY_GROUPS for c in cats}
        registry = {s.category for s in builtin_registry().signals}
        # SAFE — захисні формулювання мерчанта, вони не налаштовуються:
        # вимикати «мерчант пише без третіх осіб» немає сенсу.
        missing = registry - grouped - {"SAFE"}
        self.assertEqual(missing, set(), f"категорії поза меню: {missing}")

    def test_every_grouped_category_has_a_title(self):
        for _, _, cats in CATEGORY_GROUPS:
            for category in cats:
                with self.subTest(category=category):
                    self.assertIn(category, CATEGORY_TITLES)


class TestSettingsSurviveTheRoundTrip(unittest.IsolatedAsyncioTestCase):
    """Показане на екрані має збігатися зі збереженим у базі."""

    USER = 42

    async def asyncSetUp(self):
        self._tmp = TemporaryDirectory()
        self.db = MerchantDB(Path(self._tmp.name) / "t.db")
        await self.db.start()
        await self.db.register_user(self.USER, self.USER)
        self.signal = builtin_registry().by_key("PAY_JAR")

    async def asyncTearDown(self):
        await self.db.stop()
        self._tmp.cleanup()

    async def test_changing_one_side_keeps_the_other(self):
        # У базі немає «часткового» налаштування: другий бік мусить прийти
        # з того, що діє зараз, інакше він мовчки з'їде на дефолт класу.
        from core.risk.policy import SignalPolicy

        resolver = await self.db.resolver_for(self.USER)
        before = resolver.for_signal(self.signal)

        updated = before.with_action(SIDE_BUY, BLOCK)
        await self.db.set_policy(self.USER, SignalPolicy(
            self.signal.key, enabled=updated.enabled,
            on_buy=updated.on_buy, on_sell=updated.on_sell,
        ))

        after = (await self.db.resolver_for(self.USER)).for_signal(self.signal)
        self.assertEqual(after.on_buy, BLOCK)
        self.assertEqual(after.on_sell, before.on_sell, "другий бік з'їхав")

    async def test_custom_signal_appears_in_the_menu(self):
        ok, _ = await self.db.save_user_signal(
            self.USER, "u_test", "Моє правило", ["якась фраза"],
        )
        self.assertTrue(ok)
        custom = await self.db.get_user_signals(self.USER)
        kb = risk_custom_kb([(s.key, s.title) for s in custom])
        self.assertIn("risk:sig:u_test", _all_callbacks(kb))


if __name__ == "__main__":
    unittest.main()
