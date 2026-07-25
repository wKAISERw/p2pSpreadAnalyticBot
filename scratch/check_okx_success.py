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
    cur = conn.execute("SELECT * FROM merchant_reviews WHERE exchange='OKX' AND status='OK' AND (positive_count > 0 OR negative_count > 0) LIMIT 10")
    rows = cur.fetchall()
    if not rows:
        print("No OKX merchants with reviews found!")
        return

    print("OKX Merchants with reviews:")
    for r in rows:
        print(f"Merchant: {r['merchant_id']}, Pos: {r['positive_count']}, Neg: {r['negative_count']}, Status: {r['status']}, Bad Texts: {r['bad_texts_json']}")

if __name__ == '__main__':
    main()
