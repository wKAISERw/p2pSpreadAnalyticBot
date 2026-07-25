import sqlite3
import json
import asyncio
from curl_cffi.requests import AsyncSession

async def main():
    conn = sqlite3.connect('data/merchants.db')
    conn.row_factory = sqlite3.Row
    cur = conn.execute("SELECT headers_json, cookies_json FROM auth_sessions WHERE exchange='Binance' AND is_active=1")
    row = cur.fetchone()
    if not row:
        print("No active Binance session found in DB!")
        return

    headers = json.loads(row['headers_json'] or "{}")
    cookies = json.loads(row['cookies_json'] or "{}")
    
    req_headers = {
        "accept": "application/json",
        "user-agent": headers.get("user-agent", "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"),
        "referer": "https://c2c.binance.com/",
    }
    for k in ("csrftoken", "bnc-uuid", "fvideo-id", "fvideo-token"):
        v = headers.get(k) or headers.get(k.upper())
        if v:
            req_headers[k] = v
            
    url = "https://c2c.binance.com/bapi/c2c/v1/friendly/c2c/user/get-profile"
    
    async with AsyncSession(impersonate="chrome124") as session:
        print(f"Requesting Binance get-profile: {url}")
        try:
            resp = await session.get(url, headers=req_headers, cookies=cookies, timeout=8)
            print(f"Status Code: {resp.status_code}")
            data = resp.json()
            print("Response JSON:")
            print(json.dumps(data, indent=2, ensure_ascii=False))
        except Exception as e:
            print(f"Failed: {e}")

if __name__ == '__main__':
    asyncio.run(main())
