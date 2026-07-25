import sqlite3
import json

def main():
    conn = sqlite3.connect('data/merchants.db')
    conn.row_factory = sqlite3.Row
    cur = conn.execute("SELECT headers_json, cookies_json FROM auth_sessions WHERE exchange='Binance' AND is_active=1")
    row = cur.fetchone()
    if not row:
        print("No active Binance session found!")
        return

    headers = json.loads(row['headers_json'] or "{}")
    cookies = json.loads(row['cookies_json'] or "{}")
    
    print("--- Stored Binance Headers (Keys & Value Lengths) ---")
    for k, v in headers.items():
        print(f"{k}: length={len(str(v))}")
        
    print("\n--- Stored Binance Cookies (Keys & Value Lengths) ---")
    for k, v in cookies.items():
        print(f"{k}: length={len(str(v))}")

if __name__ == '__main__':
    main()
