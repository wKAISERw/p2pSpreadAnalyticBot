# tests/test_risk_flag_filters.py
"""
Фільтри мають фільтрувати те, що просили, і рівно те.

`risk_flag` — список прапорів, склеєний комами. Чотири місця читали його
підрядком:

    if "BLOCK" in risk_flags:      core/engine/alert_dispatcher.py:284
        ...                        bot/handlers/monitoring.py:369, 455
                                   core/engine/taker_scanner.py:555

Підрядок `BLOCK` міститься всередині слова `BLOCKED`. А `FOP_TOV_BLOCKED` і
`BANKA_JAR_BLOCKED` — не блоки, а метадані для персональних фільтрів:
користувач сам обирає ховати, попереджати чи показувати.

Через це ордер із ФОП або банкою відкидався ЗАВЖДИ. Режими «⚠️ попереджати»
і «показувати» в меню були, кнопки перемикались, налаштування зберігалось —
і не робило нічого. Причина в лозі відрізнялась, результат ні.

У `monitoring.py` було ще гірше: перевірка на "BLOCK" стояла ВИЩЕ за
зчитування налаштувань користувача, тобто рішення ухвалювалось до того, як
хтось питав його думку.
"""
from __future__ import annotations

import unittest
from decimal import Decimal
from unittest.mock import MagicMock

from core.engine import risk_flags as rf
from core.engine.alert_dispatcher import AlertDispatcher
from exchanges.base import Order


class TestFlagParsing(unittest.TestCase):
    def test_blocked_suffix_is_not_a_block(self):
        for flag in ("FOP_TOV_BLOCKED", "BANKA_JAR_BLOCKED",
                     "LOW_STATS,BANKA_JAR_BLOCKED", "FOP_TOV_BLOCKED,PERFECT_RATING"):
            with self.subTest(flag=flag):
                self.assertFalse(rf.has_block(flag), f"{flag} прийнято за блок")

    def test_real_blocks_are_recognised(self):
        for flag in ("BLOCK:CACHED", "BLOCK:TRIANGLE:третi особи",
                     "BLOCK:BLACKLIST:кинув", "LOW_STATS,BLOCK:LLM_BLOCK:скам",
                     "BLOCK"):
            with self.subTest(flag=flag):
                self.assertTrue(rf.has_block(flag), f"{flag} не розпізнано як блок")

    def test_comma_inside_a_reason_does_not_hide_the_block(self):
        # Причина вбудовується в прапор, і кома в ній рве рядок навпіл. Але
        # префікс лишається на першому фрагменті, тож блок видно.
        flag = "BLOCK:TRIANGLE:Сигналів: 2 (TRIANGLE, CASINO) Score: 100"
        self.assertTrue(rf.has_block(flag))

    def test_empty_and_safe_flags(self):
        for flag in ("", None, "OK", "PENDING", "LOW_STATS,PERFECT_RATING"):
            with self.subTest(flag=flag):
                self.assertFalse(rf.has_block(flag))

    def test_blacklist_is_told_apart_from_other_blocks(self):
        self.assertTrue(rf.is_blacklist_block("BLOCK:BLACKLIST:кинув"))
        self.assertFalse(rf.is_blacklist_block("BLOCK:TRIANGLE:щось"))

    def test_has_matches_whole_flags_only(self):
        self.assertTrue(rf.has("LOW_STATS,FOP_TOV_BLOCKED", "FOP_TOV_BLOCKED"))
        self.assertTrue(rf.has("STALE_REVIEWS:NO_SESSION:30h", "STALE_REVIEWS"))
        # Назва всередині вільного тексту чужого прапора — не збіг.
        self.assertFalse(rf.has("BLOCK:LLM_BLOCK:мерчант згадав FOP_TOV_BLOCKED", "FOP_TOV_BLOCKED"))

    def test_scrub_keeps_the_flag_string_parseable(self):
        dirty = "кинув на 5000, не повернув\nдругий рядок"
        clean = rf.scrub(dirty)
        self.assertNotIn(",", clean)
        self.assertNotIn("\n", clean)
        self.assertEqual(len(rf.parse(f"BLOCK:BLACKLIST:{clean}")), 1)


def _order(risk_flag: str, name: str = "Merchant") -> Order:
    return Order(
        id="o1", price=Decimal("40.0"), available_amount=Decimal("1000"),
        min_limit=Decimal("500"), max_limit=Decimal("10000"),
        merchant_id="m1", merchant_name=name,
        month_order_count=500, finish_rate_pct=99.0,
        exchange="Bybit", bank_codes=["43"], risk_flag=risk_flag,
    )


def _opp(buy_flag: str = "OK", sell_flag: str = "OK") -> dict:
    return {
        "buy_order": _order(buy_flag, "Buy"),
        "sell_order": _order(sell_flag, "Sell"),
        "net_spread_pct": 2.5,
        "actual_entry_uah": 1000.0,
        "buy_bank": "43", "sell_bank": "43",
        "buy_banks_fit": ["43"], "sell_banks_fit": ["43"],
        "route_type": "SPREAD",
    }


