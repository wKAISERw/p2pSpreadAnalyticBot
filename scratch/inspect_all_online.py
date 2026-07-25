import asyncio
import aiohttp
import time
import json
import sys

async def check_binance():
    print("=== Binance Ads ===")
    url = "https://p2p.binance.com/bapi/c2c/v2/friendly/c2c/adv/search"
    payload = {
        "asset": "USDT",
        "fiat": "UAH",
        "merchantCheck": False,
        "page": 1,
        "payTypes": [],
        "publisherType": None,
        "rows": 20,
        "side": "BUY",
        "tradeType": "BUY",
    }
    async with aiohttp.ClientSession() as session:
        try:
            async with session.post(url, json=payload) as resp:
                data = await resp.json()
                items = data.get("data", [])
                print(f"Fetched {len(items)} ads from Binance")
                for item in items[:10]:
                    adv = item.get("adv", {})
                    advertiser = item.get("advertiser", {})
                    name = advertiser.get("nickName")
                    active_sec = advertiser.get("activeTimeInSecond")
                    print(f"Merchant: {name} | activeTimeInSecond: {active_sec} ({active_sec/60:.2f} mins ago if it's seconds ago)")
        except Exception as e:
            print("Binance Error:", e)

async def check_okx():
    print("\n=== OKX Ads ===")
    url = "https://www.okx.com/v3/c2c/tradingOrders/getMarketplaceAdsPrelogin"
    params = {
        "fiatCurrency": "UAH",
        "cryptoCurrency": "USDT",
        "paymentMethod": "all",
        "side": "buy",
        "userType": "all",
        "sortType": "price_asc",
        "numberPerPage": "50",
        "t": str(int(time.time() * 1000)),
    }
    async with aiohttp.ClientSession() as session:
        try:
            async with session.get(url, params=params) as resp:
                data = await resp.json()
                buy_ads = data.get("data", {}).get("buy", [])
                sell_ads = data.get("data", {}).get("sell", [])
                ads = buy_ads or sell_ads
                print(f"Fetched {len(ads)} ads from OKX")
                for ad in ads[:15]:
                    name = ad.get("nickName")
                    status_vo = ad.get("userActiveStatusVo")
                    print(f"Merchant: {name} | userActiveStatusVo: {status_vo}")
        except Exception as e:
            print("OKX Error:", e)

async def check_bybit():
    print("\n=== Bybit Ads ===")
    url = "https://api2.bybit.com/fiat/otc/item/online"
    payload = {
        "userId": "",
        "tokenId": "USDT",
        "currencyId": "UAH",
        "payment": [],
        "side": "1",
        "size": "20",
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
                print(f"Fetched {len(items)} ads from Bybit")
                for item in items[:10]:
                    name = item.get("nickName")
                    is_online = item.get("isOnline")
                    last_logout = item.get("lastLogoutTime")
                    print(f"Merchant: {name} | isOnline: {is_online} | lastLogoutTime: {last_logout}")
        except Exception as e:
            print("Bybit Error:", e)

async def main():
    sys.stdout.reconfigure(encoding='utf-8')
    await check_binance()
    await check_okx()
    await check_bybit()

if __name__ == "__main__":
    asyncio.run(main())
