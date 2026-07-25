import sys
import time
import uuid
import pytest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from core.storage.merchant_db import MerchantDB
from bot.card_notifier import CardNotifier

TEST_USER_ID = 987654321

@pytest.fixture
async def db(tmp_path):
    db_path = tmp_path / "test_warmth.db"
    merchant_db = MerchantDB(db_path=db_path)
    await merchant_db.start()
    yield merchant_db
    await merchant_db.stop()

async def _setup_card(db: MerchantDB, balance: float, bank: str, last_four: str, status: str = "active", is_warmed: int = 0):
    await db._db.execute(
        "INSERT OR IGNORE INTO user_card_settings (user_id, card_module_mode) VALUES (?, 'full')",
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
        "last_four": last_four,
        "label": f"Test {last_four}",
        "is_own": 1,
        "balance": balance,
        "status": status,
        "cooldown_until": 0,
        "last_monthly_reset": time.time(),
        "created_at": time.time(),
        "last_tx_timestamp": 0,
        "is_warmed_up": is_warmed,
    }
    await db.add_card(card_data)
    return card_id

@pytest.mark.asyncio
async def test_get_user_auto_capital_per_bank(db):
    # Setup Monobank: total balance 25,000 (10k + 15k, warmed up so no warmup limit applies)
    await _setup_card(db, balance=10000.0, bank="monobank", last_four="1111", is_warmed=1)
    await _setup_card(db, balance=15000.0, bank="monobank", last_four="2222", is_warmed=1)

    # Setup Privatbank: total balance 40,000 (warmed up)
    await _setup_card(db, balance=40000.0, bank="privatbank", last_four="3333", is_warmed=1)

    # 1. Without allowed_banks (global sum: 10 + 15 + 40 = 65,000)
    global_cap = await db.get_user_auto_capital(TEST_USER_ID)
    assert global_cap == pytest.approx(65000.0)

    # 2. With allowed_banks = {"monobank", "privatbank"}
    # Returns max of Monobank capital (25,000) and Privatbank capital (40,000) => 40,000
    cap = await db.get_user_auto_capital(TEST_USER_ID, allowed_banks={"monobank", "privatbank"})
    assert cap == pytest.approx(40000.0)

    # 3. With allowed_banks = {"monobank"}
    # Returns Monobank capital (25,000)
    cap_mono = await db.get_user_auto_capital(TEST_USER_ID, allowed_banks={"monobank"})
    assert cap_mono == pytest.approx(25000.0)

@pytest.mark.asyncio
async def test_card_warmth_toggle(db):
    card_id = await _setup_card(db, balance=1000.0, bank="monobank", last_four="1111", is_warmed=0)
    
    # Verify initially cold
    cards = await db.get_cards(TEST_USER_ID)
    assert cards[0]["is_warmed_up"] == 0

    # Toggle to warmed up
    await db.update_card(card_id, {"is_warmed_up": 1})
    cards = await db.get_cards(TEST_USER_ID)
    assert cards[0]["is_warmed_up"] == 1

@pytest.mark.asyncio
async def test_balances_breakdown_and_transfer_recommendations(db):
    # Setup source card: Monobank balance=30,000 (has enough balance to transfer, is warmed)
    src_id = await _setup_card(db, balance=30000.0, bank="monobank", last_four="1111", is_warmed=1)
    
    # Setup dest card: Privatbank balance=1,000 (needs more money to cover a 15,000 order, is warmed)
    dest_id = await _setup_card(db, balance=1000.0, bank="privatbank", last_four="2222", is_warmed=1)

    notifier = CardNotifier(db, None)

    # 1. Test balance breakdown rendering
    breakdown = await notifier._render_balances_breakdown(TEST_USER_ID)
    assert "Monobank *1111" in breakdown
    assert "Privatbank *2222" in breakdown
    assert "30,000.00" in breakdown
    assert "1,000.00" in breakdown

    # 2. Test transfer suggestion when limits are healthy
    # Needed: 15,000 on Privatbank. Shortage = 14,000.
    # Source (Mono *1111) has 30,000 (enough).
    # Default limits are: max_single_tx=29999, daily_max=150000. All healthy.
    recommendation = await notifier._generate_transfer_recommendation(TEST_USER_ID, "privatbank", 15000.0)
    assert "14,000.00" in recommendation
    assert "Monobank *1111" in recommendation
    assert "Privatbank *2222" in recommendation

    # 3. Test transfer suggestion fails when source single-tx limit is violated
    # Let's set source max_single_tx_out to 5,000 UAH
    await db.set_user_bank_limit(TEST_USER_ID, "monobank", "max_single_tx_out", 5000.0)
    rec_fail_limit = await notifier._generate_transfer_recommendation(TEST_USER_ID, "privatbank", 15000.0)
    assert rec_fail_limit == ""

@pytest.mark.asyncio
async def test_matching_engine_warmup_constraints(db):
    from core.engine.card_matching_engine import CardMatchingEngine

    # 1. New card: 0 transactions, balance 50,000. Capped at 2,000 ₴.
    card_id = await _setup_card(db, balance=50000.0, bank="monobank", last_four="1234", is_warmed=0)
    engine = CardMatchingEngine(db)

    # Match for 1,500 UAH (<= 2,000 limit) -> Success
    res1 = await engine.run(TEST_USER_ID, "monobank", 1500.0, "buy")
    assert res1.status == "success"
    assert res1.best_card["id"] == card_id

    # Match for 3,000 UAH (> 2,000 limit) -> Fails (no_cards)
    res2 = await engine.run(TEST_USER_ID, "monobank", 3000.0, "buy")
    assert res2.status == "no_cards"
    assert any("Непрогріта картка" in r.get("reason", "") for r in res2.rejection_report)

    # 2. Add manual override (is_warmed_up = 1) -> Success for 3,000 UAH
    await db.update_card(card_id, {"is_warmed_up": 1})
    res3 = await engine.run(TEST_USER_ID, "monobank", 3000.0, "buy")
    assert res3.status == "success"

    # Reset manual override
    await db.update_card(card_id, {"is_warmed_up": 0})

    # 3. Simulate dormant card (>30 days inactive)
    # Add 10 historical transactions in the past (e.g. 40 days ago)
    past_timestamp = time.time() - 40 * 86400
    for _ in range(10):
        # We confirm transactions directly to card_transactions table
        await db.confirm_transaction(card_id, 100.0, "out", "work", source="test")
    # Backdate those transactions to simulate dormant state
    await db._db.execute("UPDATE card_transactions SET timestamp = ?", (past_timestamp,))
    await db._db.commit()

    # Match for 4,000 UAH (dormant limit is 5,000 UAH) -> Success
    res4 = await engine.run(TEST_USER_ID, "monobank", 4000.0, "buy")
    assert res4.status == "success"

    # Match for 6,000 UAH (dormant limit is 5,000 UAH) -> Fails
    res5 = await engine.run(TEST_USER_ID, "monobank", 6000.0, "buy")
    assert res5.status == "no_cards"

    # 4. Wake up the card: complete a recent transaction
    await db.confirm_transaction(card_id, 100.0, "out", "work", source="test") # timestamp is now (recent)

    # Card now has 11 transactions and last_tx is recent -> Auto Warmed Up!
    # Match for 25,000 UAH -> Success!
    res6 = await engine.run(TEST_USER_ID, "monobank", 25000.0, "buy")
    assert res6.status == "success"
