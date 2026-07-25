import sqlite3
import json
import asyncio
from curl_cffi.requests import AsyncSession

async def main():
    conn = sqlite3.connect('data/merchants.db')
    conn.row_factory = sqlite3.Row
    cur = conn.execute("SELECT headers_json, cookies_json FROM auth_sessions WHERE exchange='OKX' AND is_active=1")
    row = cur.fetchone()
    if not row:
        print("No active OKX session found!")
        return

    headers = json.loads(row['headers_json'] or "{}")
    cookies = json.loads(row['cookies_json'] or "{}")
    
    # Real merchant IDs from DB or referer
    merchant_ids = ["6f9658b1d8", "0119460a01", "015d5414ed"]
    
    async with AsyncSession(impersonate="chrome124") as session:
        session.headers.update(headers)
        for h in ['Host', 'content-length', 'Content-Length', 'connection', 'Connection']:
            session.headers.pop(h, None)
            
        for m_id in merchant_ids:
            # Test /v3/c2c/partner/review/history
            url = f"https://www.okx.com/v3/c2c/partner/review/history?publicUserId={m_id}&type=2&limit=20"
            print(f"\nFetching: {url}")
            try:
                resp = await session.get(url, cookies=cookies, timeout=10)
                print(f"Status Code: {resp.status_code}")
                text = resp.text.encode('utf-8', errors='replace').decode('utf-8')
                print(f"Response snippet: {text[:300]}")
            except Exception as e:
                err_str = str(e).encode('utf-8', errors='replace').decode('utf-8')
                print(f"Error: {err_str}")

if __name__ == '__main__':
    asyncio.run(main())
