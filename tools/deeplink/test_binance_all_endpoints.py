import json
import sqlite3
import sys
from curl_cffi.requests import AsyncSession

sys.stdout.reconfigure(encoding="utf-8")

async def main():
    conn = sqlite3.connect("data/merchants.db")
    c = conn.cursor()
    c.execute("SELECT headers_json, cookies_json FROM auth_sessions WHERE exchange = 'Binance' AND user_id = 1115620363")
    row = c.fetchone()
    conn.close()

    headers = json.loads(row[0] or "{}")
    cookies = json.loads(row[1] or "{}")
    
    # Ensure X-CSRF-TOKEN is set from cr00
    if "cr00" in cookies:
        headers["X-CSRF-TOKEN"] = cookies["cr00"]
        headers["csrftoken"] = cookies["cr00"]

    print(f"Testing Binance Share endpoints with {len(headers)} headers and {len(cookies)} cookies...\n")
    
    endpoints = [
        ("POST", "https://c2c.binance.com/bapi/c2c/v1/private/c2c/share/advertiser-share", {"advertiserNo": "s40c6bb83ac363d02a2f4cf9bd2f23645", "shareType": "ADVERTISER"}),
        ("POST", "https://c2c.binance.com/bapi/c2c/v1/private/c2c/share/adv-share", {"advNo": "11523456789012345678", "shareType": "ADV"}),
        ("GET", "https://c2c.binance.com/bapi/c2c/v1/private/c2c/user/profile", None),
        ("POST", "https://c2c.binance.com/bapi/c2c/v1/private/c2c/user/base-detail", {}),
        ("POST", "https://c2c.binance.com/bapi/c2c/v1/friendly/c2c/user/get-profile", {}),
    ]
    
    async with AsyncSession(impersonate="chrome124") as s:
        for method, url, body in endpoints:
            if method == "POST":
                res = await s.post(url, json=body, headers=headers, cookies=cookies)
            else:
                res = await s.get(url, headers=headers, cookies=cookies)
            
            print(f"[{method}] {url}")
            print(f"  Status: {res.status_code}")
            print(f"  Body:   {res.content.decode('utf-8', errors='ignore')[:250]}\n")

if __name__ == "__main__":
    import asyncio
    asyncio.run(main())
