# tests/test_risk_decision.py
"""
Етап 5, перша половина: рішення про ордер під конкретного користувача.

Місце, де сходяться дві половини реворку. Движок сказав, ЩО побачив;
користувач сказав, ЩО З ЦИМ РОБИТИ. Тут це зводиться в одне слово.

Найдорожча помилка, яку тут можна зробити, — переплутати систему координат
напрямку. `Order.side == "buy"` означає, що ордер із групи, де мерчант
ПРОДАЄ USDT, тобто людина купує й відправляє фіат. Переплутати це з точкою
зору мерчанта означає перевернути всю асиметрію догори дриґом: сигнал, який
мав ховати ордер на продажі, почав би ховати на купівлі.
"""
from __future__ import annotations

import unittest

from core.engine.risk_decision import Decision, decide, decide_pair, side_of
from core.risk.policy import (
    BLOCK, IGNORE, NOTE, PROFILE_CAREFUL, PROFILE_RELAXED, SIDE_BUY, SIDE_SELL,
    WARN, PolicyResolver, SignalPolicy,
)

TRIANGLE = "GEN_H02_TRIANGLE"
JAR = "PAY_JAR"
RECEIPT = "GEN_W01_RECEIPT_"


class _Order:
    def __init__(self, side: str = "buy", signals: list[str] | None = None):
        self.side = side
        self.risk_signals = signals or []


class TestSideMapping(unittest.TestCase):
    def test_scanner_side_maps_to_user_side(self):
        self.assertEqual(side_of(_Order("buy")), SIDE_BUY)
        self.assertEqual(side_of(_Order("sell")), SIDE_SELL)

    def test_missing_side_defaults_to_buy(self):
        # Невідомий напрямок трактуємо як купівлю: там дефолти м'якші, і
        # помилка не призведе до тихого приховування ордера.
        class _Bare:
            risk_signals = [TRIANGLE]

        self.assertEqual(side_of(_Bare()), SIDE_BUY)


class TestAsymmetryReachesTheDecision(unittest.TestCase):
    """Те, з чого почався реворк, — тепер у рішенні, а не лише в дефолтах."""

    def setUp(self):
        self.resolver = PolicyResolver()

    def test_third_parties_hide_the_order_only_when_selling(self):
        self.assertEqual(decide(_Order("buy", [TRIANGLE]), self.resolver).action, WARN)
        self.assertEqual(decide(_Order("sell", [TRIANGLE]), self.resolver).action, BLOCK)

    def test_jar_matters_more_when_buying(self):
        self.assertEqual(decide(_Order("buy", [JAR]), self.resolver).action, WARN)
        self.assertEqual(decide(_Order("sell", [JAR]), self.resolver).action, NOTE)

    def test_explicit_side_overrides_the_order_field(self):
        order = _Order("buy", [TRIANGLE])
        self.assertEqual(decide(order, self.resolver, SIDE_SELL).action, BLOCK)


class TestNoSignalsIsNotSafety(unittest.TestCase):
    def test_empty_signals_give_no_action(self):
        # «Нічого не спрацювало» — не «безпечно». Про те, чого ми не бачили,
        # каже risk_coverage, і це окрема розмова.
        self.assertEqual(decide(_Order("buy", []), PolicyResolver()).action, IGNORE)

    def test_unknown_signal_key_is_ignored_not_guessed(self):
        decision = decide(_Order("buy", ["НЕМАЄ_ТАКОГО"]), PolicyResolver())
        self.assertEqual(decision.action, IGNORE)
        self.assertEqual(decision.signals, [])

    def test_order_without_the_field_does_not_crash(self):
        class _Bare:
            side = "buy"

        self.assertEqual(decide(_Bare(), PolicyResolver()).action, IGNORE)


class TestPersonalSettingsWin(unittest.TestCase):
    def test_profile_shifts_the_decision(self):
        order = _Order("buy", [JAR])
        self.assertEqual(decide(order, PolicyResolver()).action, WARN)
        self.assertEqual(decide(order, PolicyResolver(PROFILE_CAREFUL)).action, BLOCK)
        self.assertEqual(decide(order, PolicyResolver(PROFILE_RELAXED)).action, NOTE)

    def test_point_setting_can_silence_a_signal(self):
        resolver = PolicyResolver(overrides={JAR: SignalPolicy(JAR, enabled=False)})
        self.assertEqual(decide(_Order("buy", [JAR]), resolver).action, IGNORE)

    def test_strictest_signal_decides(self):
        order = _Order("sell", [RECEIPT, TRIANGLE])
        decision = decide(order, PolicyResolver())
        self.assertEqual(decision.action, BLOCK)
        self.assertEqual(decision.signals, [TRIANGLE])


class TestPairDecision(unittest.TestCase):
    """Ноги зв'язки оцінюються В РІЗНИХ напрямках."""

    def setUp(self):
        self.resolver = PolicyResolver()

    def test_each_leg_is_judged_in_its_own_direction(self):
        # Той самий сигнал на обох ногах дає різні дії, і перемагає суворіша.
        decision = decide_pair(
            _Order("buy", [TRIANGLE]), _Order("sell", [TRIANGLE]), self.resolver
        )
        self.assertEqual(decision.action, BLOCK)
        self.assertTrue(any("продаж" in r for r in decision.reasons))
        self.assertFalse(any("купівля" in r for r in decision.reasons))

    def test_clean_pair_is_silent(self):
        decision = decide_pair(_Order("buy"), _Order("sell"), self.resolver)
        self.assertEqual(decision.action, IGNORE)
        self.assertEqual(decision.reasons, [])

    def test_reason_names_the_leg(self):
        decision = decide_pair(
            _Order("buy", [JAR]), _Order("sell", []), self.resolver
        )
        self.assertEqual(decision.action, WARN)
        self.assertEqual(decision.reasons, ["Банка / накопичувальний рахунок (купівля)"])

    def test_hide_and_warn_helpers(self):
        self.assertTrue(Decision(action=BLOCK).hide)
        self.assertFalse(Decision(action=WARN).hide)
        self.assertTrue(Decision(action=WARN).warn)


if __name__ == "__main__":
    unittest.main()
