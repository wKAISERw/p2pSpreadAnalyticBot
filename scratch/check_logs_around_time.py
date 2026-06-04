import os

log_files = [
    "c:/p2p_scanner/logs/debug.log",
    "c:/p2p_scanner/logs/debug.log.1",
]

target_time = "23:59:"
output_lines = []

for lf in log_files:
    if not os.path.exists(lf):
        continue
    print(f"Searching in {lf}...")
    with open(lf, "r", encoding="utf-8", errors="ignore") as f:
        for line in f:
            if "2026-05-30 23:59" in line:
                output_lines.append(line.strip())

print(f"Found {len(output_lines)} lines.")
for line in output_lines[-100:]:
    print(line)
