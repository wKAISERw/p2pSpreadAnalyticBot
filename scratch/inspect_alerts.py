import sqlite3
import json
import sys

if sys.stdout.encoding != 'utf-8':
    sys.stdout.reconfigure(encoding='utf-8')

db_path = "c:/p2p_scanner/data/merchants.db"

def inspect():
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()
    
    cursor.execute("SELECT * FROM sent_alerts ORDER BY sent_at DESC LIMIT 10")
    rows = cursor.fetchall()
    print(f"Total sent alerts: {len(rows)}")
    for r in rows:
        print(f"chat_id: {r['chat_id']} | sent_at: {r['sent_at']}")
        alert = json.loads(r['alert_json'])
        print(f"  buy_bank: {alert.get('buy_bank')} | sell_bank: {alert.get('sell_bank')}")
        print(f"  buy_banks_all: {alert.get('buy_banks_all')}")
        print(f"  sell_banks_all: {alert.get('sell_banks_all')}")
        print(f"  buy_banks_fit: {alert.get('buy_banks_fit')}")
        print(f"  sell_banks_fit: {alert.get('sell_banks_fit')}")
        print(f"  route_variants: {alert.get('route_variants')}")
        print(f"  route_pairs: {alert.get('route_pairs')}")
        
    conn.close()

if __name__ == "__main__":
    inspect()
