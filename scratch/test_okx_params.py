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

    headers = json.loads(row['headers_json'] or "{}")
    cookies = json.loads(row['cookies_json'] or "{}")
    
    m_id = "6f9658b1d8"
    
    # Try different parameters
    params = [
        {"publicUserId": m_id, "type": "2", "limit": "20"},
        {"userId": m_id, "type": "2", "limit": "20"},
        {"merchantId": m_id, "type": "2", "limit": "20"},
        {"partnerUserId": m_id, "type": "2", "limit": "20"},
        {"targetUserId": m_id, "type": "2", "limit": "20"},
        {"id": m_id, "type": "2", "limit": "20"},
        # No params at all
        {}
    ]
    
    async with AsyncSession(impersonate="chrome124") as session:
        session.headers.update(headers)
        for h in ['Host', 'content-length', 'Content-Length', 'connection', 'Connection']:
            session.headers.pop(h, None)
            
        for param in params:
            url = "https://www.okx.com/v3/c2c/partner/review/history"
            print(f"\nTrying with params: {param}")
            try:
                resp = await session.get(url, params=param, cookies=cookies, timeout=10)
                print(f"  Status Code: {resp.status_code}")
                text = resp.text.encode('utf-8', errors='replace').decode('utf-8')
                print(f"  Response: {text}")
            except Exception as e:
                print(f"  Error: {e}")

if __name__ == '__main__':
    asyncio.run(main())
