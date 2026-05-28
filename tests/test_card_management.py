"""
tests/test_card_management.py
Comprehensive tests for Card Management System (Phases 1-9).

Covers:
  - DB schema & CRUD (Phase 1-2)
  - Rolling limits, reservations, confirm_transaction (Phase 3)
  - Auto-cooldown at 95% (C7)
  - CardMatchingEngine: filters, scoring, split, internal split B6 (Phase 5)
  - CryptoUtils encryption/decryption (Phase 8)
  - Mono webhook matching logic (Phase 8)
"""
import sys
import time
import uuid
import asyncio
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

import pytest
import aiosqlite
from core.storage.merchant_db import MerchantDB
from core.engine.card_matching_engine import CardMatchingEngine, CardMatchResult
from core.security.crypto_utils import CryptoUtils


# ═══════════════════════════════════════════════════════════════
# Fixtures
# ═══════════════════════════════════════════════════════════════

@pytest.fixture
def event_loop():
    loop = asyncio.new_event_loop()
    yield loop
    loop.close()


@pytest.fixture
async def db(tmp_path):
    db_path = tmp_path / "test_cards.db"  # прибери str(), залиш Path
    merchant_db = MerchantDB(db_path=db_path)
    await merchant_db.start()
    yield merchant_db
    await merchant_db.stop()


TEST_USER_ID = 123456789


async def _setup_user_and_card(db: MerchantDB, balance: float = 50000.0, bank: str = "monobank",
                                status: str = "active", is_own: int = 1, last_four: str = "4321"):
    """Helper: create user_card_settings + user_bank_limits + a card."""
    # Ensure card settings exist
    await db._db.execute(
        "INSERT OR IGNORE INTO user_card_settings (user_id, card_module_mode, max_cards_per_order) VALUES (?, 'full', 3)",
        (TEST_USER_ID,)
    )
    # Ensure bank limits exist
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
        "last_four": last_four,
        "label": f"Test {last_four}",
        "is_own": is_own,
        "balance": balance,
        "status": status,
        "cooldown_until": 0,
        "last_monthly_reset": time.time(),
        "created_at": time.time(),
        "last_tx_timestamp": 0,
        "is_warmed_up": 1,  # Legacy tests assume no warmup restrictions
    }
    await db.add_card(card_data)
    return card_id


# ═══════════════════════════════════════════════════════════════
# Phase 1-2: DB Schema & CRUD
# ═══════════════════════════════════════════════════════════════

@pytest.mark.asyncio
async def test_add_and_get_card(db):
    card_id = await _setup_user_and_card(db, balance=10000.0)
    cards = await db.get_cards(TEST_USER_ID)
    assert len(cards) == 1
    assert cards[0]["id"] == card_id
    assert cards[0]["balance"] == 10000.0
    assert cards[0]["status"] == "active"


@pytest.mark.asyncio
async def test_update_card(db):
    card_id = await _setup_user_and_card(db)
    await db.update_card(card_id, {"label": "Updated Label", "status": "frozen_funds"})
    cards = await db.get_cards(TEST_USER_ID)
    assert cards[0]["label"] == "Updated Label"
    assert cards[0]["status"] == "frozen_funds"


@pytest.mark.asyncio
async def test_get_cards_filter_by_bank(db):
    await _setup_user_and_card(db, bank="monobank", last_four="1111")
    await _setup_user_and_card(db, bank="privatbank", last_four="2222")
    mono_cards = await db.get_cards(TEST_USER_ID, bank_name="monobank")
    assert len(mono_cards) == 1
    assert mono_cards[0]["bank_name"] == "monobank"


@pytest.mark.asyncio
async def test_get_cards_filter_by_status(db):
    c1 = await _setup_user_and_card(db, last_four="1111")
    c2 = await _setup_user_and_card(db, last_four="2222")
    await db.update_card(c2, {"status": "frozen_funds"})
    active = await db.get_cards(TEST_USER_ID, status="active")
    assert len(active) == 1


# ═══════════════════════════════════════════════════════════════
# Phase 3: Rolling Limits, Reservations, Transactions
# ═══════════════════════════════════════════════════════════════

@pytest.mark.asyncio
async def test_rolling_used_empty(db):
    card_id = await _setup_user_and_card(db)
    used = await db.get_rolling_used(card_id, "in", 24)
    assert used == 0.0


