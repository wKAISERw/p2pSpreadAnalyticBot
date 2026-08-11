# tests/test_personal_signals_reach_decision.py
"""
Персональні правила доходять до рішення — на живому тексті, не на ключі.

Дві діри, які тут закриваються, мали спільну властивість: усі тести були
зелені, бо кожен підставляв `risk_signals=["PAY_JAR"]` руками. Ніхто не
перевіряв, чи движок узагалі покладе цей ключ в ордер із тексту умов.

**Перша.** `order.risk_signals` збирався з `matches`, відфільтрованих по
`weight > 0`. А `PAY_JAR` і `PAY_BUSINESS` мають вагу 0 — вони не
підвищують ризик, вони називають факт («платіж іде на банку»). Тобто
налаштування «ховати банки» діяло на ключ, якого в ордері не було ніколи.
Це рівно та скарга, з якої почався реворк: «банки все одно прилітають».

**Друга.** Конфігуратор дозволяв додати власний сигнал, і той ніде не
спрацьовував: спільний прохід движка знає лише вбудований реєстр. Власні
правила тепер прикладаються до тексту на боці рішення — там, де контекст
персональний за визначенням, і де вони не можуть вплинути на те, що
побачать інші.
"""
from __future__ import annotations

import unittest

from core.analysis.regex_analyzer import analyze as regex_analyze
from core.engine.risk_decision import decide
from core.risk.policy import BLOCK, IGNORE, SIDE_BUY, SIDE_SELL, PolicyResolver, SignalPolicy
from core.risk.signals import LAYER_SOFT, SCOPE_REVIEWS, SCOPE_TERMS, Signal, compile_phrases
from exchanges.base import Order


def _order(terms: str = "", reviews: list[str] | None = None, side: str = "buy") -> Order:
    order = Order(
        id="1", price=45.0, available_amount=100.0,
        min_limit=1000.0, max_limit=50000.0,
        merchant_id="m1", merchant_name="Тест",
        month_order_count=100, finish_rate_pct=99.0,
        exchange="Binance", trade_terms=terms, side=side,
    )
    # Рівно те, що робить движок: ключі з тексту, а не з голови.
    order.risk_signals = list(regex_analyze(terms, 99.0, 100, True).signal_keys)
    order.review_texts = list(reviews or [])
    return order


def _signal(key: str, phrases: list[str], scope: str = SCOPE_TERMS) -> Signal:
    return Signal(
        key=key, category="CUSTOM", title=f"Правило {key}",
        pattern=compile_phrases(phrases), layer=LAYER_SOFT, weight=30,
        scope=scope, owner="user:1",
    )


class TestZeroWeightSignalsReachPolicy(unittest.TestCase):
    """Банка й ФОП: вага 0, але саме їх людина хоче ховати."""

    def test_jar_key_lands_in_the_order(self):
        self.assertIn("PAY_JAR", _order("кидаю на монобанку").risk_signals)

    def test_business_key_lands_in_the_order(self):
        self.assertIn("PAY_BUSINESS", _order("оплата на фоп рахунок").risk_signals)

    def test_jar_is_hidden_when_the_user_asked_to_hide_it(self):
        resolver = PolicyResolver(
            overrides={"PAY_JAR": SignalPolicy("PAY_JAR", on_buy=BLOCK, on_sell=BLOCK)}
        )
        decision = decide(_order("кидаю на монобанку"), resolver, SIDE_BUY)
        self.assertTrue(decision.hide)

    def test_jar_is_shown_when_the_user_did_not_ask(self):
        decision = decide(_order("кидаю на монобанку"), PolicyResolver(), SIDE_BUY)
        self.assertFalse(decision.hide)

    def test_safe_signals_never_become_a_reason_to_hide(self):
        # «без третіх осіб» — це SAFE. Ховати за нього означало б карати
        # мерчанта за те, що він написав правильну умову.
        order = _order("тільки своя карта, без третіх осіб")
        resolver = PolicyResolver(profile="paranoid")
        self.assertEqual(decide(order, resolver, SIDE_BUY).action, IGNORE)


