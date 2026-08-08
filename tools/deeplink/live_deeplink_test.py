import asyncio
import os
import subprocess
import time
import sqlite3

# Import our bot deeplink module
from bot.deeplinks import app_https_url, app_scheme_url, tg_button_url

async def run_live_tests():
    conn = sqlite3.connect("data/merchants.db")
    cursor = conn.cursor()
    
    # Get real Binance merchant
    cursor.execute("SELECT merchant_id, merchant_name FROM merchant_snapshots WHERE exchange = 'Binance' LIMIT 1")
    binance_row = cursor.fetchone()
    binance_merchant = binance_row[0] if binance_row else "s40c6bb83ac363d02a2f4cf9bd2f23645"
    
    # Get real OKX merchant
    cursor.execute("SELECT merchant_id, merchant_name FROM merchant_snapshots WHERE exchange = 'OKX' LIMIT 1")
    okx_row = cursor.fetchone()
    okx_merchant = okx_row[0] if okx_row else "005977cb24"
    
    # Get real Bybit merchant
    cursor.execute("SELECT merchant_id, merchant_name FROM merchant_snapshots WHERE exchange = 'Bybit' LIMIT 1")
    bybit_row = cursor.fetchone()
    bybit_merchant = bybit_row[0] if bybit_row else "504906738"
    
    conn.close()

    print("=== LIVE DEEPLINK TEST ON CONNECTED PHONE ===")
    print(f"Binance Merchant ID: {binance_merchant}")
    print(f"OKX Merchant ID:     {okx_merchant}")
    print(f"Bybit Merchant ID:   {bybit_merchant}\n")

    # Construct test cases
    import base64
    binance_web_url = f"https://p2p.binance.com/en/advertiserDetail?advertiserNo={binance_merchant}"
    b64_url = base64.b64encode(binance_web_url.encode()).decode()
    binance_gateway_url = f"https://app.binance.com/webview/webview?type=default&url={b64_url}"
    
    okx_order_url = "okx://exchange/p2p/order?id=20512345678901234567"
    bybit_order_url = "https://app.bybit.com/inapp/p2p/order/20512345678901234567"
    
    tests = [
        ("Binance", "com.binance.dev", binance_gateway_url, "live_binance.png"),
        ("OKX", "com.okinc.okex.gp", okx_order_url, "live_okx.png"),
        ("Bybit", "com.bybit.app", bybit_order_url, "live_bybit.png"),
    ]

    for ex_name, pkg, url, shot_name in tests:
        print(f"\n--- Testing {ex_name} ---")
        print(f"Target URL: {url}")
        
        # 1. Force stop app for clean cold/warm start
        subprocess.run(["adb", "shell", "am", "force-stop", pkg], capture_output=True)
        time.sleep(1)
        
        # 2. Launch intent
        cmd = ["adb", "shell", "am", "start", "-W", "-a", "android.intent.action.VIEW", "-c", "android.intent.category.BROWSABLE", "-d", url]
        res = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="ignore")
        print("  Launch Result:", res.stdout.strip().splitlines()[0] if res.stdout else "OK")
        
        # 3. Wait for app to render
        time.sleep(4)
        
        # 4. Check top activity
        top_res = subprocess.run(["adb", "shell", "dumpsys activity activities"], capture_output=True, text=True, encoding="utf-8", errors="ignore")
        top_activity = "Unknown"
        if top_res and top_res.stdout:
            for line in top_res.stdout.splitlines():
                if "ResumedActivity" in line or "topResumedActivity" in line:
                    top_activity = line.strip()
                    break
        print(f"  Top Activity: {top_activity}")
        
        # 5. Take clean screenshot
        shot_path_sd = f"/sdcard/{shot_name}"
        shot_path_local = os.path.join("c:\\p2p_scanner\\tools\\deeplink\\shots", shot_name)
        subprocess.run(["adb", "shell", "screencap", "-p", shot_path_sd], capture_output=True)
        subprocess.run(["adb", "pull", shot_path_sd, shot_path_local], capture_output=True)
        
        if os.path.exists(shot_path_local):
            size = os.path.getsize(shot_path_local)
            print(f"  Screenshot saved: {shot_path_local} ({size} bytes)")

if __name__ == "__main__":
    asyncio.run(run_live_tests())