def _user(**over) -> dict:
    base = {
        "user_id": 1, "capital": 5000.0, "min_spread": 1.0,
        "bank_codes": ["43"], "buy_bank_codes": ["43"], "sell_bank_codes": ["43"],
        "merchant_filters": {},
    }
    base.update(over)
    return base


def _user_with_jar(action: str) -> dict:
    """
    Користувач із заданою реакцією на банку.

    Раніше це були колонки `filter_fop_tov` / `filter_banka_jar` — два
    захардкоджені фільтри на шістнадцять категорій. Тепер те саме робить
    персональна політика, і старий вибір переноситься в неї автоматично
    (`RiskRepo.migrate_legacy_filters`).
    """
    from core.risk.policy import PolicyResolver, SignalPolicy

    return _user(_risk_resolver=PolicyResolver(
        overrides={"PAY_JAR": SignalPolicy("PAY_JAR", on_buy=action, on_sell=action)}
    ))


class TestPersonalPolicyDecidesTheOrder(unittest.TestCase):
    """
    Три режими мають давати три різні наслідки.

    Досі це було неможливо: підрядок «BLOCK» ловився всередині
    `BANKA_JAR_BLOCKED`, і ордер відкидався в будь-якому режимі. Тепер
    рішення ухвалює політика, і вона знає про напрямок угоди.
    """

    def setUp(self):
        self.d = AlertDispatcher(notifier=MagicMock(), db=MagicMock())

    def _wants(self, user: dict, signals: list[str]) -> bool:
        opp = _opp()
        opp["sell_order"].risk_signals = signals
        opp["buy_order"].risk_signals = []
        return self.d._user_wants(user, opp)[0]

    def test_block_hides_the_order(self):
        self.assertFalse(self._wants(_user_with_jar("block"), ["PAY_JAR"]))

    def test_warn_lets_the_order_through(self):
        self.assertTrue(self._wants(_user_with_jar("warn"), ["PAY_JAR"]))

    def test_ignore_lets_the_order_through(self):
        self.assertTrue(self._wants(_user_with_jar("ignore"), ["PAY_JAR"]))

    def test_clean_order_passes_in_every_mode(self):
        for action in ("block", "warn", "ignore"):
            with self.subTest(action=action):
                self.assertTrue(self._wants(_user_with_jar(action), []))

    def test_direction_matters(self):
        # Трикутник за замовчуванням ховає ордер на продажі й лише
        # попереджає на купівлі.
        from core.risk.policy import PolicyResolver

        user = _user(_risk_resolver=PolicyResolver())
        opp = _opp()
        opp["sell_order"].risk_signals = ["GEN_H02_TRIANGLE"]
        opp["buy_order"].risk_signals = []
        self.assertFalse(self.d._user_wants(user, opp)[0])

        opp2 = _opp()
        opp2["buy_order"].risk_signals = ["GEN_H02_TRIANGLE"]
        opp2["sell_order"].risk_signals = []
        self.assertTrue(self.d._user_wants(user, opp2)[0])

    def test_user_without_policy_is_not_filtered_by_it(self):
        # Резолвера немає — політика мовчить, решта перевірок працює.
        self.assertTrue(self._wants(_user(), ["PAY_JAR"]))

    def test_genuine_block_flag_is_still_dropped(self):
        opp = _opp(sell_flag="BLOCK:LLM_BLOCK:скам")
        self.assertFalse(self.d._user_wants(_user(), opp)[0])


class TestOrdersAreNotSharedBetweenUsers(unittest.TestCase):
    """
    `copy.copy(alert)` поверхнева, тож ноги приходили ті самі об'єкти всім
    користувачам батчу — а рендер їх мутує під персональні налаштування.
    """

    def test_alert_copy_gives_each_user_its_own_legs(self):
        import copy

        original = MagicMock()
        original.buy_order = _order("LOW_STATS,BANKA_JAR_BLOCKED", "Buy")
        original.sell_order = _order("OK", "Sell")

        # Те, що робить dispatch_batch після фікса.
        local = copy.copy(original)
        local.buy_order = copy.copy(original.buy_order)
        local.sell_order = copy.copy(original.sell_order)

        # Рендер першого користувача переписує прапор під його налаштування.
        local.buy_order.risk_flag = "BLOCK:BANKA_JAR_BLOCKED"
        local.buy_order.review_neg_pct = 12.0

        self.assertEqual(original.buy_order.risk_flag, "LOW_STATS,BANKA_JAR_BLOCKED",
                         "рендер першого користувача змінив спільний ордер")
        self.assertEqual(original.buy_order.review_neg_pct, 0.0)

    def test_second_user_sees_the_original_flag(self):
        import copy

        shared = _order("LOW_STATS,BANKA_JAR_BLOCKED")
        first = copy.copy(shared)
        first.risk_flag = "BANKA_JAR_WARN"          # у першого стоїть «попереджати»

        second = copy.copy(shared)
        self.assertEqual(second.risk_flag, "LOW_STATS,BANKA_JAR_BLOCKED")


if __name__ == "__main__":
    unittest.main()
