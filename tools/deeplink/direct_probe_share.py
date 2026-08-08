import json
import sqlite3
import sys
from curl_cffi.requests import AsyncSession

sys.stdout.reconfigure(encoding="utf-8")

async def probe_binance():
    conn = sqlite3.connect("data/merchants.db")
    c = conn.cursor()
    c.execute("SELECT headers_json, cookies_json FROM auth_sessions WHERE exchange = 'Binance' AND user_id = 1115620363")
    row = c.fetchone()
    conn.close()
    
    if not row:
        print("No active Binance session found for 1115620363")
        return
        
    headers = json.loads(row[0] or "{}")
    cookies = json.loads(row[1] or "{}")
    
    headers["clienttype"] = "web"
    if "cr00" in cookies:
        headers["csrftoken"] = cookies["cr00"]
    elif "bnc-uuid" in cookies:
        headers["csrftoken"] = cookies["bnc-uuid"]
    
    print("=== PROBING BINANCE C2C SHARE ENDPOINTS (Active Session 1115620363) ===")
    print(f"Loaded {len(headers)} headers, {len(cookies)} cookies")
    
    # Binance P2P share endpoint
    url = "https://c2c.binance.com/bapi/c2c/v1/private/c2c/share/advertiser-share"
    payload = {
        "advertiserNo": "s40c6bb83ac363d02a2f4cf9bd2f23645",
        "shareType": "ADVERTISER"
    }
    
    async with AsyncSession(impersonate="chrome124") as s:
        res = await s.post(url, json=payload, headers=headers, cookies=cookies)
        print(f"HTTP Status: {res.status_code}")
        print("Response Text:", res.content.decode("utf-8", errors="ignore"))

async def probe_okx():
    conn = sqlite3.connect("data/merchants.db")
    c = conn.cursor()
    c.execute("SELECT headers_json, cookies_json FROM auth_sessions WHERE exchange = 'OKX' AND user_id = 1115620363")
    row = c.fetchone()
    conn.close()
    
    if not row:
        print("No active OKX session found for 1115620363")
        return
        
    headers = json.loads(row[0] or "{}")
    cookies = json.loads(row[1] or "{}")
    
    print("\n=== PROBING OKX C2C SHARE ENDPOINTS (Active Session 1115620363) ===")
    print(f"Loaded {len(headers)} headers, {len(cookies)} cookies")
    
    url = "https://www.okx.com/v3/c2c/merchant/share"
    params = {"pubUserId": "005977cb24"}
    
    async with AsyncSession(impersonate="chrome124") as s:
        res = await s.get(url, params=params, headers=headers, cookies=cookies)
        print(f"HTTP Status: {res.status_code}")
        print("Response Text:", res.content.decode("utf-8", errors="ignore"))

if __name__ == "__main__":
    import asyncio
    asyncio.run(probe_binance())
    asyncio.run(probe_okx())
