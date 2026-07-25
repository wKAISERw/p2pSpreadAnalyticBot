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
    
    m_id = "sa90446076d7c38cf995068caca25d822"  # kievbond
    
    req_headers = dict(headers)
    req_headers.pop("Content-Length", None)
    req_headers.pop("Accept-Encoding", None)
    req_headers["Referer"] = f"https://c2c.binance.com/uk-UA/advertiserDetail?advertiserNo={m_id}"
    
    # We want NEGATIVE reviews
    payload = {"userNo": m_id, "page": 1, "rows": 10, "reviewType": "NEGATIVE"}
    
    url = "https://c2c.binance.com/bapi/c2c/v1/friendly/c2c/review/list-by-page"
    
    async with AsyncSession(impersonate="chrome124") as session:
        try:
            resp = await session.post(url, json=payload, headers=req_headers, cookies=cookies, timeout=10)
            data = resp.json()
            with open("scratch/binance_reviews_resp.json", "w", encoding="utf-8") as f:
                json.dump(data, f, indent=2, ensure_ascii=False)
            print("Successfully saved response to scratch/binance_reviews_resp.json")
        except Exception as e:
            print(f"Failed to fetch or save: {e}")

if __name__ == '__main__':
    asyncio.run(main())
