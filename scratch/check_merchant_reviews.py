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
    cur = conn.execute("SELECT * FROM merchant_reviews WHERE merchant_id='869e25f6de'")
    row = cur.fetchone()
    if not row:
        print("No reviews record found in DB for merchant 869e25f6de")
        return

    print("Merchant 869e25f6de:")
    print(dict(row))

if __name__ == '__main__':
    main()
