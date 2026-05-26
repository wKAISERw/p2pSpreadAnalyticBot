import asyncio
from pathlib import Path
from core.storage.merchant_db import MerchantDB
from core.engine.card_matching_engine import CardMatchingEngine

async def test():
    db = MerchantDB(Path("data/merchants.db"))
    await db.start()
    
    uid = 1115620363
    engine = CardMatchingEngine(db)
    
    # Check what CardMatchingEngine returns for sell
    result = await engine.run(uid, "monobank", 4300.0, "sell")
    print(f"RUN RESULT (monobank, 4300.0, sell):")
    print(f"Status: {result.status}")
    print(f"Best Card: {result.best_card}")
    print(f"Split Options: {result.split_options}")
    print(f"Rejection Report: {result.rejection_report}")
    
    # Let's check buy too
    result_buy = await engine.run(uid, "monobank", 4300.0, "buy")
    print(f"\nRUN RESULT (monobank, 4300.0, buy):")
    print(f"Status: {result_buy.status}")
    print(f"Best Card: {result_buy.best_card}")
    print(f"Split Options: {result_buy.split_options}")
    print(f"Rejection Report: {result_buy.rejection_report}")
    
    await db.disconnect()

if __name__ == "__main__":
    asyncio.run(test())
