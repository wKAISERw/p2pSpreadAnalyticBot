import sqlite3
import sys

def main():
    try:
        sys.stdout.reconfigure(encoding='utf-8')
    except Exception:
        pass

    conn = sqlite3.connect('data/merchants.db')
    conn.row_factory = sqlite3.Row
    
    print("--- OKX Review Statuses in DB ---")
    cur = conn.execute("SELECT status, COUNT(*) as cnt FROM merchant_reviews WHERE exchange='OKX' GROUP BY status")
    for r in cur.fetchall():
        print(f"Status: {r['status']}, Count: {r['cnt']}")
        
    print("\n--- OKX API_ERROR/UNAVAILABLE Details ---")
    cur = conn.execute("SELECT merchant_id, status, error_reason, datetime(updated_at, 'unixepoch', 'localtime') as ts FROM merchant_reviews WHERE exchange='OKX' AND status IN ('API_ERROR', 'UNAVAILABLE') ORDER BY updated_at DESC LIMIT 5")
    for r in cur.fetchall():
        print(f"Time: {r['ts']}, Merchant: {r['merchant_id']}, Status: {r['status']}, Error: {r['error_reason']}")
        
    print("\n--- Bybit Review Statuses in DB ---")
    cur = conn.execute("SELECT status, COUNT(*) as cnt FROM merchant_reviews WHERE exchange='Bybit' GROUP BY status")
    for r in cur.fetchall():
        print(f"Status: {r['status']}, Count: {r['cnt']}")

    print("\n--- Binance Review Statuses in DB ---")
    cur = conn.execute("SELECT status, COUNT(*) as cnt FROM merchant_reviews WHERE exchange='Binance' GROUP BY status")
    for r in cur.fetchall():
        print(f"Status: {r['status']}, Count: {r['cnt']}")

    print("\n--- Bybit/Binance NO_SESSION Samples ---")
    cur = conn.execute("SELECT exchange, merchant_id, status, error_reason, datetime(updated_at, 'unixepoch', 'localtime') as ts FROM merchant_reviews WHERE exchange IN ('Binance', 'Bybit') ORDER BY updated_at DESC LIMIT 5")
    for r in cur.fetchall():
        print(f"Time: {r['ts']}, Exchange: {r['exchange']}, Merchant: {r['merchant_id']}, Status: {r['status']}, Error: {r['error_reason']}")

if __name__ == '__main__':
    main()
