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
        # Test 1: Fetch Account Balance
        print("\n--- Testing Fetch Account Balance ---")
        try:
            balance = await client.fetch_account_balance()
            print("Balance result:", balance)
        except Exception as e:
            print("Balance failed:", e)
            
        # Test 2: Fetch P2P User Info (Merchant Profile)
        merchant_id = "b72f033d0a"
        print(f"\n--- Testing Fetch Merchant Profile {merchant_id} ---")
        try:
            profile = await client.fetch_merchant_profile(merchant_id)
            print("Profile result keys:", list(profile.keys()) if profile else "Empty dict")
            print("Profile details:", str(profile)[:300])
        except Exception as e:
            print("Profile failed:", e)

if __name__ == '__main__':
    asyncio.run(main())
