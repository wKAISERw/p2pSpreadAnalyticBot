import sqlite3
import sys

sys.stdout.reconfigure(encoding='utf-8')

conn = sqlite3.connect("data/merchants.db")
conn.row_factory = sqlite3.Row
cursor = conn.cursor()

# Let's check merchant_verdict or recent snapshots to see OKX ads
print("=== OKX verdicts ===")
cursor.execute("SELECT * FROM merchant_verdict WHERE exchange='OKX' LIMIT 10")
for r in cursor.fetchall():
    print(dict(r))

print("\n=== OKX reviews ===")
cursor.execute("SELECT * FROM merchant_reviews WHERE exchange='OKX' LIMIT 10")
for r in cursor.fetchall():
    print(dict(r))

conn.close()
