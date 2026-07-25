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
        print("No OKX session found!")
        return

    cookies = json.loads(row['cookies_json'] or "{}")
    
    # Real merchant ID
    m_id = "6f9658b1d8"
    
    urls = [
        f"https://www.okx.com/api/v5/c2c/partner/review/history?publicUserId={m_id}&type=2&limit=20",
        f"https://www.okx.com/api/v5/c2c/order/user-feedback?userId={m_id}&type=2&limit=20",
        # Maybe v3 c2c partner review history but under /api/v5 or /api/v3?
        f"https://www.okx.com/api/v3/c2c/partner/review/history?publicUserId={m_id}&type=2&limit=20"
    ]
    
    # Use exact User-Agent from webhook or chrome120 impersonation
    async with AsyncSession(impersonate="chrome120") as session:
        # We only pass the cookies
        for url in urls:
            print(f"\nFetching: {url}")
            try:
                resp = await session.get(url, cookies=cookies, timeout=10)
                print(f"  Status Code: {resp.status_code}")
                text = resp.text.encode('utf-8', errors='replace').decode('utf-8')
                print(f"  Snippet: {text[:300]}")
            except Exception as e:
                print(f"  Error: {e}")

if __name__ == '__main__':
    asyncio.run(main())
