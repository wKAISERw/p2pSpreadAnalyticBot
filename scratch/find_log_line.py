import os
import sys

if sys.stdout.encoding != 'utf-8':
    sys.stdout.reconfigure(encoding='utf-8')

log_files = [
    "c:/p2p_scanner/logs/debug.log",
    "c:/p2p_scanner/logs/debug.log.1",
    "c:/p2p_scanner/logs/debug.log.2",
    "c:/p2p_scanner/logs/error.log",
    "c:/p2p_scanner/logs/error.log.1",
    "c:/p2p_scanner/logs/error.log.2",
]

targets = ["WillyWonka", "3207"]

for lf in log_files:
    if not os.path.exists(lf):
        continue
    print(f"Searching in {lf}...")
    with open(lf, "r", encoding="utf-8", errors="ignore") as f:
        lines = f.readlines()
        for idx, line in enumerate(lines):
            if any(t in line for t in targets):
                print(f"Match found at line {idx+1}:")
                # Print 5 lines before and after
                start = max(0, idx - 5)
                end = min(len(lines), idx + 6)
                for i in range(start, end):
                    prefix = "--> " if i == idx else "    "
                    print(f"{prefix}{lines[i].strip()}")
                print("="*40)
