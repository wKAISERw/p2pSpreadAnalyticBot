import requests
import json
import base64
import subprocess
import time

# 1. Fetch live Binance P2P buy ad
binance_url = "https://p2p.binance.com/bapi/c2c/v2/friendly/c2c/adv/search"
payload = {
    "asset": "USDT",
    "fiat": "UAH",
    "merchantCheck": False,
    "page": 1,
    "rows": 5,
    "tradeType": "BUY"
}
headers = {"User-Agent": "Mozilla/5.0"}

b_res = requests.post(binance_url, json=payload, headers=headers).json()
live_ad_id = ""
live_merchant_id = ""

if b_res.get("data"):
    first = b_res["data"][0]
    live_ad_id = first["adv"]["advNo"]
    live_merchant_id = first["advertiser"]["userNo"]

print(f"Live Binance Ad No: {live_ad_id}")
print(f"Live Binance Merchant No: {live_merchant_id}")

# Construct Binance webview gateway with exact web URL
# Note: Binance web route for profile is /en/advertiserDetail?advertiserNo=... or /en/advertiserProfile?userNo=...
binance_profile_web = f"https://p2p.binance.com/en/advertiserDetail?advertiserNo={live_merchant_id}"
binance_profile_b64 = base64.b64encode(binance_profile_web.encode()).decode()
binance_gw = f"https://app.binance.com/webview/webview?type=default&url={binance_profile_b64}"

print("\n--- Launching Live Binance Merchant Profile via Webview Gateway ---")
print("Target URL:", binance_gw)
subprocess.run(["adb", "shell", "am", "force-stop", "com.binance.dev"])
time.sleep(1)
subprocess.run(["adb", "shell", "am", "start", "-W", "-a", "android.intent.action.VIEW", "-c", "android.intent.category.BROWSABLE", "-d", binance_gw])
