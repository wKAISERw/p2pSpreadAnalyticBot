import asyncio
import aiohttp
import time
import json
import sys

async def check_bybit():
    print("=== Bybit ===")
    url = "https://api2.bybit.com/fiat/otc/item/online"
    payload = {
        "userId": "",
        "tokenId": "USDT",
        "currencyId": "UAH",
        "payment": [],
        "side": "1",
        "size": "5",
        "page": "1",
        "amount": "",
        "authMaker": False,
        "canTrade": False
    }
    async with aiohttp.ClientSession() as session:
        try:
            async with session.post(url, json=payload) as resp:
                data = await resp.json()
                items = data.get("result", {}).get("items", [])
                if items:
                    print(f"Found {len(items)} items. First item keys:", list(items[0].keys()))
                    for idx, item in enumerate(items[:3]):
                        print(f"Item {idx}:")
                        print(f"  userId: {item.get('userId')}")
                        print(f"  userMaskId: {item.get('userMaskId')}")
                        print(f"  nickName: {item.get('nickName')}")
                else:
                    print("No items found")
        except Exception as e:
            print("Error:", e)

async def check_okx():
    print("\n=== OKX ===")
    url = "https://www.okx.com/v3/c2c/tradingOrders/getMarketplaceAdsPrelogin"
    params = {
        "fiatCurrency": "UAH",
        "cryptoCurrency": "USDT",
        "paymentMethod": "all",
        "side": "buy",
        "userType": "all",
        "sortType": "price_asc",
        "numberPerPage": "1",
        "t": str(int(time.time() * 1000)),
    }
    async with aiohttp.ClientSession() as session:
        try:
            async with session.get(url, params=params) as resp:
                data = await resp.json()
                buy_ads = data.get("data", {}).get("buy", [])
                sell_ads = data.get("data", {}).get("sell", [])
                ads = buy_ads or sell_ads
                if ads:
                    print("Keys:", list(ads[0].keys()))
                    # Look for online-related fields
                    online_fields = {k: v for k, v in ads[0].items() if "online" in k.lower() or "active" in k.lower() or "last" in k.lower()}
                    print("Online-related fields:", online_fields)
                    # Let's also print merchant or creator info
                    creator = ads[0].get("creator", {})
                    print("creator/merchant info keys:", list(creator.keys()) if isinstance(creator, dict) else type(creator))
                    if isinstance(creator, dict):
                        print("creator online/active info:", {k: v for k, v in creator.items() if "online" in k.lower() or "active" in k.lower() or "last" in k.lower()})
                    # Let's inspect the first ad fully to be sure
                    print("Full item excerpt (first 15 keys/values):")
                    for k in list(ads[0].keys())[:25]:
                        print(f"  {k}: {ads[0][k]}")
                else:
                    print("No ads found")
        except Exception as e:
            print("Error:", e)

async def check_binance():
    print("\n=== Binance ===")
    url = "https://p2p.binance.com/bapi/c2c/v2/friendly/c2c/adv/search"
    payload = {
        "asset": "USDT",
        "fiat": "UAH",
        "merchantCheck": False,
        "page": 1,
        "payTypes": [],
        "publisherType": None,
        "rows": 1,
        "side": "BUY",
        "tradeType": "BUY",
    }
    async with aiohttp.ClientSession() as session:
        try:
            async with session.post(url, json=payload) as resp:
                data = await resp.json()
                items = data.get("data", [])
                if items:
                    adv = items[0].get("adv", {})
                    advertiser = items[0].get("advertiser", {})
                    print("adv keys:", list(adv.keys()))
                    print("advertiser keys:", list(advertiser.keys()))
                    online_fields = {k: v for k, v in advertiser.items() if "online" in k.lower() or "active" in k.lower() or "last" in k.lower()}
                    print("Advertiser online-related fields:", online_fields)
                    adv_online = {k: v for k, v in adv.items() if "online" in k.lower() or "active" in k.lower() or "last" in k.lower()}
                    print("Adv online-related fields:", adv_online)
                else:
                    print("No items found")
        except Exception as e:
            print("Error:", e)

async def check_mexc():
    print("\n=== MEXC ===")
    url = "https://www.mexc.com/api/c2c/market/list" # wait, mexc_client URL might be different
    # Let's inspect MEXC client file first to get the URL
    print("Mexc URL info check...")

async def main():
    sys.stdout.reconfigure(encoding='utf-8')
    await check_bybit()
    await check_okx()
    await check_binance()

if __name__ == "__main__":
    asyncio.run(main())