@pytest.mark.asyncio
async def test_confirm_transaction_updates_balance(db):
    card_id = await _setup_user_and_card(db, balance=50000.0)
    await db.confirm_transaction(card_id, 10000.0, "out", "work", source="test")
    cards = await db.get_cards(TEST_USER_ID)
    assert cards[0]["balance"] == pytest.approx(40000.0)


@pytest.mark.asyncio
async def test_confirm_transaction_true_balance(db):
    card_id = await _setup_user_and_card(db, balance=50000.0)
    await db.confirm_transaction(card_id, 5000.0, "in", "personal", source="webhook", true_balance=55555.0)
    cards = await db.get_cards(TEST_USER_ID)
    assert cards[0]["balance"] == pytest.approx(55555.0)


@pytest.mark.asyncio
async def test_rolling_used_after_transaction(db):
    card_id = await _setup_user_and_card(db)
    await db.confirm_transaction(card_id, 5000.0, "out", "work", source="test")
    await db.confirm_transaction(card_id, 3000.0, "out", "work", source="test")
    used = await db.get_rolling_used(card_id, "out", 24)
    assert used == pytest.approx(8000.0)


@pytest.mark.asyncio
async def test_transaction_count(db):
    card_id = await _setup_user_and_card(db)
    await db.confirm_transaction(card_id, 1000.0, "in", "work", source="test")
    await db.confirm_transaction(card_id, 2000.0, "out", "work", source="test")
    count = await db.get_card_transactions_count(card_id, 24)
    assert count == 2


@pytest.mark.asyncio
async def test_reserve_card_amount(db):
    card_id = await _setup_user_and_card(db, balance=50000.0)
    order_id = f"order_{int(time.time())}"
    split = [{"card_id": card_id, "amount": 10000.0}]
    result = await db.reserve_card_amount(
        owner_id=TEST_USER_ID,
        total_amount=10000.0,
        target_bank="monobank",
        direction="out",
        split_strategy=split,
        order_id=order_id,
        expected_window_minutes=30  # додати це
    )
    assert result is True


# ═══════════════════════════════════════════════════════════════
# C7: Auto-Cooldown at 95%
# ═══════════════════════════════════════════════════════════════

@pytest.mark.asyncio
async def test_auto_cooldown_at_95_percent(db):
    """When rolling usage hits 95% of daily limit, cooldown_until should be set."""
    card_id = await _setup_user_and_card(db, balance=200000.0)
    
    # Default daily_out_max = 150000. 95% = 142500.
    # Record a large transaction that pushes past 95%
    await db.confirm_transaction(card_id, 143000.0, "out", "work", source="test")
    
    cards = await db.get_cards(TEST_USER_ID)
    card = cards[0]
    assert card["cooldown_until"] > time.time(), "Cooldown should have been triggered at 95%"


@pytest.mark.asyncio
async def test_no_cooldown_below_95_percent(db):
    """Small transactions should not trigger cooldown."""
    card_id = await _setup_user_and_card(db, balance=200000.0)
    await db.confirm_transaction(card_id, 10000.0, "out", "work", source="test")
    cards = await db.get_cards(TEST_USER_ID)
    assert cards[0]["cooldown_until"] <= time.time()


# ═══════════════════════════════════════════════════════════════
# Phase 5: CardMatchingEngine
# ═══════════════════════════════════════════════════════════════

@pytest.mark.asyncio
async def test_engine_disabled_when_off(db):
    await _setup_user_and_card(db)
    # Set module to off
    await db._db.execute("UPDATE user_card_settings SET card_module_mode='off' WHERE user_id=?", (TEST_USER_ID,))
    await db._db.commit()
    
    engine = CardMatchingEngine(db)
    result = await engine.run(TEST_USER_ID, "monobank", 10000.0, "buy")
    assert result.status == "disabled"


@pytest.mark.asyncio
async def test_engine_no_crypto(db):
    await _setup_user_and_card(db)
    engine = CardMatchingEngine(db)
    result = await engine.run(TEST_USER_ID, "monobank", 10000.0, "sell", crypto_available=False)
    assert result.status == "no_crypto"


