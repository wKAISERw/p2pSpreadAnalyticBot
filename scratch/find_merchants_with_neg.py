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
    
    print("--- Searching for merchants with negative reviews ---")
    cur = conn.execute("SELECT exchange, merchant_id, positive_count, negative_count, bad_texts_json, status FROM merchant_reviews WHERE (negative_count > 0 OR bad_texts_json != '[]') AND status='OK' LIMIT 10")
    for r in cur.fetchall():
        print(f"Exchange: {r['exchange']}, Merchant: {r['merchant_id']}, Pos: {r['positive_count']}, Neg: {r['negative_count']}, Status: {r['status']}, Bad Texts: {r['bad_texts_json'][:150]}")

if __name__ == '__main__':
    main()
