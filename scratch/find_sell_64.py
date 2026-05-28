import sys
sys.stdout.reconfigure(encoding='utf-8')
import sqlite3
import json
import datetime

conn = sqlite3.connect('data/merchants.db')
conn.row_factory = sqlite3.Row

rows = conn.execute("SELECT * FROM sent_alerts WHERE chat_id = 1115620363").fetchall()
print(f"Total alerts for KAIŜER: {len(rows)}")
for r in rows:
    alert_dict = json.loads(r["alert_json"])
    if alert_dict.get("sell_bank") == "64":
        dt = datetime.datetime.fromtimestamp(r["sent_at"])
        print(f"[{dt}] Buy: {alert_dict.get('buy_order', {}).get('merchant_name')} Sell: {alert_dict.get('sell_order', {}).get('merchant_name')} buy_bank: {alert_dict.get('buy_bank')} sell_bank: {alert_dict.get('sell_bank')} route_variants: {alert_dict.get('route_variants')}")
