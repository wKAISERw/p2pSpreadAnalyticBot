import sys
sys.stdout.reconfigure(encoding='utf-8')
import sqlite3
import json
import datetime

conn = sqlite3.connect('data/merchants.db')
conn.row_factory = sqlite3.Row

rows = conn.execute("SELECT * FROM sent_alerts ORDER BY sent_at ASC").fetchall()
for r in rows:
    dt = datetime.datetime.fromtimestamp(r["sent_at"])
    alert_dict = json.loads(r["alert_json"])
    print(f"[{dt}] Chat: {r['chat_id']} Buy: {alert_dict.get('buy_order', {}).get('merchant_name')} Sell: {alert_dict.get('sell_order', {}).get('merchant_name')} buy_bank: {alert_dict.get('buy_bank')} sell_bank: {alert_dict.get('sell_bank')}")
