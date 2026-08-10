"""
tests/test_limit_watch.py

Попередження про наближення до ліміту картки.

Перевірка на 80% у проєкті вже була — але в дашборді карток, тобто
спрацьовувала, лише якщо людина сама відкриє меню. Дізнатись, що картка
ось-ось упреться в стелю, можна було тільки здогадавшись піти подивитись.
Тепер вона спрацьовує в момент, коли поріг перетнуто.
"""
import sys
import time
import uuid
import asyncio
import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

import pytest

from core.engine.limit_watch import (
    WARN_RATIO, check_card_limits, reset_throttle, should_notify,
)
from core.storage.merchant_db import MerchantDB


TEST_USER_ID = 90901


@pytest.fixture
def event_loop():
    loop = asyncio.new_event_loop()
    yield loop
    loop.close()


@pytest.fixture
async def db(tmp_path):
    reset_throttle()
    merchant_db = MerchantDB(db_path=tmp_path / "test_limits.db")
    await merchant_db.start()
    yield merchant_db
    await merchant_db.stop()
    reset_throttle()


async def _card(db: MerchantDB, bank: str = "monobank", balance: float = 500000.0) -> str:
    card_id = str(uuid.uuid4())
    await db.add_card({
        "id": card_id, "owner_id": TEST_USER_ID, "bank_name": bank,
        "last_four": "4321", "label": "T", "is_own": 1, "balance": balance,
        "status": "active", "cooldown_until": 0,
        "last_monthly_reset": time.time(), "created_at": time.time(),
        "last_tx_timestamp": 0, "is_warmed_up": 1,
    })
    return card_id


@pytest.mark.asyncio
async def test_no_warning_below_the_threshold(db):
    card_id = await _card(db)
    await db.confirm_transaction(card_id, 10_000.0, "out", "work", source="test")

    assert await check_card_limits(db, card_id, "out") is None


@pytest.mark.asyncio
async def test_monthly_limit_trips_first(db):
    """
    У довіднику місячна межа менша за добову (Monobank 100к/міс проти
    150к/добу), тож упирається саме вона — а попереджав про неї досі ніхто.
    """
    card_id = await _card(db)
    await db.confirm_transaction(card_id, 85_000.0, "out", "work", source="test")

    warning = await check_card_limits(db, card_id, "out")
    assert warning is not None
    assert warning.period == "monthly"
    assert warning.limit == 100_000.0
    assert warning.ratio >= WARN_RATIO
    assert "85 000" in warning.render()
    assert "/set_bank_limits" in warning.render()


@pytest.mark.asyncio
async def test_unlimited_field_never_warns(db):
    card_id = await _card(db)
    await db.set_user_bank_limit(TEST_USER_ID, "monobank", "monthly_out_max", -1.0)
    await db.set_user_bank_limit(TEST_USER_ID, "monobank", "daily_out_max", -1.0)
    await db.confirm_transaction(card_id, 500_000.0, "out", "work", source="test")

    assert await check_card_limits(db, card_id, "out") is None


@pytest.mark.asyncio
async def test_in_and_out_are_watched_separately(db):
    """Мейкер приймає фіат, тейкер відправляє — напрямки не мають змішуватись."""
    card_id = await _card(db)
    await db.confirm_transaction(card_id, 85_000.0, "in", "work", source="test")

    assert await check_card_limits(db, card_id, "in") is not None
    assert await check_card_limits(db, card_id, "out") is None


@pytest.mark.asyncio
async def test_the_tighter_limit_wins(db):
    """
    Показуємо те попередження, чия межа зупинить наступну угоду першою, а
    не те, чий відсоток більший.
    """
    card_id = await _card(db)
    await db.set_user_bank_limit(TEST_USER_ID, "monobank", "daily_out_max", 20_000.0)
    await db.set_user_bank_limit(TEST_USER_ID, "monobank", "monthly_out_max", 100_000.0)
    await db.confirm_transaction(card_id, 18_000.0, "out", "work", source="test")

    warning = await check_card_limits(db, card_id, "out")
    assert warning.period == "daily", "до добової межі лишилось 2 000 ₴"


def test_throttle_is_once_per_day_per_card():
    from core.engine.limit_watch import LimitWarning

    reset_throttle()
    w = LimitWarning(card_id="c1", last_four="1111", bank="monobank",
                     direction="out", period="daily", used=90.0, limit=100.0)
    today = datetime.date.today().isoformat()

    assert should_notify(w, today) is True
    assert should_notify(w, today) is False, "та сама ситуація того ж дня"
    assert should_notify(w, "2099-01-01") is True, "новий день — нова доба ліміту"


def test_throttle_separates_direction_and_period():
    from core.engine.limit_watch import LimitWarning

    reset_throttle()
    today = datetime.date.today().isoformat()

    def _w(direction, period):
        return LimitWarning(card_id="c1", last_four="1111", bank="monobank",
                            direction=direction, period=period, used=90.0, limit=100.0)

    assert should_notify(_w("out", "daily"), today) is True
    assert should_notify(_w("out", "monthly"), today) is True
    assert should_notify(_w("in", "daily"), today) is True
    assert should_notify(_w("out", "daily"), today) is False


@pytest.mark.asyncio
async def test_transaction_still_recorded_when_notification_path_fails(db):
    """
    Сповіщення — побічна дія. Його збій не має відкочувати транзакцію, яка
    вже успішно записана.
    """
    import core.engine.limit_watch as lw

    async def _boom(*a, **kw):
        raise RuntimeError("bot is down")

    original = lw.check_card_limits
    lw.check_card_limits = _boom
    try:
        card_id = await _card(db)
        await db.confirm_transaction(card_id, 85_000.0, "out", "work", source="test")
    finally:
        lw.check_card_limits = original

    used = await db.get_monthly_used(card_id, "out")
    assert used == pytest.approx(85_000.0)
