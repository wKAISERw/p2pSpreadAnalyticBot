import sqlite3
import json
import asyncio
import sys
from infrastructure.http.okx_client import OkxClient

async def main():
    try:
        sys.stdout.reconfigure(encoding='utf-8')
    except Exception:
        pass

    # Load OKX API credentials
    from core.storage.user_repo import decrypt
    conn = sqlite3.connect('data/merchants.db')
    conn.row_factory = sqlite3.Row
    cur = conn.execute("SELECT api_key, api_secret, passphrase FROM user_credentials WHERE user_id=0 AND exchange='OKX'")
    row = cur.fetchone()
    if not row:
        print("No OKX credentials found!")
        return

    api_key = decrypt(row['api_key'])
    api_secret = decrypt(row['api_secret'])
    passphrase = decrypt(row['passphrase']) if row['passphrase'] else ""
    
    client = OkxClient()
    client.set_credentials(api_key, api_secret, passphrase)
    
    async with client:
        # Test 1: Fetch Account Balance verbose
        path1 = "/api/v5/account/balance?ccy=USDT,UAH"
        url1 = f"https://www.okx.com{path1}"
        headers1 = client._sign_headers("GET", path1)
        print("\n--- Testing Fetch Account Balance Verbose ---")
        try:
            resp = await client._get(url1, headers=headers1)
            print("Balance response:", json.dumps(resp, indent=2))
        except Exception as e:
            print("Balance failed:", e)

        # Test 2: Fetch Merchant Profile verbose
        merchant_id = "b72f033d0a"
        path2 = f"/api/v5/c2c/order/user-info?userId={merchant_id}"
        url2 = f"https://www.okx.com{path2}"
        headers2 = client._sign_headers("GET", path2)
        print(f"\n--- Testing Fetch Merchant Profile Verbose {merchant_id} ---")
        try:
            resp = await client._get(url2, headers=headers2)
            print("Profile response:", json.dumps(resp, indent=2))
        except Exception as e:
            print("Profile failed:", e)

if __name__ == '__main__':
    asyncio.run(main())
