import sys

with open("logs/debug.log", "r", encoding="utf-8", errors="ignore") as f:
    for line in f:
        if "2026-05-28" in line and ("Перемальовка" in line or "redraw" in line or "Error" in line or "exception" in line.lower()):
            safe_line = line.strip().encode(sys.stdout.encoding or 'utf-8', errors='replace').decode(sys.stdout.encoding or 'utf-8')
            print(safe_line)
