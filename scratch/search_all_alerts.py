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
    
    cursor.execute("SELECT chat_id, sent_at, alert_json FROM sent_alerts")
    rows = cursor.fetchall()
    print(f"Total sent alerts: {len(rows)}")
    for r in rows:
        alert = json.loads(r['alert_json'])
        rv = alert.get('route_variants', [])
        if any("PrivatBank" in v and "ПУМБ" in v for v in rv):
            buy_o = alert.get('buy_order', {})
            sell_o = alert.get('sell_order', {})
            print(f"CHAT: {r['chat_id']} | TIME: {r['sent_at']}")
            print(f"  BUY MERCHANT: {buy_o.get('merchant_name')} ({buy_o.get('exchange')})")
            print(f"  SELL MERCHANT: {sell_o.get('merchant_name')} ({sell_o.get('exchange')})")
            print(f"  buy_bank: {alert.get('buy_bank')} | sell_bank: {alert.get('sell_bank')}")
            print(f"  route_variants: {rv}")
        
    conn.close()

if __name__ == "__main__":
    inspect()
