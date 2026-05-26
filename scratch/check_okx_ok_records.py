import sqlite3
import json
import sys

def main():
    try:
        sys.stdout.reconfigure(encoding='utf-8')
    except Exception:
        pass

    conn = sqlite3.connect('data/merchants.db')
    conn.row_factory = sqlite3.Row
    cur = conn.execute("SELECT * FROM merchant_reviews WHERE exchange='OKX' AND status='OK' LIMIT 10")
    rows = cur.fetchall()
    print("OKX Status=OK sample records:")
    for r in rows:
        print(dict(r))

if __name__ == '__main__':
    main()
