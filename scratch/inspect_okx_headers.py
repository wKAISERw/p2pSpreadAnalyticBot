import sqlite3
import json
import sys

def main():
    try:
        sys.stdout.reconfigure(encoding='utf-8')
    except Exception:
        pass

    conn = sqlite3.connect('data/merchants.db')
    conn.row_factory = sqlite3.Row
    cur = conn.execute("SELECT headers_json FROM auth_sessions WHERE exchange='OKX' AND is_active=1")
    row = cur.fetchone()
    if not row:
        print("No active OKX session found!")
        return

    headers = json.loads(row['headers_json'] or "{}")
    for k, v in headers.items():
        # Mask authorization header for safety
        if k.lower() == 'authorization':
            print(f"{k}: Bearer ... [MASKED, len={len(v)}]")
        else:
            print(f"{k}: {v}")

if __name__ == '__main__':
    main()
