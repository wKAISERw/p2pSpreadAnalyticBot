import subprocess
import time

merchant_id = "100002345" # Example Bybit merchant ID

candidates = [
    f"bybitapp://open?page=p2pMerchantProfile&merchantId={merchant_id}",
    f"bybitapp://open?page=p2pMerchant&merchantId={merchant_id}",
    f"bybitapp://open?page=/p2p/merchant/{merchant_id}",
    f"bybitapp://open?page=merchantDetail&merchantId={merchant_id}",
    f"bybitapp://open?page=p2p&merchantId={merchant_id}",
    f"https://app.bybit.com/inapp/p2p/merchant/{merchant_id}",
    f"https://app.bybit.com/inapp/p2p/merchant?merchantId={merchant_id}",
    f"https://app.bybit.com/inapp/fiat/trade/otc/profile/{merchant_id}",
    f"https://app.bybit.com/inapp?page=p2pMerchant&merchantId={merchant_id}",
]

print(f"Testing {len(candidates)} Bybit merchant profile candidates via adb...\n")

for url in candidates:
    subprocess.run(["adb", "shell", "am", "force-stop", "com.bybit.app"], capture_output=True)
    cmd = ["adb", "shell", "am", "start", "-W", "-a", "android.intent.action.VIEW", "-c", "android.intent.category.BROWSABLE", "-d", url]
    res = subprocess.run(cmd, capture_output=True, text=True)
    
    is_app = "com.bybit.app" in res.stdout
    comp = ""
    for line in res.stdout.splitlines():
        if "cmp=" in line or "ComponentInfo" in line:
            comp = line.strip()
            break
            
    verdict = "APP" if is_app else "BROWSER/CHOOSER"
    print(f">>> {url}")
    print(f"    VERDICT: {verdict} ({comp})")
    time.sleep(1)
