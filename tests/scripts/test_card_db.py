import asyncio
import time
import sys
import os
from pathlib import Path

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import time
from pathlib import Path

from core.storage.merchant_db import MerchantDB

async def test_card_db():
    print("Initializing DB...")
    db_path = Path("test_merchants.db")
    if db_path.exists():
        db_path.unlink()
        
    db = MerchantDB(db_path=db_path)
    await db.start()
    
    print("Testing add_card...")
    user_id = 12345
    card_id = "uuid-card-1"
    
    await db.add_card({
        "id": card_id,
        "owner_id": user_id,
        "bank_name": "monobank",
        "last_four": "4321",
        "label": "Моно Своя",
        "balance": 10000.0
    })
    
    cards = await db.get_cards(user_id)
    assert len(cards) == 1
    assert cards[0]["balance"] == 10000.0
    print("add_card passed!")
    
    print("Testing reserve_card_amount...")
    order_id = "uuid-order-1"
    success = await db.reserve_card_amount(
        order_id=order_id,
        owner_id=user_id,
        target_bank="monobank",
        direction="out",
        total_amount=2000.0,
        expected_window_minutes=30,
        split_strategy=[{"card_id": card_id, "amount": 2000.0}]
    )
    assert success is True
    print("reserve_card_amount passed!")
    
    print("Testing confirm_transaction (simulating manual push)...")
    await db.confirm_transaction(
        card_id=card_id,
        amount=2000.0,
        direction="out",
        type_str="work",
        linked_order_id=order_id
    )
    
    cards_after = await db.get_cards(user_id)
    assert cards_after[0]["balance"] == 8000.0  # 10000 - 2000
    print("confirm_transaction passed!")
    
    print("Testing get_rolling_used...")
    used_out = await db.get_rolling_used(card_id, "out")
    assert used_out == 2000.0
    print(f"get_rolling_used passed (used: {used_out})!")
    
    print("Testing release_expired_reservations...")
    # Add an expired reservation manually
    expired_order_id = "uuid-order-expired"
    old_time = time.time() - 3600  # 1 hour ago
    
    await db._db.execute(
        "INSERT INTO card_orders (id, owner_id, total_amount, target_bank, direction, status, expected_window_minutes, created_at) "
        "VALUES (?, ?, ?, ?, ?, 'pending', 30, ?)",
        (expired_order_id, user_id, 1000.0, "monobank", "out", old_time)
    )
    await db._db.execute(
        "INSERT INTO card_order_legs (order_id, card_id, amount, leg_status) VALUES (?, ?, ?, 'pending')",
        (expired_order_id, card_id, 1000.0)
    )
    await db._db.commit()
    
    released_count = await db.release_expired_reservations()
    assert released_count == 1
    
    async with db._db.execute("SELECT status FROM card_orders WHERE id=?", (expired_order_id,)) as cur:
        row = await cur.fetchone()
        assert row["status"] == "canceled"
        
    async with db._db.execute("SELECT leg_status FROM card_order_legs WHERE order_id=?", (expired_order_id,)) as cur:
        row = await cur.fetchone()
        assert row["leg_status"] == "timeout"
    
    print("release_expired_reservations passed!")
    
    await db.stop()
    if db_path.exists():
        db_path.unlink()
        Path(f"{db_path}-wal").unlink(missing_ok=True)
        Path(f"{db_path}-shm").unlink(missing_ok=True)
    print("ALL TESTS PASSED!")

if __name__ == "__main__":
    asyncio.run(test_card_db())
