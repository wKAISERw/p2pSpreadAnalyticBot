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
    
    # Fetch 80 rows to get all reviews
    payload = {"userNo": m_id, "page": 1, "rows": 80}
    url = "https://c2c.binance.com/bapi/c2c/v1/friendly/c2c/review/list-by-page"
    
    async with AsyncSession(impersonate="chrome124") as session:
        try:
            resp = await session.post(url, json=payload, headers=req_headers, cookies=cookies, timeout=10)
            data = resp.json()
            reviews = data.get("data") or []
            print(f"Total reviews in response data list: {len(reviews)}")
            
            # Find reviews with rating != 1 or with non-empty comments
            non_standard = []
            for r in reviews:
                rating = r.get("rating")
                comments = r.get("comments")
                if rating != 1 or comments:
                    non_standard.append(r)
                    
            print(f"Found {len(non_standard)} non-standard reviews:")
            for r in non_standard:
                print(f"Review ID: {r.get('reviewId')}, Rating: {r.get('rating')}, Comments: {repr(r.get('comments'))}, Nickname: {r.get('reviewer', {}).get('nickname')}")
                
        except Exception as e:
            print(f"Failed: {e}")

if __name__ == '__main__':
    asyncio.run(main())
