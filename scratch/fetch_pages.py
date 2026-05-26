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
    
    async with AsyncSession(impersonate="chrome124") as session:
        all_reviews = []
        for page in range(1, 9):
            payload = {"userNo": m_id, "page": page, "rows": 10}
            try:
                resp = await session.post(url, json=payload, headers=req_headers, cookies=cookies, timeout=10)
                data = resp.json()
                reviews = data.get("data") or []
                print(f"Page {page} returned {len(reviews)} reviews.")
                if not reviews:
                    break
                all_reviews.extend(reviews)
            except Exception as e:
                print(f"Page {page} failed: {e}")
                break
                
        print(f"\nFetched {len(all_reviews)} total reviews across pages.")
        
        # Check ratings and comments
        non_standard = []
        for r in all_reviews:
            rating = r.get("rating")
            comments = r.get("comments")
            if rating != 1 or comments:
                non_standard.append(r)
                
        with open("scratch/non_standard_reviews.json", "w", encoding="utf-8") as f:
            json.dump(non_standard, f, indent=2, ensure_ascii=False)
        print("Successfully saved non-standard reviews to scratch/non_standard_reviews.json")

if __name__ == '__main__':
    asyncio.run(main())
