"""Імпорт сесії біржі з cURL, збереженого у ФАЙЛ.

Навіщо окремо від import_curl.py:
  * той бере cURL аргументом командного рядка — рядок з живими куками осідає
    в історії shell і в логах; тут його читаємо з файлу;
  * той жорстко прошитий на "Binance" — тут біржа задається явно;
  * тут ще й перевірка, чи в заголовках є те, без чого приватні ендпоінти
    все одно віддадуть 401/403.

Чому взагалі потрібен cURL, а не логін у боті. Логін зберігає куки, але не
заголовки приватних запитів. Для Binance критичний `csrftoken` — це окремий
заголовок, а НЕ кука `cr00` (підстановка cr00 дає 401, перевірено). Для OKX
потрібні `devid`, `x-cdn`, `x-locale` тощо. Взяти їх можна лише з реального
приватного запиту у вкладці Network.

Як зняти:
  1. У ЗВИЧАЙНОМУ браузері, де ти залогінений, відкрий сторінку мерчанта:
       Binance — p2p.binance.com/uk-UA/advertiserDetail?advertiserNo=...
       OKX     — www.okx.com/ru/p2p/ads-merchant?publicUserId=...
  2. DevTools -> Network. Фільтр:
       Binance — bapi/c2c
       OKX     — /v3/c2c/
  3. Клікни вкладку з відгуками, щоб полетів приватний запит.
  4. На запиті: правою -> Copy -> Copy as cURL (bash).
  5. Встав у файл, збережи.

Запуск:
    python -m tools.deeplink.import_curl_file --exchange Binance --file curl_binance.txt --wipe

`--wipe` затирає файл після успішного імпорту — у ньому лежить жива сесія.
"""

from __future__ import annotations

import argparse
import json
import os
import sqlite3
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from tools.deeplink.import_curl import parse_curl  # noqa: E402

DB_PATH = Path(r"C:\p2p_scanner\data\merchants.db")
DEFAULT_USER_ID = 1115620363

# Без цього приватні ендпоінти share не працюють. Значень не друкуємо — лише
# наявність, щоб нічого чутливого не потрапило у вивід.
REQUIRED_HEADERS = {
    "Binance": ["csrftoken", "clienttype", "c2ctype"],
    "OKX": ["devid", "x-cdn", "x-locale"],
}
REQUIRED_COOKIES = {
    "Binance": ["p20t"],
    "OKX": [],
}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--exchange", required=True, choices=["Binance", "OKX", "Bybit"])
    ap.add_argument("--file", required=True, help="файл з cURL")
    ap.add_argument("--user-id", type=int, default=DEFAULT_USER_ID)
    ap.add_argument("--wipe", action="store_true",
                    help="затерти файл після успішного імпорту")
    args = ap.parse_args()

    sys.stdout.reconfigure(encoding="utf-8")

    path = Path(args.file)
    if not path.is_absolute():
        path = Path(r"C:\p2p_scanner") / path
    if not path.exists():
        print(f"✗ Файлу немає: {path}")
        return 1

    headers, cookies = parse_curl(path.read_text(encoding="utf-8", errors="replace"))
    print(f"Розібрано: {len(headers)} заголовків, {len(cookies)} кук")

    if not cookies:
        print("✗ Кук не знайдено. Схоже, скопійовано не як 'Copy as cURL (bash)'.")
        return 1

    low = {k.lower() for k in headers}
    missing = [h for h in REQUIRED_HEADERS.get(args.exchange, []) if h not in low]
    missing_ck = [c for c in REQUIRED_COOKIES.get(args.exchange, []) if c not in cookies]

    for h in REQUIRED_HEADERS.get(args.exchange, []):
        print(f"   заголовок {h:12} {'Є' if h in low else 'НЕМАЄ'}")
    for c in REQUIRED_COOKIES.get(args.exchange, []):
        print(f"   кука      {c:12} {'Є' if c in cookies else 'НЕМАЄ'}")

    if missing or missing_ck:
        print(f"\n✗ Бракує: {', '.join(missing + missing_ck)}")
        print("  Це запит не з того ендпоінта. Потрібен ПРИВАТНИЙ запит "
              "(Binance: bapi/c2c/..., OKX: /v3/c2c/...), а не картинка чи статика.")
        return 1

    with sqlite3.connect(DB_PATH) as conn:
        conn.execute(
            "INSERT OR REPLACE INTO auth_sessions "
            "(user_id, exchange, headers_json, cookies_json, updated_at, is_active) "
            "VALUES (?, ?, ?, ?, ?, 1)",
            (args.user_id, args.exchange, json.dumps(headers), json.dumps(cookies),
             time.time()),
        )
    print(f"\n✅ Сесію {args.exchange} записано для user {args.user_id}")

    if args.wipe:
        try:
            path.write_text("", encoding="utf-8")
            os.remove(path)
            print(f"🧹 {path.name} затерто й видалено")
        except OSError as e:
            print(f"⚠️ Не вдалось видалити {path.name}: {e} — прибери вручну, "
                  f"там жива сесія")
    else:
        print(f"⚠️ У {path.name} лежить жива сесія — видали його після перевірки")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
