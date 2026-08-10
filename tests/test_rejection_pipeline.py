"""
tests/test_rejection_pipeline.py

Стик «сканер → база»: чи доїжджають причини відмов до card_rejection_log.

Цей шов покритий не був. Кожна ланка мала свій тест — движок віддає коди,
`scan()` віддає відмови, `log_rejections()` пише рядки — а те, що
`process_taker_path` реально їх з'єднує, не перевіряв ніхто. Коли
`/card_rejections` показав порожньо, відрізнити «нічого не відкидалось» від
«запис не доходить» було нічим.
"""
import sys
import time
import uuid
import asyncio
from decimal import Decimal
from pathlib import Path
from unittest.mock import MagicMock, patch

sys.path.insert(0, str(Path(__file__).parent.parent))

import pytest

from core.engine import rejection_codes as rc
from core.engine import scanner_helpers
from core.engine.taker_scanner import TakerScanner
from core.storage.merchant_db import MerchantDB
from exchanges.base import Order


TEST_USER_ID = 33301


@pytest.fixture
def event_loop():
    loop = asyncio.new_event_loop()
    yield loop
    loop.close()


@pytest.fixture
async def db(tmp_path):
    merchant_db = MerchantDB(db_path=tmp_path / "test_pipeline.db")
    await merchant_db.start()
    yield merchant_db
    await merchant_db.stop()


async def _add_card(db: MerchantDB, balance: float, bank: str = "monobank") -> None:
    await db._db.execute(
        "INSERT OR IGNORE INTO user_card_settings "
        "(user_id, card_module_mode, enable_in_single_modes) VALUES (?, 'full', 1)",
        (TEST_USER_ID,),
    )
    await db._db.commit()
    await db.add_card({
        "id": str(uuid.uuid4()), "owner_id": TEST_USER_ID, "bank_name": bank,
        "last_four": "1111", "label": "T", "is_own": 1, "balance": balance,
        "status": "active", "cooldown_until": 0, "last_monthly_reset": time.time(),
        "created_at": time.time(), "last_tx_timestamp": 0, "is_warmed_up": 1,
    })


def _order(oid: str = "o1", price: float = 44.0) -> Order:
    return Order(
        id=oid, price=Decimal(str(price)), available_amount=Decimal("100000"),
        min_limit=Decimal("1000"), max_limit=Decimal("60000"),
        merchant_id=f"m{oid}", merchant_name=f"M{oid}",
        month_order_count=500, finish_rate_pct=99.0, exchange="Binance",
        bank_codes=["43"],
    )


def _user(**over) -> dict:
    base = {
        "user_id": TEST_USER_ID, "chat_id": TEST_USER_ID,
        "scanner_mode": "TAKER_BUY", "scanner_modes": ["TAKER_BUY"],
        "bank_codes": ["43"], "taker_buy_amount": 500.0,
        "taker_buy_price_strategy": "any", "taker_buy_speed": "ANY",
        "buy_balance_mode": "MANUAL_STRICT", "capital": 0.0,
    }
    base.update(over)
    return base


async def _run_path(db, user, orders):
    notifier = MagicMock()

    async def _noop(*a, **kw):
        return None

    notifier.send_plain = _noop
    notifier.send_taker_to_user = _noop

    dedup = MagicMock()
    dedup.seen.return_value = False

    with patch.object(scanner_helpers, "is_muted", return_value=False), \
         patch.object(scanner_helpers, "spawn", lambda coro, *a, **kw: coro.close()):
        await scanner_helpers.process_taker_path(
            [user], {"43": orders}, {}, TakerScanner(db), dedup, notifier,
        )


@pytest.mark.asyncio
async def test_rejections_reach_the_database(db):
    await _add_card(db, balance=100.0)

    await _run_path(db, _user(), [_order()])

    stats = await db.get_rejection_stats(TEST_USER_ID, days=7)
    assert stats["total"] == 1
    assert stats["codes"][0]["code"] == rc.INSUFFICIENT_BALANCE
    assert stats["codes"][0]["avg_shortfall"] > 0


@pytest.mark.asyncio
async def test_scaled_orders_reach_the_database_too(db):
    """
    Прохід, де автоскейл ужав обсяг, лишав у статистиці порожнечу — хоча
    саме він і показує, чого коштує стеля одного банку.
    """
    await _add_card(db, balance=21297.80)

    await _run_path(
        db,
        _user(taker_buy_amount=700.0, buy_balance_mode="AUTO_SCALE",
              buy_auto_scale_down=1),
        [_order("o1", 44.28)],
    )

    stats = await db.get_rejection_stats(TEST_USER_ID, days=7)
    assert stats["total"] == 0, "ордер пройшов — це не відмова"
    assert stats["observed_total"] == 1
    assert stats["observations"][0]["code"] == rc.VOLUME_SCALED_DOWN


@pytest.mark.asyncio
async def test_nothing_is_written_when_everything_fits(db):
    """Порожній журнал має означати «усе гаразд», а не «запис не доходить»."""
    await _add_card(db, balance=100000.0)

    await _run_path(db, _user(taker_buy_amount=100.0), [_order()])

    stats = await db.get_rejection_stats(TEST_USER_ID, days=7)
    assert stats["total"] == 0
    assert stats["observed_total"] == 0


@pytest.mark.asyncio
async def test_repeated_cycles_do_not_multiply_the_record(db):
    """Сканер ходить по стакану щохвилини — журнал не має рости від цього."""
    await _add_card(db, balance=100.0)

    for _ in range(4):
        await _run_path(db, _user(), [_order()])

    assert (await db.get_rejection_stats(TEST_USER_ID, days=7))["total"] == 1
