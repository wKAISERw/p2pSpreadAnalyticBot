import sqlite3
import json
import sys
from datetime import datetime

if sys.stdout.encoding != 'utf-8':
    sys.stdout.reconfigure(encoding='utf-8')

db_path = "c:/p2p_scanner/data/merchants.db"

def inspect():
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()
    
    cursor.execute("SELECT * FROM sent_alerts")
    rows = cursor.fetchall()
    print(f"Total sent alerts: {len(rows)}")
    for r in rows:
        dt = datetime.fromtimestamp(r['sent_at']).strftime('%Y-%m-%d %H:%M:%S')
        alert = json.loads(r['alert_json'])
        rv = alert.get('route_variants', [])
        print(f"chat_id: {r['chat_id']} | sent_at: {dt} | buy_bank: {alert.get('buy_bank')} | sell_bank: {alert.get('sell_bank')} | route_variants: {rv}")
        
    conn.close()

if __name__ == "__main__":
    inspect()
