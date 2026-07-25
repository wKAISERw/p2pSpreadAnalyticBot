import sqlite3
import datetime

def main():
    conn = sqlite3.connect('data/merchants.db')
    conn.row_factory = sqlite3.Row
    cur = conn.execute("SELECT is_active, updated_at, length(headers_json) as h_len, length(cookies_json) as c_len FROM auth_sessions WHERE exchange='Binance'")
    rows = cur.fetchall()
    print("Binance sessions:")
    for r in rows:
        dt = datetime.datetime.fromtimestamp(r['updated_at']) if r['updated_at'] else "N/A"
        print(f"Active: {r['is_active']}, Updated At: {dt}, Headers Len: {r['h_len']}, Cookies Len: {r['c_len']}")

if __name__ == '__main__':
    main()
