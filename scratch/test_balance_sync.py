import asyncio
import os
import dotenv
from pathlib import Path
from core.storage.merchant_db import MerchantDB
from core.workers.card_sync import CardBalanceSyncTask
from core.utils.crypto import decrypt

dotenv.load_dotenv()

async def verify():
    db = MerchantDB(Path("data/merchants.db"))
    await db.start()
    
    # 1. Print old balance in DB
    print("--- OLD CARD BALANCE ---")
    conn = db._db
    async with conn.execute("SELECT id, bank_name, last_four, balance, mono_x_token_encrypted, mono_account_id FROM cards WHERE bank_name='monobank'") as cur:
        row = await cur.fetchone()
        if not row:
            print("No monobank card found in DB.")
            await db.disconnect()
            return
            
        print(f"Card ID: {row['id']}")
        print(f"Bank Name: {row['bank_name']}")
        print(f"Last Four: {row['last_four']}")
        print(f"Balance in DB: {row['balance']} UAH")
        print(f"Account ID: {row['mono_account_id']}")
        
        # 2. Try decrypting token
        token_enc = row['mono_x_token_encrypted']
        try:
            token = decrypt(token_enc)
            print(f"Decrypted Token: {token[:10]}... (length: {len(token)})" if token else "Decrypted Token is empty")
        except Exception as e:
            print(f"Failed to decrypt token: {e}")
            
    # 3. Instantiate CardBalanceSyncTask and run sync_balances()
    print("\n--- RUNNING CARD BALANCE SYNC TASK ---")
    task = CardBalanceSyncTask(db, interval_seconds=90)
    await task.sync_balances()
    
    # 4. Print new balance in DB
    print("\n--- NEW CARD BALANCE ---")
    async with conn.execute("SELECT balance FROM cards WHERE id=?", (row['id'],)) as cur:
        new_row = await cur.fetchone()
        print(f"New Balance in DB: {new_row['balance']} UAH")
        
    await db.disconnect()

if __name__ == "__main__":
    asyncio.run(verify())
