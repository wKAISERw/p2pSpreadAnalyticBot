"""
tests/test_autoscale_throttle_and_capital.py

Регресія на два дефекти, які виявив реальний лог користувача 09.08.2026.

1. Повідомлення про масштабування прийшло двічі за 65 хвилин, хоча ситуація
   не змінилась: на картках ті самі 21 297.80 ₴, обсяг ті самі 700 USDT.
   Різнились лише похідні 480.98 і 481.20 USDT — бо курс ордера зрушив на
   дві копійки. Тротлінг був прив'язаний до результату обчислення, а не до
   ситуації.

2. Слово «Капітал» діставалось не тій цифрі. Дашборд показував суму по всіх
   картках (31 122.8 ₴), меню фільтрів — максимум по одному банку
   (21 297.8 ₴), і обидва підписували це однаково.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

import pytest

from bot.formatters import format_capital


USER_ID = 99901


# ═══════════════════════════════════════════════════════════════
# 1. Тротлінг за ситуацією, а не за похідною цифрою
# ═══════════════════════════════════════════════════════════════

@pytest.fixture
def notice_spy(monkeypatch):
    """Ловить повідомлення замість надсилання в Telegram."""
    import core.engine.taker_scanner as ts
    import bot.handlers.core as bot_core

    sent = []

    class _FakeBot:
        async def send_message(self, chat_id, text, parse_mode=None, **kw):
            sent.append(text)

    monkeypatch.setattr(bot_core, "_bot", _FakeBot(), raising=False)
    ts._last_autoscale_notice.clear()
    yield sent
    ts._last_autoscale_notice.clear()


@pytest.mark.asyncio
async def test_price_drift_does_not_retrigger_the_notice(notice_spy):
    """
    Той самий баланс, той самий обсяг, курс зрушив на дві копійки —
    це не нова подія.
    """
    from core.engine.taker_scanner import notify_buy_autoscale

    user = {"user_id": USER_ID}

    # 8:57 — курс 44.28 → 480.98 USDT
    await notify_buy_autoscale(user, 21297.80, 700.0, 480.98, 44.28, is_down=True)
    # 10:02 — курс 44.26 → 481.20 USDT
    await notify_buy_autoscale(user, 21297.80, 700.0, 481.20, 44.26, is_down=True)

    assert len(notice_spy) == 1, "друге повідомлення описує ту саму ситуацію"


@pytest.mark.asyncio
async def test_every_order_in_the_book_does_not_produce_its_own_notice(notice_spy):
    """
    Ефективна сума рахується від ціни КОЖНОГО ордера, тож у стакані з
    десятьма ордерами старий ключ давав десять різних значень — і десять
    повідомлень за один прохід сканера.
    """
    from core.engine.taker_scanner import notify_buy_autoscale

    user = {"user_id": USER_ID}
    for price in (44.10, 44.15, 44.20, 44.28, 44.31, 44.40):
        await notify_buy_autoscale(
            user, 21297.80, 700.0, round(21297.80 / price, 2), price, is_down=True
        )

    assert len(notice_spy) == 1


@pytest.mark.asyncio
async def test_material_balance_change_is_a_new_situation(notice_spy):
    """А от коли баланс справді змінився — сказати варто."""
    from core.engine.taker_scanner import notify_buy_autoscale

    user = {"user_id": USER_ID}
    await notify_buy_autoscale(user, 21297.80, 700.0, 480.98, 44.28, is_down=True)
    await notify_buy_autoscale(user, 12000.00, 700.0, 271.00, 44.28, is_down=True)

    assert len(notice_spy) == 2


@pytest.mark.asyncio
async def test_tiny_balance_drift_is_not_news(notice_spy):
    """Кілька гривень на картці — не привід повторювати повідомлення."""
    from core.engine.taker_scanner import notify_buy_autoscale

    user = {"user_id": USER_ID}
    await notify_buy_autoscale(user, 21297.80, 700.0, 480.98, 44.28, is_down=True)
    await notify_buy_autoscale(user, 21309.10, 700.0, 481.24, 44.28, is_down=True)

    assert len(notice_spy) == 1


@pytest.mark.asyncio
async def test_recovery_notice_is_separate_from_the_drop(notice_spy):
    from core.engine.taker_scanner import notify_buy_autoscale

    user = {"user_id": USER_ID}
    await notify_buy_autoscale(user, 21297.80, 700.0, 480.98, 44.28, is_down=True)
    await notify_buy_autoscale(user, 21297.80, 700.0, 700.00, 44.28, is_down=False)

    assert len(notice_spy) == 2
    assert "не вистачає" in notice_spy[0]
    assert "відновлено" in notice_spy[1]


@pytest.mark.asyncio
async def test_notice_keeps_the_desired_amount_visible(notice_spy):
    """Головне, заради чого робився етап 1: 700 USDT нікуди не діваються."""
    from core.engine.taker_scanner import notify_buy_autoscale

    await notify_buy_autoscale({"user_id": USER_ID}, 21297.80, 700.0, 480.98,
                               44.28, is_down=True)

    text = notice_spy[0]
    assert "700.00 USDT" in text
    assert "збережено" in text
    assert "Налаштування не змінено" in text


# ═══════════════════════════════════════════════════════════════
# 2. Капітал — це сума грошей
# ═══════════════════════════════════════════════════════════════

_BREAKDOWN = {
    "total": 31122.8, "usable": 21297.8, "best_bank": "monobank",
    "banks": {"monobank": 21297.8, "pumb": 5005.0, "sense": 4820.0},
}


def test_capital_is_the_total_not_the_biggest_bank():
    capital, trade_line = format_capital(_BREAKDOWN, "auto", 0.0, True)
    assert "31 122.8" in capital
    assert "21 297" not in capital


def test_trade_ceiling_goes_to_its_own_line():
    _, trade_line = format_capital(_BREAKDOWN, "auto", 0.0, True)
    assert "21 298" in trade_line
    assert "monobank" in trade_line
    assert "різних банках" in trade_line


def test_no_trade_line_when_all_money_is_in_one_bank():
    one_bank = {"total": 21297.8, "usable": 21297.8,
                "best_bank": "monobank", "banks": {}}
    capital, trade_line = format_capital(one_bank, "auto", 0.0, True)
    assert "21 297.8" in capital
    assert trade_line == ""


def test_manual_capital_below_bank_ceiling_needs_no_explanation():
    """
    Стелю задав сам користувач — пояснювати її фрагментацією банків було б
    називанням причиною того, що причиною не є.
    """
    _, trade_line = format_capital(_BREAKDOWN, "manual", 15000.0, True)
    assert trade_line == ""


def test_manual_capital_above_bank_ceiling_still_explains():
    capital, trade_line = format_capital(_BREAKDOWN, "manual", 40000.0, True)
    assert "40 000.0" in capital
    assert "на картках 31 123" in capital
    assert "21 298" in trade_line


def test_empty_wallet_shows_no_trade_line():
    empty = {"total": 0.0, "usable": 0.0, "best_bank": "", "banks": {}}
    capital, trade_line = format_capital(empty, "auto", 0.0, True)
    assert trade_line == ""
    assert "0.0" in capital
