import sqlite3
import sys

# Reconfigure stdout to handle UTF-8
sys.stdout.reconfigure(encoding='utf-8')

conn = sqlite3.connect("data/merchants.db")
conn.row_factory = sqlite3.Row
cursor = conn.cursor()

user_id = 1115620363

print("=== User settings ===")
cursor.execute("SELECT * FROM scanner_users WHERE user_id = ?", (user_id,))
u = cursor.fetchone()
if u:
    print(dict(u))
else:
    print("User not found in scanner_users")

print("\n=== All active cards ===")
cursor.execute("SELECT * FROM cards WHERE owner_id = ? AND status='active'", (user_id,))
rows = cursor.fetchall()
for r in rows:
    print(dict(r))

conn.close()