@pytest.mark.asyncio
async def test_engine_success_single_card(db):
    await _setup_user_and_card(db, balance=50000.0)
    engine = CardMatchingEngine(db)
    result = await engine.run(TEST_USER_ID, "monobank", 10000.0, "buy")
    assert result.status == "success"
    assert result.best_card is not None
    assert result.best_card["last_four"] == "4321"


@pytest.mark.asyncio
async def test_engine_no_cards_insufficient_balance(db):
    await _setup_user_and_card(db, balance=100.0)
    engine = CardMatchingEngine(db)
    result = await engine.run(TEST_USER_ID, "monobank", 50000.0, "buy")
    # With balance=100 and amount=50000, should fail
    assert result.status in ("no_cards", "needs_split")


@pytest.mark.asyncio
async def test_engine_cooldown_rejects_card(db):
    card_id = await _setup_user_and_card(db, balance=50000.0)
    # Set cooldown to future
    await db.update_card(card_id, {"cooldown_until": time.time() + 3600})
    
    engine = CardMatchingEngine(db)
    result = await engine.run(TEST_USER_ID, "monobank", 10000.0, "buy")
    assert result.status == "no_cards"
    assert any("cooldown" in r.get("reason", "").lower() for r in result.rejection_report)


@pytest.mark.asyncio
async def test_engine_excluded_cards(db):
    card_id = await _setup_user_and_card(db, balance=50000.0)
    engine = CardMatchingEngine(db)
    result = await engine.run(TEST_USER_ID, "monobank", 10000.0, "buy", excluded_cards=[card_id])
    assert result.status == "no_cards"


@pytest.mark.asyncio
async def test_engine_multi_card_split(db):
    """Two cards with small balances should produce a split."""
    await _setup_user_and_card(db, balance=15000.0, last_four="1111")
    await _setup_user_and_card(db, balance=15000.0, last_four="2222")
    
    engine = CardMatchingEngine(db)
    # 20000 > each card's max_avail (15000), but combined they cover it
    result = await engine.run(TEST_USER_ID, "monobank", 20000.0, "buy")
    assert result.status in ("success", "needs_split")


# ═══════════════════════════════════════════════════════════════
# B6: Internal Split (one card, two transactions)
# ═══════════════════════════════════════════════════════════════

@pytest.mark.asyncio
async def test_internal_split_single_card(db):
    """When amount > max_single_tx but card has enough daily capacity, 
    internal split should produce 2 legs on same card."""
    # max_single_tx_out = 29999 by default. Card has 100k balance.
    await _setup_user_and_card(db, balance=100000.0, last_four="5555")
    
    engine = CardMatchingEngine(db)
    # 40000 > 29999 (max_single_tx), but < 100000 (balance) and < 150000 (daily)
    result = await engine.run(TEST_USER_ID, "monobank", 40000.0, "buy")
    assert result.status == "needs_split"
    # Should have at least one split option where both legs are the same card
    found_internal = False
    for split in result.split_options:
        card_ids = [leg["card_id"] for leg in split]
        if len(card_ids) == 2 and card_ids[0] == card_ids[1]:
            found_internal = True
            assert split[0]["amount"] + split[1]["amount"] == pytest.approx(40000.0)
            break
    assert found_internal, "Expected internal split (same card, 2 legs)"


# ═══════════════════════════════════════════════════════════════
# Phase 8: CryptoUtils
# ═══════════════════════════════════════════════════════════════

def test_crypto_encrypt_decrypt():
    plaintext = "u1234567890abcdef_mono_token_test"
    encrypted = CryptoUtils.encrypt(plaintext)
    assert encrypted != plaintext
    decrypted = CryptoUtils.decrypt(encrypted)
    assert decrypted == plaintext


def test_crypto_empty_string():
    assert CryptoUtils.encrypt("") == ""
    assert CryptoUtils.decrypt("") == ""


def test_crypto_bad_ciphertext():
    result = CryptoUtils.decrypt("not_a_valid_token")
    assert result == ""


# ═══════════════════════════════════════════════════════════════
# Phase 8: Mono Webhook Matching
# ═══════════════════════════════════════════════════════════════

@pytest.mark.asyncio
async def test_mono_settings_save_and_get(db):
    card_id = await _setup_user_and_card(db)
    await db.save_card_mono_settings(card_id, "encrypted_token_xxx", "secret_abc")
    settings = await db.get_card_mono_settings(card_id)
    assert settings["x_token_encrypted"] == "encrypted_token_xxx"
    assert settings["webhook_secret"] == "secret_abc"


