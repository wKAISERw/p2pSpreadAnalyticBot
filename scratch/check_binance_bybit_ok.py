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
    
    print("--- Successful Binance Reviews in DB ---")
    cur = conn.execute("SELECT merchant_id, positive_count, negative_count, bad_texts_json FROM merchant_reviews WHERE exchange='Binance' AND status='OK' AND (positive_count > 0 OR negative_count > 0 OR json_array_length(bad_texts_json) > 0) LIMIT 5")
    rows = cur.fetchall()
    for r in rows:
        print(f"Merchant: {r['merchant_id']}, Pos: {r['positive_count']}, Neg: {r['negative_count']}, Bad Texts: {r['bad_texts_json'][:150]}")
        
    print("\n--- Successful Bybit Reviews in DB ---")
    cur = conn.execute("SELECT merchant_id, positive_count, negative_count, bad_texts_json FROM merchant_reviews WHERE exchange='Bybit' AND status='OK' AND (positive_count > 0 OR negative_count > 0 OR json_array_length(bad_texts_json) > 0) LIMIT 5")
    rows = cur.fetchall()
    for r in rows:
        print(f"Merchant: {r['merchant_id']}, Pos: {r['positive_count']}, Neg: {r['negative_count']}, Bad Texts: {r['bad_texts_json'][:150]}")

if __name__ == '__main__':
    main()
