import asyncio
import sys
from infrastructure.http.mexc_client import MexcClient

async def check_mexc():
    print("=== MEXC Ads with MexcClient ===")
    payload = {
        "adsType": "1",
        "allowTrade": "false",
        "amount": "",
        "blockTrade": "false",
        "certifiedMerchant": "false",
        "coinId": "128f589271cb4951b03e71e6323eb7be",
        "countryCode": "",
        "currency": "UAH",
        "follow": "false",
        "haveTrade": "false",
        "page": "1",
        "payMethod": "", 
        "tradeType": "SELL"
    }
    async with MexcClient() as client:
        try:
            data = await client.fetch(payload)
            items = data.get("data", [])
            print(f"Fetched {len(items)} ads from MEXC")
            if items:
                print("Item keys:", list(items[0].keys()))
                merchant = items[0].get("merchant", {})
                print("Merchant keys:", list(merchant.keys()))
                stats = items[0].get("merchantStatistics", {})
                print("Stats keys:", list(stats.keys()))
                online_fields = {k: v for k, v in items[0].items() if "online" in k.lower() or "active" in k.lower() or "last" in k.lower()}
                print("Item online fields:", online_fields)
                merch_online = {k: v for k, v in merchant.items() if "online" in k.lower() or "active" in k.lower() or "last" in k.lower()}
                print("Merchant online fields:", merch_online)
        except Exception as e:
            print("MEXC Error:", e)

async def main():
    sys.stdout.reconfigure(encoding='utf-8')
    await check_mexc()

if __name__ == "__main__":
    asyncio.run(main())
