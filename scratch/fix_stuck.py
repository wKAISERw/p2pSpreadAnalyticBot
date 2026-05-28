import sqlite3
import time
import sys

# Fix encoding for Windows console
if sys.stdout.encoding != 'utf-8':
    import io
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')

con = sqlite3.connect('data/merchants.db')
con.row_factory = sqlite3.Row

sql = "SELECT exchange, merchant_id, merchant_name, trade_recommendation, verdict, updated_at FROM merchant_verdict WHERE trade_recommendation = 'RECHECKING'"
rows = con.execute(sql).fetchall()
print(f"RECHECKING count: {len(rows)}")

for r in rows:
    age = time.time() - (r['updated_at'] or 0)
    print(f"  {r['exchange']} | {r['merchant_name']} | verdict={r['verdict']} | age={age:.0f}s")
    # Fix stuck ones (>5 min)
    if age > 300:
        derive = {"OK": "APPROVE", "SUSPICIOUS": "CONDITIONAL", "BLOCK": "REJECT"}
        new_rec = derive.get(r['verdict'], "CONDITIONAL")
        con.execute(
            "UPDATE merchant_verdict SET trade_recommendation = ? WHERE exchange = ? AND merchant_id = ?",
            (new_rec, r['exchange'], r['merchant_id'])
        )
        print(f"    FIXED: RECHECKING -> {new_rec}")

con.commit()
con.close()
print("Done.")
