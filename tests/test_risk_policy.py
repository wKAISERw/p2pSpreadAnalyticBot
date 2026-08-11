# tests/test_risk_policy.py
"""
Етап 3: рішення відокремлене від фактів.

Движок каже, ЩО побачив; політика каже, ЩО З ЦИМ РОБИТИ. Перше однакове
для всіх і рахується один раз, друге в кожного своє й не потребує жодного
виклику моделі — саме тому повна кастомізація не впирається ні в ключі до
LLM, ні в спільний кеш вердиктів.

Асиметрія купівлі й продажу тут не деталь, а причина існування модуля.
Купуючи, я лише обираю, кому платити. Продаючи — приймаю переказ на свою
картку невідомо від кого, і це вже мій фінмон. Один сигнал, дві різні ціни
помилки.
"""
from __future__ import annotations

import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from core.risk.policy import (
    BLOCK, IGNORE, NOTE, PROFILE_BALANCED, PROFILE_CAREFUL, PROFILE_RELAXED,
    SIDE_BUY, SIDE_SELL, WARN, PolicyResolver, SignalPolicy,
    apply_profile, default_policy, stricter,
)
from core.risk.registry import builtin_registry
from core.storage.merchant_db import MerchantDB


def _signal(key: str):
    return builtin_registry().by_key(key)


def _by_category(category: str):
    return next(s for s in builtin_registry().signals if s.category == category)


class TestBuySellAsymmetry(unittest.TestCase):
    """Те, з чого почався весь реворк."""

    def test_third_parties_cost_more_when_selling(self):
        policy = default_policy(_by_category("TRIANGLE"))
        self.assertEqual(policy.on_buy, WARN)
        self.assertEqual(policy.on_sell, BLOCK)

    def test_payment_target_costs_more_when_buying(self):
        # На банку переказую Я — і пояснювати банку рух теж мені.
        # Продаючи, я туди нічого не шлю.
        policy = default_policy(_signal("PAY_JAR"))
        self.assertEqual(policy.on_buy, WARN)
        self.assertEqual(policy.on_sell, NOTE)

    def test_scam_does_not_depend_on_direction(self):
        policy = default_policy(_signal("REVIEW_SCAM_CLAIM"))
        self.assertEqual(policy.on_buy, BLOCK)
        self.assertEqual(policy.on_sell, BLOCK)

    def test_action_picks_the_right_side(self):
        policy = SignalPolicy("x", on_buy=NOTE, on_sell=BLOCK)
        self.assertEqual(policy.action(SIDE_BUY), NOTE)
        self.assertEqual(policy.action(SIDE_SELL), BLOCK)

    def test_disabled_signal_is_silent_on_both_sides(self):
        policy = SignalPolicy("x", enabled=False, on_buy=BLOCK, on_sell=BLOCK)
        self.assertEqual(policy.action(SIDE_BUY), IGNORE)
        self.assertEqual(policy.action(SIDE_SELL), IGNORE)


class TestProfiles(unittest.TestCase):
    def test_careful_is_stricter(self):
        base = SignalPolicy("x", on_buy=WARN, on_sell=WARN)
        shifted = apply_profile(base, PROFILE_CAREFUL)
        self.assertEqual(shifted.on_buy, BLOCK)

    def test_relaxed_is_looser(self):
        base = SignalPolicy("x", on_buy=WARN, on_sell=WARN)
        shifted = apply_profile(base, PROFILE_RELAXED)
        self.assertEqual(shifted.on_buy, NOTE)

    def test_balanced_changes_nothing(self):
        base = SignalPolicy("x", on_buy=WARN, on_sell=BLOCK)
        self.assertEqual(apply_profile(base, PROFILE_BALANCED), base)

    def test_profile_does_not_revive_a_disabled_signal(self):
        # Людина вимкнула сигнал руками — зміна профілю не має його
        # воскресити.
        base = SignalPolicy("x", enabled=False, on_buy=IGNORE, on_sell=IGNORE)
        self.assertEqual(apply_profile(base, PROFILE_CAREFUL), base)

    def test_shift_does_not_run_off_the_ladder(self):
        top = SignalPolicy("x", on_buy=BLOCK, on_sell=BLOCK)
        self.assertEqual(apply_profile(top, PROFILE_CAREFUL).on_buy, BLOCK)
        bottom = SignalPolicy("y", on_buy=IGNORE, on_sell=IGNORE)
        self.assertEqual(apply_profile(bottom, PROFILE_RELAXED).on_buy, IGNORE)


class TestResolver(unittest.TestCase):
    def test_empty_settings_give_the_defaults(self):
        # Порожня політика = теперішня поведінка. Саме тому міграція нікому
        # нічого не ламає.
        signal = _by_category("TRIANGLE")
        self.assertEqual(
            PolicyResolver().for_signal(signal), default_policy(signal)
        )

    def test_point_setting_beats_the_profile(self):
        signal = _signal("PAY_JAR")
        override = SignalPolicy(signal.key, on_buy=IGNORE, on_sell=IGNORE)
        resolver = PolicyResolver(PROFILE_CAREFUL, {signal.key: override})
        self.assertEqual(resolver.action_for(signal, SIDE_BUY), IGNORE)

    def test_strictest_action_wins_the_verdict(self):
        resolver = PolicyResolver()
        signals = [_by_category("RECEIPT_REQUIRED"), _by_category("TRIANGLE")]
        action, causes = resolver.decide(signals, SIDE_SELL)
        self.assertEqual(action, BLOCK)
        self.assertEqual([s.category for s in causes], ["TRIANGLE"])

    def test_ignored_signals_do_not_reach_the_verdict(self):
        signal = _signal("PAY_JAR")
        resolver = PolicyResolver(overrides={signal.key: SignalPolicy(signal.key, enabled=False)})
        self.assertEqual(resolver.decide([signal], SIDE_BUY)[0], IGNORE)

    def test_stricter_helper(self):
        self.assertEqual(stricter(NOTE, BLOCK), BLOCK)
        self.assertEqual(stricter(WARN, IGNORE), WARN)


