import asyncio
import sqlite3
import time
from core.storage.merchant_db import MerchantDB

async def main():
    db = MerchantDB()
    await db.start()
    
    exchange = "OKX"
    merchant_id = "e8c609fc16"
    
    # Let's inspect the database directly
    conn = db._db
    now = time.time()
    since = now - 1800
    
    print(f"Current time: {now}")
    print(f"Since threshold: {since}")
    
    async with conn.execute(
        "SELECT chat_id, message_ids_json, sent_at FROM sent_alerts WHERE exchange = ? AND merchant_id = ?",
        (exchange, merchant_id)
    ) as cur:
        rows = await cur.fetchall()
        print(f"Total rows for {exchange} / {merchant_id}: {len(rows)}")
        for r in rows:
            print(f"  chat_id={r[0]}, message_ids={r[1]}, sent_at={r[2]}, age_seconds={now - r[2]}")
            
    recent = await db.get_recent_sent_alerts(exchange, merchant_id)
    print(f"get_recent_sent_alerts returned: {recent}")
    
    await db.stop()

if __name__ == "__main__":
    asyncio.run(main())
