import asyncio
import time
import sys
import os
from pathlib import Path

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core.storage.merchant_db import MerchantDB
from core.engine.card_matching_engine import CardMatchingEngine

async def test_engine():
    print("Initializing DB...")
    db_path = Path("test_engine.db")
    if db_path.exists():
        db_path.unlink()
        
    db = MerchantDB(db_path=db_path)
    await db.start()
    
    user_id = 777
    bank = "monobank"
    
    # Enable module
    await db._db.execute(
        "INSERT INTO user_card_settings (user_id, card_module_mode, max_cards_per_order) VALUES (?, 'full', 3)",
        (user_id,)
    )
    # Add two cards: one with 2000, one with 5000
    await db.add_card({
        "id": "card-1", "owner_id": user_id, "bank_name": bank, "last_four": "1111", 
        "balance": 2000.0, "is_own": 1, "status": "active"
    })
    await db.add_card({
        "id": "card-2", "owner_id": user_id, "bank_name": bank, "last_four": "2222", 
        "balance": 5000.0, "is_own": 0, "status": "active"
    })
    
    engine = CardMatchingEngine(db)
    
    print("Testing buy amount 1000...")
    res = await engine.run(user_id, bank, 1000.0, "buy")
    assert res.status == "success"
    # Should pick card-2 because is_own=0 gives +50 points
    assert res.best_card["id"] == "card-2"
    print("Passed.")
    
    print("Testing buy amount 6000 (needs split)...")
    res = await engine.run(user_id, bank, 6000.0, "buy")
    assert res.status == "needs_split"
    assert len(res.split_options) > 0
    # The sum of the split option should be 6000
    total = sum(s["amount"] for s in res.split_options[0])
    assert total == 6000.0
    print("Passed.")
    
    print("Testing buy amount 10000 (no cards)...")
    res = await engine.run(user_id, bank, 10000.0, "buy")
    assert res.status == "no_cards"
    print("Passed.")
    
    print("Testing spread with no crypto...")
    res = await engine.run(user_id, bank, 1000.0, "spread", crypto_available=False)
    assert res.status == "no_crypto"
    print("Passed.")
    
    await db.stop()
    if db_path.exists():
        db_path.unlink()
        Path(f"{db_path}-wal").unlink(missing_ok=True)
        Path(f"{db_path}-shm").unlink(missing_ok=True)
    print("ALL ENGINE TESTS PASSED!")

if __name__ == "__main__":
    asyncio.run(test_engine())
