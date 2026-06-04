import sys
import time
import asyncio
import uuid
from pathlib import Path
from decimal import Decimal

sys.path.insert(0, str(Path(__file__).parent.parent))

import pytest
from core.storage.merchant_db import MerchantDB
from core.engine.taker_scanner import TakerScanner
from exchanges.base import Order


@pytest.fixture
def event_loop():
    loop = asyncio.new_event_loop()
    yield loop
    loop.close()


@pytest.fixture
async def db(tmp_path):
    db_path = tmp_path / "test_taker.db"
    merchant_db = MerchantDB(db_path=db_path)
    await merchant_db.start()
    yield merchant_db
    await merchant_db.stop()


TEST_USER_ID = 987654321


async def setup_card(db: MerchantDB, bank: str = "monobank", balance: float = 10000.0, cooldown_until: float = 0):
    await db._db.execute(
        "INSERT OR IGNORE INTO user_card_settings (user_id, card_module_mode, max_cards_per_order, enable_in_single_modes) VALUES (?, 'full', 3, 1)",
        (TEST_USER_ID,)
    )
    await db._db.execute(
        "INSERT OR IGNORE INTO user_bank_limits (user_id, bank_name) VALUES (?, ?)",
        (TEST_USER_ID, bank)
    )
    await db._db.commit()

    card_id = str(uuid.uuid4())
    card_data = {
        "id": card_id,
        "owner_id": TEST_USER_ID,
        "bank_name": bank,
        "last_four": "1234",
        "label": "Test",
        "is_own": 1,
        "balance": balance,
        "status": "active",
        "cooldown_until": cooldown_until,
        "last_monthly_reset": time.time(),
        "created_at": time.time(),
        "last_tx_timestamp": 0,
        "is_warmed_up": 1,
    }
    await db.add_card(card_data)
    return card_id


def make_test_order(id_val: str, price: float, min_limit: float, max_limit: float, is_verified: bool = False, bank_codes: list = None, account_age: int = 0, pos_rate: float = 0.0):
    return Order(
        id=id_val,
        price=Decimal(str(price)),
        available_amount=Decimal("1000.0"),
        min_limit=Decimal(str(min_limit)),
        max_limit=Decimal(str(max_limit)),
        merchant_id=f"merchant_{id_val}",
        merchant_name=f"Merchant {id_val}",
        month_order_count=100,
        finish_rate_pct=99.0,
        exchange="Binance",
        is_verified=is_verified,
        bank_codes=bank_codes or ["43"],
        account_age_days=account_age,
        positive_rate=pos_rate,
    )


def test_merchant_ok_verified_filter():
    scanner = TakerScanner()
    
    verified_order = make_test_order("o1", 40.0, 500, 10000, is_verified=True)
    unverified_order = make_test_order("o2", 40.0, 500, 10000, is_verified=False)

    # 1. verified_filter = all
    mf = {"verified_filter": "all"}
    assert scanner._merchant_ok(verified_order, mf, {}) is True
    assert scanner._merchant_ok(unverified_order, mf, {}) is True

    # 2. verified_filter = verified
    mf = {"verified_filter": "verified"}
    assert scanner._merchant_ok(verified_order, mf, {}) is True
    assert scanner._merchant_ok(unverified_order, mf, {}) is False

    # 3. verified_filter = unverified
    mf = {"verified_filter": "unverified"}
    assert scanner._merchant_ok(verified_order, mf, {}) is False
    assert scanner._merchant_ok(unverified_order, mf, {}) is True


def test_merchant_ok_account_age():
    scanner = TakerScanner()

    old_order = make_test_order("o1", 40.0, 500, 10000, account_age=60)
    new_order = make_test_order("o2", 40.0, 500, 10000, account_age=5)
    unknown_age_order = make_test_order("o3", 40.0, 500, 10000, account_age=0)

    mf = {"min_account_age_days": 30}
    assert scanner._merchant_ok(old_order, mf, {}) is True
    assert scanner._merchant_ok(new_order, mf, {}) is False
    # If age is unknown (0), it does not get filtered out because some exchanges do not support it
    assert scanner._merchant_ok(unknown_age_order, mf, {}) is True


def test_merchant_ok_positive_rate():
    scanner = TakerScanner()

    # Binance uses 0.0 - 1.0 format
    good_binance_order = make_test_order("o1", 40.0, 500, 10000, pos_rate=0.99)
    bad_binance_order = make_test_order("o2", 40.0, 500, 10000, pos_rate=0.95)

    # Other systems might use 0.0 - 100.0 format
    good_percent_order = make_test_order("o3", 40.0, 500, 10000, pos_rate=99.0)
    bad_percent_order = make_test_order("o4", 40.0, 500, 10000, pos_rate=95.0)

    mf = {"min_positive_rate": 98.0}
    assert scanner._merchant_ok(good_binance_order, mf, {}) is True
    assert scanner._merchant_ok(bad_binance_order, mf, {}) is False
    
    assert scanner._merchant_ok(good_percent_order, mf, {}) is True
    assert scanner._merchant_ok(bad_percent_order, mf, {}) is False


@pytest.mark.asyncio
async def test_card_prefiltering_and_matching(db):
    scanner = TakerScanner(db)
    
    # 1. No active cards configured -> filter should let it pass if card module off, but fail if active but no cards
    # Ensure card settings exist and enable_in_single_modes is off
    await db._db.execute(
        "INSERT OR IGNORE INTO user_card_settings (user_id, card_module_mode, max_cards_per_order, enable_in_single_modes) VALUES (?, 'full', 3, 0)",
        (TEST_USER_ID,)
    )
    await db._db.commit()
    
    user = {
        "user_id": TEST_USER_ID,
        "scanner_mode": "TAKER_BUY",
        "bank_codes": ["43"],
        "capital": 10000.0,
        "taker_buy_amount": 100.0,
        "taker_buy_price_strategy": "any",
    }
    
    order = make_test_order("o1", 40.0, 1000, 5000, bank_codes=["43"])
    buy_grouped = {"43": [order]}
    
    # Card module is off for single modes, should pass normally
    orders = await scanner.find_orders_for_user(user, buy_grouped, {})
    assert len(orders) == 1
    assert orders[0].id == "o1"

    # 2. Enable card module in single modes, but no active cards -> should filter it out
    await db._db.execute("UPDATE user_card_settings SET enable_in_single_modes=1 WHERE user_id=?", (TEST_USER_ID,))
    await db._db.commit()
    
    orders = await scanner.find_orders_for_user(user, buy_grouped, {})
    assert len(orders) == 0

    # 3. Add active card with low balance -> should filter out because of balance (amount 100 USDT * 40 UAH = 4000 UAH > 1000 balance)
    await setup_card(db, bank="monobank", balance=1000.0)
    orders = await scanner.find_orders_for_user(user, buy_grouped, {})
    assert len(orders) == 0

    # 4. Add active card with high balance -> should pass
    await db._db.execute("UPDATE cards SET balance=20000.0 WHERE owner_id=?", (TEST_USER_ID,))
    await db._db.commit()
    orders = await scanner.find_orders_for_user(user, buy_grouped, {})
    assert len(orders) == 1
    assert orders[0].id == "o1"

    # 5. Put card on cooldown -> should filter out
    await db._db.execute("UPDATE cards SET cooldown_until=? WHERE owner_id=?", (time.time() + 3600, TEST_USER_ID))
    await db._db.commit()
    orders = await scanner.find_orders_for_user(user, buy_grouped, {})
    assert len(orders) == 0
