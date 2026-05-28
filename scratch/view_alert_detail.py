import sys
sys.stdout.reconfigure(encoding='utf-8')
import sqlite3
import json

conn = sqlite3.connect('data/merchants.db')
conn.row_factory = sqlite3.Row

row = conn.execute("SELECT * FROM sent_alerts WHERE alert_json LIKE '%Ilya_ZAV%' ORDER BY sent_at DESC LIMIT 1").fetchone()
if not row:
    print("Alert not found")
else:
    print("Sent At:", row["sent_at"])
    alert_dict = json.loads(row["alert_json"])
    print(json.dumps(alert_dict, indent=2, ensure_ascii=False))
