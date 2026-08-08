import json
import re
import sqlite3
import sys
import time

def parse_curl(curl_str: str):
    headers = {}
    cookies = {}

    # Extract headers
    header_matches = re.findall(r"-H\s+['\"]([^'\"]+)['\"]", curl_str)
    for h in header_matches:
        if ":" in h:
            k, v = h.split(":", 1)
            k_strip = k.strip()
            v_strip = v.strip()
            if k_strip.lower() == "cookie":
                # Parse cookie string
                cookie_pairs = v_strip.split(";")
                for cp in cookie_pairs:
                    if "=" in cp:
                        ck, cv = cp.split("=", 1)
                        cookies[ck.strip()] = cv.strip()
            else:
                headers[k_strip] = v_strip

    # Extract cookie header if passed with -b or --cookie
    cookie_arg = re.search(r"(?:-b|--cookie)\s+['\"]([^'\"]+)['\"]", curl_str)
    if cookie_arg:
        cookie_pairs = cookie_arg.group(1).split(";")
        for cp in cookie_pairs:
            if "=" in cp:
                ck, cv = cp.split("=", 1)
                cookies[ck.strip()] = cv.strip()

    return headers, cookies

def main():
    if len(sys.argv) < 2:
        print("Usage: python import_curl.py \"<COPIED_CURL_STRING>\" [user_id]")
        sys.exit(1)

    curl_str = sys.argv[1]
    user_id = int(sys.argv[2]) if len(sys.argv) > 2 else 1115620363

    headers, cookies = parse_curl(curl_str)
    print(f"Parsed {len(headers)} headers and {len(cookies)} cookies.")
    print("Critical cookies present:", [k for k in ["p20t", "cr00", "d1og", "bnc-uuid"] if k in cookies])

    if not cookies:
        print("Error: No cookies found in cURL string!")
        sys.exit(1)

    conn = sqlite3.connect("data/merchants.db")
    c = conn.cursor()
    c.execute("""
        INSERT OR REPLACE INTO auth_sessions (user_id, exchange, headers_json, cookies_json, updated_at, is_active)
        VALUES (?, ?, ?, ?, ?, 1)
    """, (user_id, "Binance", json.dumps(headers), json.dumps(cookies), time.time()))
    conn.commit()
    conn.close()

    print(f"✅ Successfully updated Binance session for user {user_id} in data/merchants.db!")

if __name__ == "__main__":
    main()
