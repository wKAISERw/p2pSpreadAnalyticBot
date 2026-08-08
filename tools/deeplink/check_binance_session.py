#!/usr/bin/env python3
"""
Діагностика збереженої сесії Binance.

Навіщо. Сесія знята з залогіненого браузера, а приватний ендпоінт усе одно
віддає 401. Отже питання не «чи залогінений акаунт», а «що саме потрапило
в auth_sessions». Найчастіші причини:

  1. немає `p20t` — головного авторизаційного токена P2P. Він прив'язаний до
     домену, і якщо кукі знімались на www.binance.com, а не на p2p/c2c, у базу
     потрапляють лише візитерські;
  2. немає заголовка `csrftoken` — приватні ендпоінти Binance звіряють його
     з однойменною кукою;
  3. немає `bnc-uuid` / `device-info` — антифрод відсікає запит як чужий.

Скрипт нічого не змінює і не друкує значень кукі — лише імена і довжини,
плюс перевіряє три ендпоінти від найпростішого до цільового, щоб побачити,
на якому саме рівні ламається авторизація.

Запуск з кореня проєкту:
    python -m tools.deeplink.check_binance_session
"""

from __future__ import annotations

import argparse
import asyncio
import sys

# Кукі, без яких приватний C2C-ендпоінт не працюватиме.
CRITICAL_COOKIES = ["p20t", "cr00", "d1og", "logined", "bnc-uuid"]
# Заголовки, які браузер шле разом із приватним запитом.
CRITICAL_HEADERS = ["csrftoken", "bnc-uuid", "clienttype", "device-info"]

# Від найпростішого до цільового — щоб локалізувати рівень поломки.
#
# Перший рядок — єдиний ендпоінт, про який ми ТОЧНО знаємо, що він відповідає
# code=000000 на цій сесії: саме його пінгує SessionManager._try_binance_refresh,
# і саме тому статус показує зелене. Він і є опорною точкою.
#   200 + 000000 на першому, 401 на другому -> кукі валідні, але приватному
#       рівню бракує чогось ще (fingerprint, підпис, окремий токен);
#   401 уже на першому -> сесія не та, що показує SessionManager.
LADDER = [
    ("опорний: friendly/get-profile (SessionManager каже, що він живий)",
     "https://c2c.binance.com/bapi/c2c/v1/friendly/c2c/user/get-profile", "GET"),
    ("приватний: базові дані користувача",
     "https://c2c.binance.com/bapi/c2c/v1/private/c2c/user/base-detail", "POST"),
    ("приватний: цільовий share-ендпоінт",
     "https://c2c.binance.com/bapi/c2c/v1/private/c2c/share/advertiser-share", "POST"),
]


async def main_async(user_id: int) -> None:
    sys.stdout.reconfigure(encoding="utf-8")
    from curl_cffi.requests import AsyncSession as CurlSession

    from core.storage.merchant_db import MerchantDB
    db = MerchantDB()
    await db.start()

    headers_dict, cookies_dict, updated = await db.get_auth_session("Binance", user_id)
    if not headers_dict and not cookies_dict:
        sys.exit("У auth_sessions немає сесії Binance взагалі.")

    headers_dict = {k.lower(): v for k, v in (headers_dict or {}).items()}
    cookies_dict = cookies_dict or {}

    print(f"\n{'=' * 70}\nЩО ЗБЕРЕЖЕНО\n{'=' * 70}")
    print(f"оновлено: {updated}")
    print(f"кукі: {len(cookies_dict)}, заголовків: {len(headers_dict)}\n")

    print("Критичні кукі:")
    missing_c = []
    for name in CRITICAL_COOKIES:
        val = cookies_dict.get(name)
        if val:
            print(f"  [є]    {name}  (довжина {len(str(val))})")
        else:
            print(f"  [НЕМА] {name}")
            missing_c.append(name)

    print("\nКритичні заголовки:")
    missing_h = []
    for name in CRITICAL_HEADERS:
        val = headers_dict.get(name)
        if val:
            print(f"  [є]    {name}  (довжина {len(str(val))})")
        else:
            print(f"  [НЕМА] {name}")
            missing_h.append(name)

    others = sorted(set(cookies_dict) - set(CRITICAL_COOKIES))
    if others:
        print(f"\nІнші кукі ({len(others)}): {', '.join(others[:25])}"
              + (" …" if len(others) > 25 else ""))

    # csrftoken інколи лежить у куках, а не в заголовках — це поширена причина 401.
    if "csrftoken" in missing_h and cookies_dict.get("csrftoken"):
        print("\n  ! csrftoken є в куках, але НЕ в заголовках. Binance звіряє саме "
              "заголовок — його треба додавати в запит вручну.")

    # Форма з core/engine/route_executor.py: усі збережені заголовки плюс
    # X-CSRF-TOKEN з кукі. Той код на живому акаунті не перевірявся, тому саме
    # драбинка нижче і вирішує — вона нічого не припускає.
    req_headers = dict(headers_dict)
    for k in ("content-length", "Content-Length", "accept-encoding", "Accept-Encoding"):
        req_headers.pop(k, None)
    req_headers["Accept"] = "application/json"
    req_headers["Content-Type"] = "application/json"
    req_headers["Referer"] = "https://p2p.binance.com/"
    csrf = cookies_dict.get("csrftoken", "")
    if csrf:
        req_headers["X-CSRF-TOKEN"] = csrf

    try:
        from config import settings
        proxy = getattr(settings, "proxy_url", None)
    except Exception:
        proxy = None
    proxies = {"http": proxy, "https": proxy} if proxy else None

    print(f"\n{'=' * 70}\nДРАБИНКА ЗАПИТІВ\n{'=' * 70}")
    async with CurlSession(impersonate="chrome124", proxies=proxies) as session:
        for label, url, method in LADDER:
            try:
                if method == "GET":
                    resp = await session.get(url, headers=req_headers,
                                             cookies=cookies_dict, timeout=12)
                else:
                    resp = await session.post(url, headers=req_headers,
                                              cookies=cookies_dict, json={}, timeout=12)
                body = resp.text[:160].replace("\n", " ")
                print(f"\n{label}")
                print(f"  HTTP {resp.status_code}  {body}")
            except Exception as e:
                print(f"\n{label}\n  {type(e).__name__}: {e}")

    print(f"\n{'=' * 70}\nЯК ЧИТАТИ\n{'=' * 70}")
    if missing_c:
        print(f"Бракує кукі: {', '.join(missing_c)}")
        if "p20t" in missing_c:
            print("  p20t — головний токен P2P. Без нього приватні C2C-ендпоінти")
            print("  завжди дадуть 401, скільки б браузер не був залогінений.")
            print("  Знімати кукі треба саме на p2p.binance.com або c2c.binance.com,")
            print("  а не на www.binance.com — токен прив'язаний до домену.")
    if missing_h:
        print(f"Бракує заголовків: {', '.join(missing_h)}")
    if not missing_c and not missing_h:
        print("Усе критичне на місці. Якщо перший рядок драбинки дав 200,")
        print("а другий 401 — річ не в куках, а в додатковій перевірці")
        print("(device fingerprint або підпис запиту).")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--user-id", type=int, default=0)
    args = ap.parse_args()
    asyncio.run(main_async(args.user_id))


if __name__ == "__main__":
    main()
