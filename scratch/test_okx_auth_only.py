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
        print("No active OKX session found!")
        return

    headers = json.loads(row['headers_json'] or "{}")
    cookies = json.loads(row['cookies_json'] or "{}")
    
    # We keep only Authorization, User-Agent, and basic headers.
    # We strip signature, timestamp, and site-info to avoid verification errors.
    clean_headers = {
        "User-Agent": headers.get("user-agent", "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"),
        "Accept": "application/json",
        "Content-Type": "application/json"
    }
    if "authorization" in headers:
        clean_headers["Authorization"] = headers["authorization"]
    elif "Authorization" in headers:
        clean_headers["Authorization"] = headers["Authorization"]
        
    print("Clean Headers:", list(clean_headers.keys()))
    
    # Real merchant ID
    m_id = "6f9658b1d8"
    
    urls = [
        f"https://www.okx.com/api/v5/c2c/order/user-feedback?userId={m_id}&type=2&limit=20",
        f"https://www.okx.com/api/v5/c2c/partner/review/history?publicUserId={m_id}&type=2&limit=20"
    ]
    
    async with AsyncSession(impersonate="chrome120") as session:
        for url in urls:
            print(f"\nFetching: {url}")
            try:
                resp = await session.get(url, headers=clean_headers, cookies=cookies, timeout=10)
                print(f"  Status Code: {resp.status_code}")
                text = resp.text.encode('utf-8', errors='replace').decode('utf-8')
                print(f"  Response JSON/HTML (first 300 chars): {text[:300]}")
            except Exception as e:
                print(f"  Error: {e}")

if __name__ == '__main__':
    asyncio.run(main())
