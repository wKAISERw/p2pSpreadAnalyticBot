import sqlite3
import json
import sys
import asyncio
from curl_cffi.requests import AsyncSession

async def main():
    try:
        sys.stdout.reconfigure(encoding='utf-8')
    except Exception:
        pass

    conn = sqlite3.connect('data/merchants.db')
    conn.row_factory = sqlite3.Row
    cur = conn.execute("SELECT headers_json, cookies_json FROM auth_sessions WHERE exchange='OKX' AND is_active=1")
    row = cur.fetchone()
    if not row:
        print("No active OKX session found in DB!")
        return

    headers = json.loads(row['headers_json'] or "{}")
    cookies = json.loads(row['cookies_json'] or "{}")
    
    print("Captured Headers keys:", list(headers.keys()))
    print("Captured Cookies keys:", list(cookies.keys()))
    
    urls = [
        "https://www.okx.com/api/v5/c2c/partner/review/history?publicUserId=0e37a42aca&type=2&limit=20",
        "https://www.okx.com/api/v5/c2c/order/user-feedback?userId=0e37a42aca&type=2&limit=20"
    ]
    
    async with AsyncSession(impersonate="chrome124") as session:
        session.headers.update(headers)
        for h in ['Host', 'content-length', 'Content-Length', 'connection', 'Connection']:
            session.headers.pop(h, None)
            
        for url in urls:
            print(f"\nFetching: {url}")
            try:
                resp = await session.get(url, cookies=cookies, timeout=10)
                print(f"Status Code: {resp.status_code}")
                # Print response text snippet safely
                text = resp.text.encode('utf-8', errors='replace').decode('utf-8')
                print(f"Response snippet (first 300 chars): {text[:300]}")
            except Exception as e:
                err_str = str(e).encode('utf-8', errors='replace').decode('utf-8')
                print(f"Error fetching: {err_str}")

if __name__ == '__main__':
    asyncio.run(main())
