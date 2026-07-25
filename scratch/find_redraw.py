import sys

with open("logs/debug.log", "r", encoding="utf-8", errors="ignore") as f:
    for line in f:
        if "2026-05-28" in line and ("e8c609fc16" in line or "s3venthy" in line or "redraw" in line):
            # Safe print encoding for Windows console
            safe_line = line.strip().encode(sys.stdout.encoding or 'utf-8', errors='replace').decode(sys.stdout.encoding or 'utf-8')
            print(safe_line)
