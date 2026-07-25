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
    conn.close()
    
    m_id = "sa90446076d7c38cf995068caca25d822"  # kievbond
    
    req_headers = dict(headers)
    req_headers.pop("Content-Length", None)
    req_headers.pop("Accept-Encoding", None)
    req_headers["Referer"] = f"https://c2c.binance.com/uk-UA/advertiserDetail?advertiserNo={m_id}"
    
    url = "https://c2c.binance.com/bapi/c2c/v1/friendly/c2c/review/list-by-page"
    
    # We will test different filtering payloads
    payloads = [
        {"userNo": m_id, "page": 1, "rows": 10, "type": 2},
        {"userNo": m_id, "page": 1, "rows": 10, "type": "NEGATIVE"},
        {"userNo": m_id, "page": 1, "rows": 10, "rating": 3},
        {"userNo": m_id, "page": 1, "rows": 10, "ratings": [3]},
        {"userNo": m_id, "page": 1, "rows": 10, "reviewType": 3},
        {"userNo": m_id, "page": 1, "rows": 10, "reviewType": "3"},
        # What if it's reviewType: "NEGATIVE"? We already know it returns rating: 1. Let's see if we missed something.
    ]
    
    async with AsyncSession(impersonate="chrome124") as session:
        for i, p in enumerate(payloads):
            try:
                resp = await session.post(url, json=p, headers=req_headers, cookies=cookies, timeout=10)
                data = resp.json()
                reviews = data.get("data") or []
                ratings = [r.get("rating") for r in reviews]
                print(f"Payload {i}: {p} -> Status: {resp.status_code}, code: {data.get('code')}, count: {len(reviews)}, ratings in response: {ratings}")
            except Exception as e:
                print(f"Payload {i} failed: {e}")

if __name__ == '__main__':
    asyncio.run(main())