class TestStorage(unittest.IsolatedAsyncioTestCase):
    USER = 1

    async def asyncSetUp(self):
        self._tmp = TemporaryDirectory()
        self.db = MerchantDB(Path(self._tmp.name) / "t.db")
        await self.db.start()
        await self.db.register_user(self.USER, self.USER)
        self.jar = _signal("PAY_JAR")

    async def asyncTearDown(self):
        await self.db.stop()
        self._tmp.cleanup()

    async def test_fresh_user_gets_defaults(self):
        resolver = await self.db.resolver_for(self.USER)
        self.assertEqual(resolver.for_signal(self.jar), default_policy(self.jar))

    async def test_profile_round_trip(self):
        self.assertTrue(await self.db.set_risk_profile(self.USER, PROFILE_CAREFUL))
        self.assertEqual(await self.db.get_risk_profile(self.USER), PROFILE_CAREFUL)

    async def test_unknown_profile_is_rejected(self):
        self.assertFalse(await self.db.set_risk_profile(self.USER, "хакерський"))
        self.assertEqual(await self.db.get_risk_profile(self.USER), PROFILE_BALANCED)

    async def test_policy_round_trip(self):
        await self.db.set_policy(self.USER, SignalPolicy(self.jar.key, on_buy=BLOCK, on_sell=BLOCK))
        resolver = await self.db.resolver_for(self.USER)
        self.assertEqual(resolver.action_for(self.jar, SIDE_BUY), BLOCK)

    async def test_unknown_action_is_not_stored(self):
        ok = await self.db.set_policy(self.USER, SignalPolicy(self.jar.key, on_buy="вибухнути"))
        self.assertFalse(ok)
        self.assertEqual(await self.db.get_policies(self.USER), {})

    async def test_reset_returns_to_the_default_not_to_a_snapshot(self):
        # Видаляємо рядок, а не пишемо дефолтні значення: інакше зміна
        # дефолту в коді не дійшла б до тих, хто колись «скинув».
        await self.db.set_policy(self.USER, SignalPolicy(self.jar.key, on_buy=BLOCK))
        self.assertTrue(await self.db.reset_policy(self.USER, self.jar.key))
        self.assertEqual(await self.db.get_policies(self.USER), {})

    async def test_users_do_not_see_each_others_settings(self):
        await self.db.register_user(2, 2)
        await self.db.set_policy(self.USER, SignalPolicy(self.jar.key, on_buy=BLOCK))
        self.assertEqual(await self.db.get_policies(2), {})


class TestUserSignals(unittest.IsolatedAsyncioTestCase):
    USER = 7

    async def asyncSetUp(self):
        self._tmp = TemporaryDirectory()
        self.db = MerchantDB(Path(self._tmp.name) / "t.db")
        await self.db.start()
        await self.db.register_user(self.USER, self.USER)

    async def asyncTearDown(self):
        await self.db.stop()
        self._tmp.cleanup()

    async def test_own_signal_compiles_and_matches(self):
        ok, _ = await self.db.save_user_signal(
            self.USER, "my_zbir", "Збір в А-банку",
            ["накопичувальний збір", "а-банк збір"], category="PAYMENT_TARGET",
        )
        self.assertTrue(ok)

        signals = await self.db.get_user_signals(self.USER)
        self.assertEqual(len(signals), 1)
        self.assertTrue(signals[0].pattern.search("оплата на накопичувальний збір"))
        self.assertEqual(signals[0].owner, f"user:{self.USER}")

    async def test_signal_without_phrases_is_refused(self):
        # Патерн із порожнього списку збігався б із будь-чим.
        ok, msg = await self.db.save_user_signal(self.USER, "empty", "Порожній", [])
        self.assertFalse(ok)
        self.assertIn("фраз", msg)

    async def test_whitespace_only_phrases_are_refused(self):
        ok, _ = await self.db.save_user_signal(self.USER, "blank", "Пробіли", ["   ", ""])
        self.assertFalse(ok)

    async def test_deleting_a_signal_takes_its_policy_along(self):
        await self.db.save_user_signal(self.USER, "tmp", "Тимчасовий", ["фраза"])
        await self.db.set_policy(self.USER, SignalPolicy("tmp", on_buy=BLOCK))
        self.assertTrue(await self.db.delete_user_signal(self.USER, "tmp"))
        self.assertEqual(await self.db.get_policies(self.USER), {})

    async def test_broken_row_does_not_kill_the_rest(self):
        # Одне криве правило не має позбавляти людину решти захисту.
        await self.db.save_user_signal(self.USER, "good", "Робочий", ["фраза"])
        await self.db._db.execute(
            "INSERT INTO risk_user_signals (user_id, key, category, title, phrases_json, "
            "negations_json, layer, weight, scope, why, enabled, created_at) "
            "VALUES (?, 'broken', 'X', 'Зіпсований', '{не json', '[]', 'soft', 10, 'terms', '', 1, 0)",
            (self.USER,),
        )
        await self.db._db.commit()

        signals = await self.db.get_user_signals(self.USER)
        self.assertEqual([s.key for s in signals], ["good"])


if __name__ == "__main__":
    unittest.main()
