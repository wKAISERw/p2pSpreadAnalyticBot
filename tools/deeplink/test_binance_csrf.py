import json
import sqlite3
import sys
from curl_cffi.requests import AsyncSession

sys.stdout.reconfigure(encoding="utf-8")

async def test_binance_share():
    conn = sqlite3.connect("data/merchants.db")
    c = conn.cursor()
    c.execute("SELECT headers_json, cookies_json FROM auth_sessions WHERE exchange = 'Binance' AND user_id = 1115620363")
    row = c.fetchone()
    conn.close()
    
    headers = json.loads(row[0] or "{}")
    cookies = json.loads(row[1] or "{}")
    
    print("=== TESTING BINANCE SHARE API WITH FULL HEADERS ===")
    print("Cookies present:", [k for k in ["p20t", "cr00", "d1og", "bnc-uuid"] if k in cookies])
    
    req_headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
        "Accept": "*/*",
        "Accept-Language": "uk-UA,uk;q=0.9,en-US;q=0.8,en;q=0.7",
        "Content-Type": "application/json",
        "clienttype": "web",
        "bnc-uuid": cookies.get("bnc-uuid", ""),
        "csrftoken": cookies.get("cr00", ""),
        "X-CSRF-TOKEN": cookies.get("cr00", ""),
    }
    
    url = "https://c2c.binance.com/bapi/c2c/v1/private/c2c/share/advertiser-share"
    payload = {
        "advertiserNo": "s40c6bb83ac363d02a2f4cf9bd2f23645",
        "shareType": "ADVERTISER"
    }
    
    async with AsyncSession(impersonate="chrome124") as s:
        res = await s.post(url, json=payload, headers=req_headers, cookies=cookies)
        print(f"HTTP Status: {res.status_code}")
        print("Response Text:", res.content.decode("utf-8", errors="ignore"))

if __name__ == "__main__":
    import asyncio
    asyncio.run(test_binance_share())
