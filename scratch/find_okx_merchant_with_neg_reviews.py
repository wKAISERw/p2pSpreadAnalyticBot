import sqlite3
import json
import asyncio
import time
from curl_cffi.requests import AsyncSession

async def main():
    conn = sqlite3.connect('data/merchants.db')
    conn.row_factory = sqlite3.Row
    cur = conn.execute("SELECT DISTINCT merchant_id, merchant_name FROM merchant_snapshots WHERE exchange='OKX'")
    merchants = cur.fetchall()
    print(f"Loaded {len(merchants)} distinct OKX merchants.")
    
    cur_sess = conn.execute("SELECT headers_json, cookies_json FROM auth_sessions WHERE exchange='OKX' AND is_active=1")
    row = cur_sess.fetchone()
    if not row:
        print("No active OKX session found in DB!")
        return

    db_headers = json.loads(row['headers_json'] or "{}")
    cookies = json.loads(row['cookies_json'] or "{}")
    
    headers = {
        "accept": "application/json",
        "content-type": "application/json",
        "app-type": "web",
        "x-locale": "ru_RU",
    }
    for key in ['authorization', 'devid', 'x-id-group', 'x-site-info', 'user-agent', 'x-client-signature', 'x-client-signature-version']:
        val = db_headers.get(key) or db_headers.get(key.lower()) or db_headers.get(key.upper())
        if val:
            headers[key] = val

    async with AsyncSession(impersonate="chrome124") as session:
        for idx, m in enumerate(merchants):
            m_id = m['merchant_id']
            m_name = m['merchant_name']
            url = f"https://www.okx.com/v3/c2c/review/history?t={int(time.time() * 1000)}"
            payload = {
                "currentPage": 1,
                "hasComment": False,
                "pageSize": 5,
                "reviewFromBuyer": True,
                "reviewScoreType": "",
                "pubUserId": m_id
            }
            try:
                resp = await session.post(url, json=payload, headers=headers, cookies=cookies, timeout=5)
                if resp.status_code == 200:
                    data = resp.json()
                    item = data.get("data", {}).get("item", {})
                    neg = item.get("negativeCount", 0)
                    pos = item.get("positiveCount", 0)
                    all_cnt = item.get("allCount", 0)
                    print(f"[{idx}] Merchant {m_name} ({m_id}): Pos: {pos}, Neg: {neg}, All: {all_cnt}")
                    if neg > 0:
                        print(f"!!! FOUND ONE: {m_name} ({m_id}) has {neg} negative reviews!")
                        break
                else:
                    print(f"[{idx}] Merchant {m_name} ({m_id}) failed with status {resp.status_code}")
            except Exception as e:
                print(f"[{idx}] Merchant {m_name} ({m_id}) error: {e}")
            await asyncio.sleep(0.5)

if __name__ == '__main__':
    asyncio.run(main())
