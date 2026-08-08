import base64
import os
import re
import subprocess
import sys
import time

sys.stdout.reconfigure(encoding="utf-8")

pkg = "com.binance.dev"
order_no = "22222222222222222222"

def b64(s: str) -> str:
    return base64.b64encode(s.encode("utf-8")).decode("utf-8")

probes = [
    f"https://app.binance.com/fiat/orderDetails?id={order_no}",
    "https://app.binance.com/mp/web?appId=Bzp9defeaRgNqhgV4wEG5C&startPagePath=" + b64("pages/merchant-detail/index"),
    "https://app.binance.com/webview/webview?type=default&url=" + b64("https://www.binance.com/fixedLoan"),
    "https://app.binance.com/definitely/not/a/route",
    "https://app.binance.com/main/main?at=index",
    "https://app.binance.com/fiat/hold",
    "https://app.binance.com/p2p/chatList?source=homepage",
]

report_file = r"c:\p2p_scanner\tools\deeplink\binance_allowlist.txt"

with open(report_file, "w", encoding="utf-8") as f:
    f.write(f"# binance deeplink allowlist :: {time.strftime('%Y-%m-%dT%H:%M:%S')}\n")

print(f"Probing {len(probes)} URLs and inspecting logcat for nezha / allowlist logs...\n")

for i, url in enumerate(probes, 1):
    print(f"[{i}/{len(probes)}] {url}")
    with open(report_file, "a", encoding="utf-8") as f:
        f.write(f"\n===== [{i}] {url}\n")

    subprocess.run(["adb", "shell", "am", "force-stop", pkg], capture_output=True)
    subprocess.run(["adb", "logcat", "-c"], capture_output=True)
    time.sleep(0.5)

    cmd = ["adb", "shell", "am", "start", "-W", "-a", "android.intent.action.VIEW", "-c", "android.intent.category.BROWSABLE", "-d", f'"{url}"']
    res = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="ignore")
    with open(report_file, "a", encoding="utf-8") as f:
        f.write(res.stdout + "\n")

    time.sleep(3.0)

    top_res = subprocess.run(["adb", "shell", "dumpsys activity activities"], capture_output=True, text=True, encoding="utf-8", errors="ignore")
    top = ""
    for line in top_res.stdout.splitlines():
        if "topResumedActivity" in line or "ResumedActivity" in line:
            top = line.strip()
            break
    with open(report_file, "a", encoding="utf-8") as f:
        f.write(f"TOP: {top}\n")

    log_res = subprocess.run(["adb", "logcat", "-d"], capture_output=True, text=True, encoding="utf-8", errors="ignore")
    relevant_logs = []
    for line in log_res.stdout.splitlines():
        if any(kw in line.lower() for kw in ["deeplink", "deep_link", "externaldeeplink", "routerpath", "allow", "denied", "nezha"]):
            relevant_logs.append(line.strip())

    with open(report_file, "a", encoding="utf-8") as f:
        f.write("LOG:\n")
        for l in relevant_logs[-40:]:
            f.write(f"  {l}\n")

    verdict = "REJECTED (NoSupportRouterPath)" if "NoSupportRouterPath" in top else ("ACCEPTED" if pkg in top else "UNKNOWN")
    print(f"    VERDICT: {verdict}")
    print(f"    TOP: {top}")
    for hit in relevant_logs:
        if any(kw in hit for kw in ["allowed", "denied", "allow_index", "block_index", "externalDeeplink"]):
            print(f"    LOG MATCH: {hit}")
    print()

print(f"Done. Report saved to: {report_file}")
