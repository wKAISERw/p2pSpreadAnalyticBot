import sys
sys.stdout.reconfigure(encoding='utf-8')
import asyncio
import aiohttp
import time

async def main():
    url = "https://www.okx.com/v3/c2c/tradingOrders/getMarketplaceAdsPrelogin"
    params = {
        "fiatCurrency": "UAH",
        "cryptoCurrency": "USDT",
        "paymentMethod": "all",
        "side": "buy",
        "userType": "all",
        "sortType": "price_asc",
        "numberPerPage": "10",
        "t": str(int(time.time() * 1000)),
    }
    async with aiohttp.ClientSession() as session:
        async with session.get(url, params=params) as resp:
            data = await resp.json()
            ads = data.get("data", {}).get("buy", [])
            print(f"Total ads: {len(ads)}")
            for ad in ads:
                print(f"Merchant: {ad.get('nickName')} | Price: {ad.get('price')}")
                methods = ad.get("paymentMethods", [])
                for m in methods:
                    print(f"  Method: {m}")
                    
if __name__ == "__main__":
    asyncio.run(main())
