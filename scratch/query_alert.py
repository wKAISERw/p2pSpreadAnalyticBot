import sys
sys.stdout.reconfigure(encoding='utf-8')
import sqlite3
import json
import datetime
import time

conn = sqlite3.connect('data/merchants.db')
conn.row_factory = sqlite3.Row

since = time.time() - 12 * 3600
rows = conn.execute("SELECT * FROM sent_alerts WHERE sent_at > ? ORDER BY sent_at DESC", (since,)).fetchall()
print(f"Total sent alerts in last 12h: {len(rows)}")
for r in rows:
    alert_dict = json.loads(r["alert_json"])
    buy_merch = alert_dict.get("buy_order", {}).get("merchant_name")
    sell_merch = alert_dict.get("sell_order", {}).get("merchant_name")
    print(f"[{datetime.datetime.fromtimestamp(r['sent_at'])}] Chat: {r['chat_id']} Buy: {buy_merch} Sell: {sell_merch} buy_bank: {alert_dict.get('buy_bank')} sell_bank: {alert_dict.get('sell_bank')} route_variants: {alert_dict.get('route_variants')}")
