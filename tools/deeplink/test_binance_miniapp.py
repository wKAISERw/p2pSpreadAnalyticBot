import base64
import os
import re
import subprocess
import sys
import time
from urllib.parse import quote

sys.stdout.reconfigure(encoding="utf-8")

merchant_no = "s40c6bb83ac363d02a2f4cf9bd2f23645"
ad_no = "11523456789012345678"
order_no = "20512345678901234567"

pkg = "com.binance.dev"
app_id = "Bzp9defeaRgNqhgV4wEG5C"

def b64(s: str) -> str:
    return base64.b64encode(s.encode("utf-8")).decode("utf-8")

def mp_url(page: str, query: str = "") -> str:
    u = f"https://app.binance.com/mp/web?appId={app_id}&startPagePath=" + quote(b64(page), safe="")
    if query:
        u += "&startPageQuery=" + quote(b64(query), safe="")
    return u

cases = [
    {"label": "1_control-index", "url": mp_url("pages/index", "fromNative=true")},
]

for p in ["advertiserNo", "merchantNo", "userNo", "advertiserId", "id"]:
    cases.append({"label": f"merchant-{p}", "url": mp_url("pages/merchant-detail/index", f"{p}={merchant_no}&fromNative=true")})

for p in ["advNo", "adNo", "advertiserNo", "id"]:
    cases.append({"label": f"ads-{p}", "url": mp_url("pages/ads/index", f"{p}={ad_no}&fromNative=true")})

for p in ["orderNo", "orderId", "id"]:
    cases.append({"label": f"order-{p}", "url": mp_url("pages/order-detail/index", f"{p}={order_no}&fromNative=true")})

shots_dir = r"c:\p2p_scanner\tools\deeplink\shots_miniapp"
os.makedirs(shots_dir, exist_ok=True)
report_file = r"c:\p2p_scanner\tools\deeplink\miniapp_report.txt"

with open(report_file, "w", encoding="utf-8") as f:
    f.write(f"# binance miniapp test :: {time.strftime('%Y-%m-%dT%H:%M:%S')}\n")

print(f"Testing {len(cases)} Binance MiniApp candidates via adb...\n")

for i, c in enumerate(cases, 1):
    label = c["label"]
    url = c["url"]
    print(f"[{i}/{len(cases)}] {label}")
    print(f"    URL: {url}")

    with open(report_file, "a", encoding="utf-8") as f:
        f.write(f"\n===== [{i}] {label}\nURL: {url}\n")

    subprocess.run(["adb", "shell", "am", "force-stop", pkg], capture_output=True)
    time.sleep(0.7)

    cmd = ["adb", "shell", "am", "start", "-W", "-a", "android.intent.action.VIEW", "-c", "android.intent.category.BROWSABLE", "-d", f'"{url}"']
    res = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="ignore")
    with open(report_file, "a", encoding="utf-8") as f:
        f.write(res.stdout + "\n")

    time.sleep(4.0)

    top_res = subprocess.run(["adb", "shell", "dumpsys activity activities"], capture_output=True, text=True, encoding="utf-8", errors="ignore")
    top = ""
    for line in top_res.stdout.splitlines():
        if "topResumedActivity" in line or "ResumedActivity" in line:
            top = line.strip()
            break
    with open(report_file, "a", encoding="utf-8") as f:
        f.write(f"TOP: {top}\n")

    subprocess.run(["adb", "shell", "uiautomator", "dump", "/sdcard/__ui.xml"], capture_output=True)
    ui_res = subprocess.run(["adb", "shell", "cat", "/sdcard/__ui.xml"], capture_output=True, text=True, encoding="utf-8", errors="ignore")
    subprocess.run(["adb", "shell", "rm", "-f", "/sdcard/__ui.xml"], capture_output=True)

    matches = re.findall(r'text="([^"]{2,80})"', ui_res.stdout)
    texts = list(set([m for m in matches if m.strip()]))[:20]
    screen_str = " | ".join(texts)
    with open(report_file, "a", encoding="utf-8") as f:
        f.write(f"SCREEN: {screen_str}\n")

    if "NoSupportRouterPath" in top or any(kw in screen_str.lower() for kw in ["не працює", "doesn't work", "застаріла"]):
        verdict = "REJECTED"
    elif "com.binance.dev" in top:
        verdict = "LAUNCHED IN APP"
    else:
        verdict = "UNKNOWN"

    print(f"    VERDICT: {verdict}")
    print(f"    SCREEN: {screen_str[:120]}\n")

    shot_path_local = os.path.join(shots_dir, f"{i:02d}_{label}.png")
    subprocess.run(["adb", "shell", "screencap", "-p", "/sdcard/__s.png"], capture_output=True)
    subprocess.run(["adb", "pull", "/sdcard/__s.png", shot_path_local], capture_output=True)
    subprocess.run(["adb", "shell", "rm", "-f", "/sdcard/__s.png"], capture_output=True)

print(f"Done. Report saved to: {report_file}")
