import base64
import os
import re
import subprocess
import time

merchant_no = "s40c6bb83ac363d02a2f4cf9bd2f23645"
pkg = "com.binance.dev"

def b64(s: str) -> str:
    return base64.b64encode(s.encode("utf-8")).decode("utf-8")

def gate(u: str) -> str:
    return "https://app.binance.com/webview/webview?type=default&url=" + b64(u)

cases = [
    {"label": "1_control-www-fixedLoan", "url": gate("https://www.binance.com/fixedLoan")},
    {"label": "2_current-c2c-advertiserDetail", "url": gate(f"https://c2c.binance.com/uk-UA/advertiserDetail?advertiserNo={merchant_no}")},
    {"label": "3_www-advertiserDetail", "url": gate(f"https://www.binance.com/uk-UA/advertiserDetail?advertiserNo={merchant_no}")},
    {"label": "4_p2p-advertiserDetail", "url": gate(f"https://p2p.binance.com/uk-UA/advertiserDetail?advertiserNo={merchant_no}")},
    {"label": "5_www-advertiserDetail-dynamic", "url": "https://app.binance.com/webview/webview?type=default&needDynamic=true&url=" + b64(f"https://www.binance.com/uk-UA/advertiserDetail?advertiserNo={merchant_no}")},
]

shots_dir = r"c:\p2p_scanner\tools\deeplink\shots_webview"
os.makedirs(shots_dir, exist_ok=True)
report_file = r"c:\p2p_scanner\tools\deeplink\webview_report.txt"

with open(report_file, "w", encoding="utf-8") as f:
    f.write(f"# webview whitelist test :: {time.strftime('%Y-%m-%dT%H:%M:%S')}\n")

print(f"Testing {len(cases)} webview whitelist candidates via adb...\n")

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

    time.sleep(3.5)

    top_res = subprocess.run(["adb", "shell", "dumpsys activity activities"], capture_output=True, text=True, encoding="utf-8", errors="ignore")
    top = ""
    for line in top_res.stdout.splitlines():
        if "topResumedActivity" in line or "ResumedActivity" in line:
            top = line.strip()
            break
    with open(report_file, "a", encoding="utf-8") as f:
        f.write(f"TOP: {top}\n")

    # uiautomator dump to extract screen text
    subprocess.run(["adb", "shell", "uiautomator", "dump", "/sdcard/__ui.xml"], capture_output=True)
    ui_res = subprocess.run(["adb", "shell", "cat", "/sdcard/__ui.xml"], capture_output=True, text=True, encoding="utf-8", errors="ignore")
    subprocess.run(["adb", "shell", "rm", "-f", "/sdcard/__ui.xml"], capture_output=True)

    matches = re.findall(r'text="([^"]{2,80})"', ui_res.stdout)
    texts = list(set([m for m in matches if m.strip()]))[:20]
    screen_str = " | ".join(texts)
    with open(report_file, "a", encoding="utf-8") as f:
        f.write(f"SCREEN: {screen_str}\n")

    bad = any(kw in screen_str.lower() for kw in ["не працює", "doesn't work", "застаріла", "outdated"]) or "NoSupportRouterPath" in top
    verdict = "REJECTED" if bad else "ACCEPTED"
    print(f"    VERDICT: {verdict}")
    print(f"    SCREEN: {screen_str[:120]}\n")

    shot_path_local = os.path.join(shots_dir, f"{i:02d}_{label}.png")
    subprocess.run(["adb", "shell", "screencap", "-p", "/sdcard/__s.png"], capture_output=True)
    subprocess.run(["adb", "pull", "/sdcard/__s.png", shot_path_local], capture_output=True)
    subprocess.run(["adb", "shell", "rm", "-f", "/sdcard/__s.png"], capture_output=True)

print(f"Done. Report saved to: {report_file}")
