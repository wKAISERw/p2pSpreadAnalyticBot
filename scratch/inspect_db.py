import asyncio
from pathlib import Path
from core.storage.merchant_db import MerchantDB

async def main():
    db = MerchantDB(Path("data/merchants.db"))
    await db.start()
    
    # Get active users
    users = await db.get_active_users()
    print("USERS:")
    for u in users:
        print(f"User ID: {u.get('user_id')}, Chat ID: {u.get('chat_id')}, scanner_mode: {u.get('scanner_mode')}")
        print(f"  buy_bank_codes: {u.get('buy_bank_codes')}, sell_bank_codes: {u.get('sell_bank_codes')}")
        
    # Get cards
    print("\nCARDS:")
    cursor = await db._db.execute("SELECT * FROM cards")
    rows = await cursor.fetchall()
    cols = [col[0] for col in cursor.description]
    for r in rows:
        card = dict(zip(cols, r))
        print(card)
        
    # Get user_card_settings
    print("\nUSER_CARD_SETTINGS:")
    cursor = await db._db.execute("SELECT * FROM user_card_settings")
    rows = await cursor.fetchall()
    cols = [col[0] for col in cursor.description]
    for r in rows:
        print(dict(zip(cols, r)))
        
    await db.disconnect()

if __name__ == "__main__":
    asyncio.run(main())
