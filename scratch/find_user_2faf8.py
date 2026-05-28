import sys
sys.stdout.reconfigure(encoding='utf-8')
import sqlite3
import json
import datetime

conn = sqlite3.connect('data/merchants.db')
conn.row_factory = sqlite3.Row

rows = conn.execute("SELECT * FROM sent_alerts").fetchall()
for r in rows:
    if "s793c0a23291b3139910234d65d2ad96d" in r["alert_json"]:
        print("FOUND IT BY ADVERTISER NO!")
        print("Sent At:", datetime.datetime.fromtimestamp(r["sent_at"]))
        print(json.dumps(json.loads(r["alert_json"]), indent=2, ensure_ascii=False))