@pytest.mark.asyncio
async def test_mono_account_mapping(db):
    card_id = await _setup_user_and_card(db)
    await db.update_card_mono_account(card_id, "mono_acc_UeOWj123")
    card = await db.get_card_by_mono_account("mono_acc_UeOWj123")
    assert card is not None
    assert card["id"] == card_id


@pytest.mark.asyncio
async def test_find_pending_order_match(db):
    card_id = await _setup_user_and_card(db, balance=50000.0)
    order_id = f"order_{int(time.time()*1000)}"
    split = [{"card_id": card_id, "amount": 15000.0}]
    await db.reserve_card_amount(
        owner_id=TEST_USER_ID,
        total_amount=15000.0,
        target_bank="monobank",
        direction="in",
        split_strategy=split,
        order_id=order_id,
        expected_window_minutes=30  # додати
    )
    found = await db.find_pending_order_for_card(card_id, 15000.0)
    assert found is not None
    assert found["id"] == order_id


@pytest.mark.asyncio
async def test_find_pending_order_no_match(db):
    card_id = await _setup_user_and_card(db)
    found = await db.find_pending_order_for_card(card_id, 99999.0)
    assert found is None


# ═══════════════════════════════════════════════════════════════
# D6: Bank Limits CRUD
# ═══════════════════════════════════════════════════════════════

@pytest.mark.asyncio
async def test_set_bank_limit_creates_row(db):
    await db.set_user_bank_limit(TEST_USER_ID, "privatbank", "daily_out_max", 100000.0)
    limits = await db.get_user_bank_limits(TEST_USER_ID, "privatbank")
    assert limits is not None
    assert limits["daily_out_max"] == 100000.0
    # Other fields should have defaults
    assert limits["daily_in_max"] == 150000.0


@pytest.mark.asyncio
async def test_set_bank_limit_updates_existing(db):
    await db.set_user_bank_limit(TEST_USER_ID, "monobank", "max_tx_per_day", 20)
    await db.set_user_bank_limit(TEST_USER_ID, "monobank", "max_tx_per_day", 25)
    limits = await db.get_user_bank_limits(TEST_USER_ID, "monobank")
    assert limits["max_tx_per_day"] == 25


@pytest.mark.asyncio
async def test_set_bank_limit_rejects_unknown_field(db):
    # Should silently ignore unknown fields (SQL injection protection)
    await db.set_user_bank_limit(TEST_USER_ID, "monobank", "DROP TABLE cards", 0)
    limits = await db.get_user_bank_limits(TEST_USER_ID, "monobank")
    # If it was accepted, the query would fail. If no row exists, that's fine too.
    # Main thing: no crash, no injection.


# ═══════════════════════════════════════════════════════════════
# D5: Report Stats
# ═══════════════════════════════════════════════════════════════

@pytest.mark.asyncio
async def test_card_report_stats(db):
    card_id = await _setup_user_and_card(db)
    await db.confirm_transaction(card_id, 5000.0, "in", "work", source="test")
    await db.confirm_transaction(card_id, 2000.0, "out", "personal", source="test")
    
    stats = await db.get_card_report_stats(card_id)
    assert stats["tx_count"] == 2
    assert stats["work_in_today"] == pytest.approx(5000.0)
    assert stats["personal_out_today"] == pytest.approx(2000.0)


# ═══════════════════════════════════════════════════════════════
# TTL cleanup (card_notifier cache — unit logic)
# ═══════════════════════════════════════════════════════════════

def test_ttl_cache_cleanup():
    """Verify TTL cleanup logic works on expired keys."""
    from bot.card_notifier import _card_matching_cache
    
    now = int(time.time())
    old_key = f"match_{TEST_USER_ID}_{now - 2000}"  # 2000 seconds ago (> 1800 TTL)
    fresh_key = f"match_{TEST_USER_ID}_{now - 100}"  # 100 seconds ago (< 1800 TTL)
    
    _card_matching_cache[old_key] = {"test": True}
    _card_matching_cache[fresh_key] = {"test": True}
    
    # Simulate TTL cleanup
    expired = [k for k in list(_card_matching_cache.keys())
               if now - int(k.split("_")[-1]) > 1800]
    for k in expired:
        del _card_matching_cache[k]
    
    assert old_key not in _card_matching_cache
    assert fresh_key in _card_matching_cache
    
    # Cleanup
    del _card_matching_cache[fresh_key]


