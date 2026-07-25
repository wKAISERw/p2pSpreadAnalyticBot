import asyncio
import sys
from infrastructure.http.wallet_client import WalletClient

async def check_wallet():
    print("=== Wallet Ads with WalletClient ===")
    payload = {
        "cryptoCurrency": "USDT",
        "fiatCurrency": "UAH",
        "side": "SELL",
        "page": 1,
        "pageSize": 20
    }
    url = "https://p2p.walletbot.me/p2p/integration-api/v1/item/online"
    async with WalletClient() as client:
        try:
            data = await client.fetch(url, payload)
            items = data.get("data", [])
            print(f"Fetched {len(items)} ads from Wallet")
            if items:
                print("Item keys:", list(items[0].keys()))
                online_fields = {k: v for k, v in items[0].items() if "online" in k.lower() or "active" in k.lower() or "last" in k.lower()}
                print("Item online fields:", online_fields)
                # print first item completely
                print("Full item excerpt (first 25 keys/values):")
                for k in list(items[0].keys())[:25]:
                    print(f"  {k}: {items[0][k]}")
        except Exception as e:
            print("Wallet Error:", e)

async def main():
    sys.stdout.reconfigure(encoding='utf-8')
    await check_wallet()

if __name__ == "__main__":
    asyncio.run(main())
