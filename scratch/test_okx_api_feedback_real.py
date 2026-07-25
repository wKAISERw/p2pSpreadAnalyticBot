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

    # Load OKX API credentials for user_id = 0
    from core.storage.user_repo import decrypt
    conn = sqlite3.connect('data/merchants.db')
    conn.row_factory = sqlite3.Row
    cur = conn.execute("SELECT api_key, api_secret, passphrase FROM user_credentials WHERE user_id=0 AND exchange='OKX'")
    row = cur.fetchone()
    if not row:
        print("No OKX credentials found in DB!")
        return

    api_key = decrypt(row['api_key'])
    api_secret = decrypt(row['api_secret'])
    passphrase = decrypt(row['passphrase']) if row['passphrase'] else ""
    
    client = OkxClient()
    client.set_credentials(api_key, api_secret, passphrase)
    
    async with client:
        merchant_id = "b72f033d0a"  # DARKSElD
        path = f"/api/v5/c2c/order/user-feedback?userId={merchant_id}&type=2&limit=20"
        url = f"https://aws.okx.com{path}"
        headers = client._sign_headers("GET", path)
        
        print(f"Requesting URL: {url}")
        try:
            resp = await client._get(url, headers=headers)
            print("Response JSON:")
            print(json.dumps(resp, indent=2))
        except Exception as e:
            print(f"API Request Failed: {e}")

if __name__ == '__main__':
    asyncio.run(main())