class TestCustomSignalsWork(unittest.TestCase):
    def test_custom_terms_signal_fires_on_real_text(self):
        resolver = PolicyResolver(
            overrides={"MY_RULE": SignalPolicy("MY_RULE", on_buy=BLOCK, on_sell=BLOCK)},
            signals=[_signal("MY_RULE", ["курс фіксований"])],
        )
        decision = decide(_order("курс фіксований, торг недоречний"), resolver, SIDE_BUY)
        self.assertTrue(decision.hide)
        self.assertIn("MY_RULE", decision.signals)

    def test_custom_signal_does_not_fire_on_other_text(self):
        resolver = PolicyResolver(
            overrides={"MY_RULE": SignalPolicy("MY_RULE", on_buy=BLOCK, on_sell=BLOCK)},
            signals=[_signal("MY_RULE", ["курс фіксований"])],
        )
        self.assertFalse(decide(_order("оплата 15 хв"), resolver, SIDE_BUY).hide)

    def test_review_scope_signal_reads_review_texts_not_terms(self):
        resolver = PolicyResolver(
            overrides={"SCAM": SignalPolicy("SCAM", on_buy=BLOCK, on_sell=BLOCK)},
            signals=[_signal("SCAM", ["кинув"], scope=SCOPE_REVIEWS)],
        )
        order = _order("чесні умови", reviews=["кинув на 5000 грн, не відповідає"])
        self.assertTrue(decide(order, resolver, SIDE_BUY).hide)

    def test_review_word_in_terms_does_not_trigger_a_review_rule(self):
        # Те саме слово в умовах означає протилежне: там мерчант пише про
        # себе, а не про нього. Область сигналу — не формальність.
        resolver = PolicyResolver(
            overrides={"SCAM": SignalPolicy("SCAM", on_buy=BLOCK, on_sell=BLOCK)},
            signals=[_signal("SCAM", ["кинув"], scope=SCOPE_REVIEWS)],
        )
        order = _order("ніколи нікого не кинув, працюю чесно")
        self.assertFalse(decide(order, resolver, SIDE_BUY).hide)

    def test_review_texts_as_a_string_do_not_become_letters(self):
        # Ордер їздить через серіалізацію в базу й назад, і поле може
        # повернутись рядком. `"\n".join` на рядку не падає — він мовчки
        # склеює його ПОСИМВОЛЬНО, і правило спрацьовувало б на випадкових
        # збігах літер.
        resolver = PolicyResolver(
            overrides={"SCAM": SignalPolicy("SCAM", on_buy=BLOCK, on_sell=BLOCK)},
            signals=[_signal("SCAM", ["кинув"], scope=SCOPE_REVIEWS)],
        )
        order = _order("чесні умови")
        order.review_texts = "кинув на 5000 грн"      # не список
        self.assertTrue(decide(order, resolver, SIDE_BUY).hide)

    def test_custom_signal_keeps_the_asymmetry(self):
        resolver = PolicyResolver(
            overrides={"MY_RULE": SignalPolicy("MY_RULE", on_buy=IGNORE, on_sell=BLOCK)},
            signals=[_signal("MY_RULE", ["приймаю переказ"])],
        )
        order = _order("приймаю переказ будь-яким способом")
        self.assertFalse(decide(order, resolver, SIDE_BUY).hide)
        self.assertTrue(decide(order, resolver, SIDE_SELL).hide)

    def test_no_custom_signals_means_no_second_pass(self):
        # Найчастіший випадок — у людини власних правил немає. Прогін тексту
        # вдруге тут був би чистою витратою на кожному ордері кожного циклу.
        resolver = PolicyResolver()
        self.assertFalse(resolver.has_custom)
        self.assertEqual(decide(_order("оплата 15 хв"), resolver, SIDE_BUY).action, IGNORE)

    def test_custom_registry_is_built_once(self):
        resolver = PolicyResolver(signals=[_signal("MY_RULE", ["фраза"])])
        self.assertIs(resolver.custom_registry(), resolver.custom_registry())


class TestCustomSignalsStayPersonal(unittest.TestCase):
    """Головна межа: чуже правило не міняє того, що бачать інші."""

    def test_builtin_registry_is_untouched(self):
        from core.risk.registry import builtin_registry

        before = {s.key for s in builtin_registry().signals}
        resolver = PolicyResolver(signals=[_signal("MY_RULE", ["курс фіксований"])])
        decide(_order("курс фіксований"), resolver, SIDE_BUY)
        self.assertEqual(before, {s.key for s in builtin_registry().signals})

    def test_another_user_sees_nothing(self):
        mine = PolicyResolver(
            overrides={"MY_RULE": SignalPolicy("MY_RULE", on_buy=BLOCK, on_sell=BLOCK)},
            signals=[_signal("MY_RULE", ["курс фіксований"])],
        )
        theirs = PolicyResolver()
        order = _order("курс фіксований")
        self.assertTrue(decide(order, mine, SIDE_BUY).hide)
        self.assertFalse(decide(order, theirs, SIDE_BUY).hide)

    def test_order_signals_are_not_mutated_by_a_personal_pass(self):
        # Спільний об'єкт ордера ходить по всіх користувачах підряд.
        # Дописати в нього чийсь ключ означало б протекти правило далі.
        resolver = PolicyResolver(signals=[_signal("MY_RULE", ["курс фіксований"])])
        order = _order("курс фіксований")
        before = list(order.risk_signals)
        decide(order, resolver, SIDE_BUY)
        self.assertEqual(before, order.risk_signals)


if __name__ == "__main__":
    unittest.main()
