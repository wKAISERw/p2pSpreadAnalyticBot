import sqlite3
import json
import asyncio
import time
from curl_cffi.requests import AsyncSession

async def main():
    import sys
    try:
        sys.stdout.reconfigure(encoding='utf-8')
    except Exception:
        pass
    conn = sqlite3.connect('data/merchants.db')
    conn.row_factory = sqlite3.Row
    cur = conn.execute("SELECT headers_json, cookies_json FROM auth_sessions WHERE exchange='OKX' AND is_active=1")
    row = cur.fetchone()
    if not row:
        print("No active OKX session found in DB!")
        return

    db_headers = json.loads(row['headers_json'] or "{}")
    cookies = json.loads(row['cookies_json'] or "{}")
    
    # We will construct headers similar to what the browser sent, but reusing the auth/cookies from DB
    url = f"https://www.okx.com/v3/c2c/review/history?t={int(time.time() * 1000)}"
    
    # Try with exact headers from DB first
    headers = {
        "accept": "application/json",
        "content-type": "application/json",
        "app-type": "web",
        "x-locale": "ru_RU",
        "referer": "https://www.okx.com/ru/p2p/ads-merchant?publicUserId=6f9658b1d8",
    }
    
    # Copy authorization, devid, x-id-group, x-site-info from DB headers if present
    for key in ['authorization', 'devid', 'x-id-group', 'x-site-info', 'user-agent', 'x-client-signature', 'x-client-signature-version']:
        val = db_headers.get(key) or db_headers.get(key.lower()) or db_headers.get(key.upper())
        if val:
            headers[key] = val
            
    # If authorization not in headers, let's search db_headers keys
    print("DB headers keys:", list(db_headers.keys()))
    if 'authorization' not in headers:
        for k, v in db_headers.items():
            if k.lower() == 'authorization':
                headers['authorization'] = v
                break

    import sys
    pub_user_id = sys.argv[1] if len(sys.argv) > 1 else "dfb51f6677"
    
    score_types = [
        "",
        1,
        2,
        0,
        -1,
        "1",
        "2",
        None,
        "GOOD",
        "BAD"
    ]
    
    async with AsyncSession(impersonate="chrome124") as session:
        for st in score_types:
            payload = {
                "currentPage": 1,
                "hasComment": False,
                "pageSize": 10,
                "reviewFromBuyer": True,
                "reviewScoreType": st,
                "pubUserId": pub_user_id
            }
            print(f"\nPosting with reviewScoreType={repr(st)} ({type(st).__name__}):")
            try:
                resp = await session.post(url, json=payload, headers=headers, cookies=cookies, timeout=10)
                if resp.status_code != 200:
                    print(f"  Failed with status {resp.status_code}")
                    continue
                data = resp.json()
                item = data.get("data", {}).get("item", {})
                history = item.get('reviewHistoryDetail', [])
                print(f"  Code: {data.get('code')}, allCount: {item.get('allCount')}, positiveCount: {item.get('positiveCount')}, negativeCount: {item.get('negativeCount')}")
                print(f"  Details count returned: {len(history)}")
                if history:
                    scores_returned = [r.get('score') for r in history]
                    print(f"    Returned scores: {scores_returned}")
            except Exception as e:
                print(f"  Error: {e}")

if __name__ == '__main__':
    asyncio.run(main())
