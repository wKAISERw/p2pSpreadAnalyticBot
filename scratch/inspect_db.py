import asyncio
import sqlite3
import json

async def main():
    conn = sqlite3.connect("data/merchants.db")
    conn.row_factory = sqlite3.Row
    cur = conn.cursor()
    cur.execute("SELECT exchange, merchant_id, chat_id, message_ids_json, sent_at FROM sent_alerts")
    rows = cur.fetchall()
    print(f"Total sent alerts cached: {len(rows)}")
    for r in rows:
        print(dict(r))
    conn.close()

if __name__ == "__main__":
    asyncio.run(main())
