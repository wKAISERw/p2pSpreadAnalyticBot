"""
tests/test_taker_fees.py

PLAN_CARD_MATCHING.md 7.6, пункт 3 — калькулятор комісій у тейкері.

`get_calculator` викликався рівно з одного місця, `core/engine/cross_matcher`,
тобто лише в спред-режимі. А банківський переказ фіату відбувається саме в
тейкерських режимах: там комісія завжди дорівнювала нулю. При спреді 0.5–1%
комісія А-Банку 2% з'їдає весь профіт, і побачити це було нізвідки.
"""
import sys
from decimal import Decimal
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

import pytest

from bot.taker_builder import _transfer_fee
from exchanges.base import Order


CHAT_ID = 4242


class _FakeDb:
    def __init__(self, taker_buy_amount: float = 0.0):
        self._amount = taker_buy_amount

    async def get_user_by_id(self, user_id):
        return {"user_id": user_id, "taker_buy_amount": self._amount}


class _FakeNotifier:
    def __init__(self, taker_buy_amount: float = 0.0):
        self._db = _FakeDb(taker_buy_amount)
        self._chat_id = CHAT_ID


def _order(bank_code: str, price: float = 44.0,
           min_limit: float = 1000.0, max_limit: float = 500_000.0) -> Order:
    return Order(
        id="o1", price=Decimal(str(price)),
        available_amount=Decimal("100000"),
        min_limit=Decimal(str(min_limit)), max_limit=Decimal(str(max_limit)),
        merchant_id="m1", merchant_name="Merchant",
        month_order_count=500, finish_rate_pct=99.0, exchange="Binance",
        bank_codes=[bank_code],
    )


@pytest.mark.asyncio
async def test_sell_has_no_transfer_fee():
    """У TAKER_SELL фіат відправляє мерчант — комісію свого банку платить він."""
    fee = await _transfer_fee(_FakeNotifier(500), CHAT_ID, _order("99"), is_buy=False)
    assert fee is None


@pytest.mark.asyncio
async def test_monobank_is_free_domestically():
    """У коді стояло 0.5% крос-банк, у довіднику — 0% по Україні."""
    fee = await _transfer_fee(_FakeNotifier(500), CHAT_ID, _order("43"), is_buy=True)
    assert fee is None


@pytest.mark.asyncio
async def test_privatbank_internal_transfer_is_free():
    """
    У тейкері переказ завжди йде на той самий банк — движок добирає картку
    рівно того банку, який приймає мерчант. Тож комісія «між банками» тут
    не виникає.
    """
    fee = await _transfer_fee(_FakeNotifier(500), CHAT_ID, _order("14"), is_buy=True)
    assert fee is None


@pytest.mark.asyncio
async def test_oschadbank_fee_shows_up_and_raises_effective_price():
    notifier = _FakeNotifier(taker_buy_amount=500)  # 500 × 44 = 22 000 ₴
    fee = await _transfer_fee(notifier, CHAT_ID, _order("99"), is_buy=True)

    assert fee is not None
    fee_uah, desc, eff_price = fee
    assert fee_uah == pytest.approx(22_000 * 0.01 + 5.0)
    assert "Ощадбанк" in desc
    assert eff_price > 44.0


@pytest.mark.asyncio
async def test_threshold_makes_small_transfers_free():
    """Sense: до 20к — 0%. Раніше поріг був описаний у коментарі й ігнорувався."""
    small = await _transfer_fee(_FakeNotifier(300), CHAT_ID, _order("328"), is_buy=True)
    assert small is None, "300 × 44 = 13 200 ₴ — у межах безкоштовного ліміту"

    big = await _transfer_fee(_FakeNotifier(700), CHAT_ID, _order("328"), is_buy=True)
    assert big is not None
    assert big[0] == pytest.approx(700 * 44 * 0.01 + 5.0)


@pytest.mark.asyncio
async def test_amount_comes_from_user_volume_not_order_minimum():
    """
    Комісія з порогом залежить від суми прямо, тож рахувати її від мінімалки
    ордера означало б занижувати: користувач відправляє свій обсяг, а не
    нижню межу мерчанта.
    """
    order = _order("328", min_limit=1000.0)

    by_minimum = await _transfer_fee(_FakeNotifier(0), CHAT_ID, order, is_buy=True)
    assert by_minimum is None, "1 000 ₴ — нижче порогу Sense"

    by_volume = await _transfer_fee(_FakeNotifier(700), CHAT_ID, order, is_buy=True)
    assert by_volume is not None


@pytest.mark.asyncio
async def test_amount_is_clamped_to_order_limits():
    """Більше за max_limit мерчанта відправити не вийде — і комісію теж."""
    order = _order("99", min_limit=1000.0, max_limit=10_000.0)
    fee = await _transfer_fee(_FakeNotifier(700), CHAT_ID, order, is_buy=True)

    assert fee is not None
    # 700 × 44 = 30 800 ₴, але стеля ордера 10 000 ₴
    assert fee[0] == pytest.approx(10_000 * 0.01 + 5.0)


@pytest.mark.asyncio
async def test_missing_user_row_does_not_break_the_alert():
    class _NoUserDb:
        async def get_user_by_id(self, user_id):
            return None

    notifier = _FakeNotifier()
    notifier._db = _NoUserDb()

    fee = await _transfer_fee(notifier, CHAT_ID, _order("99", min_limit=30_000.0), is_buy=True)
    assert fee is not None
    assert fee[0] == pytest.approx(30_000 * 0.01 + 5.0)