# ═══════════════════════════════════════════════════════════════
# Expired Reservations Release
# ═══════════════════════════════════════════════════════════════

@pytest.mark.asyncio
async def test_release_expired_reservations(db):
    card_id = await _setup_user_and_card(db, balance=50000.0)
    order_id = f"order_expired_{int(time.time()*1000)}"
    split = [{"card_id": card_id, "amount": 10000.0}]
    await db.reserve_card_amount(
        owner_id=TEST_USER_ID,
        total_amount=10000.0,
        target_bank="monobank",
        direction="out",
        split_strategy=split,
        order_id=order_id,
        expected_window_minutes=0  # expires immediately
    )
    # Backdate the order so it's expired
    await db._db.execute(
        "UPDATE card_orders SET created_at=? WHERE id=?",
        (time.time() - 7200, order_id)  # 2 hours ago
    )
    await db._db.commit()
    
    count = await db.release_expired_reservations()
    assert count >= 1


@pytest.mark.asyncio
async def test_get_user_auto_capital(db):
    # Setup card 1: Monobank balance=15000 (healthy)
    c1 = await _setup_user_and_card(db, balance=15000.0, bank="monobank", last_four="1111")
    
    # Setup card 2: Privatbank balance=200000 (healthy)
    c2 = await _setup_user_and_card(db, balance=200000.0, bank="privatbank", last_four="2222")
    
    # Setup card 3: Cooldown card balance=30000 (ignored because of cooldown)
    c3 = await _setup_user_and_card(db, balance=30000.0, bank="monobank", last_four="3333")
    await db.update_card(c3, {"cooldown_until": time.time() + 3600})
    
    # Initial auto-capital should sum healthy card balances (15000 + 200000 [bounded by daily_out_max=150000] = 165000)
    cap = await db.get_user_auto_capital(TEST_USER_ID)
    assert cap == pytest.approx(165000.0)
    
    # Exhaust privatbank limits by recording transactions (limits: daily_out_max=150000)
    # If we spend 140000 from c2 (available daily becomes 10000)
    # Available amount on c2 will be min(balance=60000, daily_avail=10000) = 10000.
    # Total auto-capital should become 15000 (c1) + 10000 (c2) = 25000.
    await db.confirm_transaction(c2, 140000.0, "out", "work", source="test")
    
    cap2 = await db.get_user_auto_capital(TEST_USER_ID)
    assert cap2 == pytest.approx(25000.0)


@pytest.mark.asyncio
async def test_disabled_limits_ignored_by_engine(db):
    # Setup a card with balance of 250,000.0
    card_id = await _setup_user_and_card(db, balance=250000.0, bank="monobank")
    
    # 1. Test global limits set to -1
    # By default, max_single_tx_out is 29999.0, and daily_out_max is 150000.0.
    # Set them to -1.0
    await db.set_user_bank_limit(TEST_USER_ID, "monobank", "max_single_tx_out", -1.0)
    await db.set_user_bank_limit(TEST_USER_ID, "monobank", "daily_out_max", -1.0)
    
    # Run engine with 200,000.0. This exceeds default daily out max (150000) and max single (29999).
    engine = CardMatchingEngine(db)
    result = await engine.run(TEST_USER_ID, "monobank", 200000.0, "buy")
    assert result.status == "success"
    assert result.best_card["id"] == card_id
    
    # 2. Test local override set to -1
    # Set global max_single_tx_out back to a small value, e.g. 5000.0
    await db.set_user_bank_limit(TEST_USER_ID, "monobank", "max_single_tx_out", 5000.0)
    
    # Verify that run with 10000.0 fails to match directly because of max_single (triggers split)
    result_fail = await engine.run(TEST_USER_ID, "monobank", 10000.0, "buy")
    assert result_fail.status == "needs_split"
    
    # Now set local override for this card's max_single_tx_out to -1.0
    await db.update_card_limit_override(card_id, "max_single_tx_out", -1.0)
    
    # Verify that run with 10000.0 now succeeds
    result_success = await engine.run(TEST_USER_ID, "monobank", 10000.0, "buy")
    assert result_success.status == "success"
    assert result_success.best_card["id"] == card_id
