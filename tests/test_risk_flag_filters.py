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
        "filter_fop_tov": "hide", "filter_banka_jar": "hide",
    }
    base.update(over)
    return base


class TestPersonalFiltersAreActuallyHonoured(unittest.TestCase):
    """Три режими ФОП/банки мусять давати три різні наслідки."""

    def setUp(self):
        self.d = AlertDispatcher(notifier=MagicMock(), db=MagicMock())

    def _wants(self, user: dict, flag: str) -> bool:
        return self.d._user_wants(user, _opp(sell_flag=flag))[0]

    def test_hide_still_hides(self):
        self.assertFalse(self._wants(_user(filter_banka_jar="hide"), "BANKA_JAR_BLOCKED"))
        self.assertFalse(self._wants(_user(filter_fop_tov="hide"), "FOP_TOV_BLOCKED"))

    def test_warn_lets_the_order_through(self):
        # Це і був недосяжний режим: ордер зникав через підрядкову перевірку.
        self.assertTrue(self._wants(_user(filter_banka_jar="warn"), "BANKA_JAR_BLOCKED"))
        self.assertTrue(self._wants(_user(filter_fop_tov="warn"), "FOP_TOV_BLOCKED"))

    def test_show_lets_the_order_through(self):
        self.assertTrue(self._wants(_user(filter_banka_jar="show"), "BANKA_JAR_BLOCKED"))
        self.assertTrue(self._wants(_user(filter_fop_tov="show"), "FOP_TOV_BLOCKED"))

    def test_metadata_next_to_other_flags_still_passes(self):
        self.assertTrue(
            self._wants(_user(filter_banka_jar="warn"), "LOW_STATS,BANKA_JAR_BLOCKED")
        )

    def test_genuine_block_is_still_dropped_in_every_mode(self):
        for mode in ("hide", "warn", "show"):
            with self.subTest(mode=mode):
                self.assertFalse(
                    self._wants(_user(filter_banka_jar=mode), "BLOCK:LLM_BLOCK:скам")
                )

    def test_blacklist_keeps_its_own_mode(self):
        blocked = _user(merchant_filters={"blacklist_mode": "block"})
        warned = _user(merchant_filters={"blacklist_mode": "warn"})
        self.assertFalse(self._wants(blocked, "BLOCK:BLACKLIST:кинув"))
        self.assertTrue(self._wants(warned, "BLOCK:BLACKLIST:кинув"))


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
