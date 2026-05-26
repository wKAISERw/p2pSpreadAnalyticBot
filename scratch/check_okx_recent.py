import sqlite3
import json
import sys
import time

def main():
    try:
        sys.stdout.reconfigure(encoding='utf-8')
    except Exception:
        pass

    conn = sqlite3.connect('data/merchants.db')
    conn.row_factory = sqlite3.Row
    
    # 48 hours ago
    cutoff = time.time() - 48 * 3600
    cur = conn.execute("SELECT * FROM merchant_reviews WHERE exchange='OKX' AND updated_at > ?", (cutoff,))
    rows = cur.fetchall()
    print(f"Recent OKX review records (count: {len(rows)}):")
    for r in rows[:15]:
        print(dict(r))

if __name__ == '__main__':
    main()
