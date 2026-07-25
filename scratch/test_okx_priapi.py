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
    
    # Real merchant ID
    m_id = "6f9658b1d8"
    
    paths = [
        f"/priapi/v5/c2c/partner/review/history?publicUserId={m_id}&type=2&limit=20",
        f"/priapi/v3/c2c/partner/review/history?publicUserId={m_id}&type=2&limit=20",
        f"/priapi/v1/c2c/partner/review/history?publicUserId={m_id}&type=2&limit=20",
        f"/priapi/v5/c2c/review/history?publicUserId={m_id}&type=2&limit=20",
        f"/priapi/v3/c2c/review/history?publicUserId={m_id}&type=2&limit=20"
    ]
    
    async with AsyncSession(impersonate="chrome124") as session:
        session.headers.update(headers)
        for h in ['Host', 'content-length', 'Content-Length', 'connection', 'Connection']:
            session.headers.pop(h, None)
            
        for path in paths:
            url = f"https://www.okx.com{path}"
            print(f"\nTrying path: {path}")
            try:
                resp = await session.get(url, cookies=cookies, timeout=10)
                print(f"  Status Code: {resp.status_code}")
                ct = resp.headers.get('content-type', '')
                print(f"  Content-Type: {ct}")
                text = resp.text.encode('utf-8', errors='replace').decode('utf-8')
                print(f"  Snippet: {text[:300]}")
            except Exception as e:
                err_str = str(e).encode('utf-8', errors='replace').decode('utf-8')
                print(f"  Error: {err_str}")

if __name__ == '__main__':
    asyncio.run(main())
