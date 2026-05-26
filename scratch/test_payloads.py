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
    
    payloads = [
        {"advertiserNo": m_id, "page": 1, "rows": 10},
        {"advertiserNo": m_id, "page": 1, "rows": 10, "reviewType": "NEGATIVE"},
        {"advertiserNo": m_id, "page": 1, "rows": 10, "reviewType": "BAD"},
        {"advertiserNo": m_id, "page": 1, "rows": 10, "reviewType": "ALL"},
        {"advertiserNo": m_id, "page": 1, "rows": 10, "reviewType": "negative"},
        {"advertiserNo": m_id, "page": 1, "rows": 10, "reviewType": "bad"},
        {"advertiserNo": m_id, "page": 1, "rows": 10, "reviewType": "all"},
        # Let's also check feedback-list endpoint with session
        {"advertiserNo": m_id, "type": 2, "page": 1, "rows": 10},
    ]
    
    urls = [
        "https://c2c.binance.com/bapi/c2c/v1/friendly/c2c/review/list-by-page",
        "https://c2c.binance.com/bapi/c2c/v2/friendly/c2c/review/list-by-page",
        "https://c2c.binance.com/bapi/c2c/v1/friendly/c2c/user/feedback-list",
        "https://c2c.binance.com/bapi/c2c/v2/friendly/c2c/user/feedback-list"
    ]
    
    async with AsyncSession(impersonate="chrome124") as session:
        for url in urls:
            print(f"\n===== TESTING URL: {url} =====")
            for p in payloads:
                # Adjust fields based on endpoint name
                payload_to_send = dict(p)
                if "feedback-list" in url:
                    # feedback-list typically uses type instead of reviewType
                    if "reviewType" in payload_to_send:
                        # map reviewType to type
                        rt = payload_to_send.pop("reviewType")
                        if rt == "NEGATIVE":
                            payload_to_send["type"] = 2
                        elif rt == "ALL":
                            payload_to_send["type"] = 0
                        else:
                            continue
                else:
                    if "type" in payload_to_send:
                        continue
                
                try:
                    resp = await session.post(url, json=payload_to_send, headers=req_headers, cookies=cookies, timeout=5)
                    data = resp.json()
                    total = data.get("total") or len(data.get("data") or [])
                    print(f"Payload {payload_to_send} -> Status: {resp.status_code}, code: {data.get('code')}, total: {total}")
                    if total > 0:
                        print(f"First item: {data.get('data')[0] if isinstance(data.get('data'), list) and data.get('data') else data}")
                except Exception as e:
                    print(f"Payload {payload_to_send} -> Error: {e}")

if __name__ == '__main__':
    asyncio.run(main())
