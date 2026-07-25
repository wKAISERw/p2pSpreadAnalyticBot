import sqlite3
import json
import asyncio
import sys
from curl_cffi.requests import AsyncSession

async def main():
    try:
        sys.stdout.reconfigure(encoding='utf-8')
    except Exception:
        pass

    conn = sqlite3.connect('data/merchants.db')
    conn.row_factory = sqlite3.Row
    cur = conn.execute("SELECT headers_json, cookies_json FROM auth_sessions WHERE exchange='Binance' AND is_active=1")
    row = cur.fetchone()
    if not row:
        print("No active Binance session found in DB!")
        return

    headers = json.loads(row['headers_json'] or "{}")
    cookies = json.loads(row['cookies_json'] or "{}")
    
    m_id = "sa90446076d7c38cf995068caca25d822"  # kievbond
    
    # Let's clean up request headers
    req_headers = dict(headers)
    req_headers.pop("Content-Length", None)
    req_headers.pop("Accept-Encoding", None)
    req_headers["Referer"] = f"https://c2c.binance.com/uk-UA/advertiserDetail?advertiserNo={m_id}"
    
    payload = {"advertiserNo": m_id, "page": 1, "rows": 10, "reviewType": "ALL"} # Test ALL to see if there are any reviews
    
    url = "https://p2p.binance.com/bapi/c2c/v1/friendly/c2c/review/list-by-page"
    
    async with AsyncSession(impersonate="chrome124") as session:
        print(f"Requesting Binance reviews URL: {url}")
        try:
            resp = await session.post(url, json=payload, headers=req_headers, cookies=cookies, timeout=10)
            print(f"Status Code: {resp.status_code}")
            data = resp.json()
            print("Response JSON:")
            print(json.dumps(data, indent=2, ensure_ascii=False))
        except Exception as e:
            print(f"Failed: {e}")

if __name__ == '__main__':
    asyncio.run(main())
