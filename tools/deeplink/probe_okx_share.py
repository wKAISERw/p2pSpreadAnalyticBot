#!/usr/bin/env python3
"""
Пробник share-ендпоінтів OKX C2C.

Ідея та сама, що спрацювала для Binance, але для OKX вона виглядає навіть
перспективніше. У dex застосунку є готові ендпоінти:

    /v3/c2c/merchant/share          — поділитися мерчантом
    /v3/c2c/merchant/sharedInfo     — дані вже створеного share
    /v3/c2c/tradingOrders/share     — поділитися ордером

Чому це важливо саме для OKX. Прогін на пристрої показав, що
`okx://exchange/p2p/profile?userId=` веде на вітрину P2P
(`com.okinc.p2p.trade.HomePageActivity`), а не на картку продавця. Тобто
нативного маршруту на профіль ми не маємо.

Але в манифесті OKX зареєстровано шлях `/ul/.*` — короткі універсальні лінки
(`https://www.okx.com/ul/m3w2P7`). Якщо `merchant/share` віддає саме такий
`/ul/`-лінк, питання профілю OKX закривається повністю і без проміжної
сторінки: `/ul/` можна класти прямо в кнопку Telegram, бо це https.

Запуск з кореня проєкту:
    python -m tools.deeplink.probe_okx_share --pub-user-id <ID_МЕРЧАНТА> \
                                             [--order-id <ID_ОРДЕРА>]
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
import time

BASE = "https://www.okx.com/v3/c2c"

# Імена полів у OKX різняться між ендпоінтами, тому пробуємо кілька варіантів.
CASES = [
    ("merchant/share", [
        {"pubUserId": "{merchant}"},
        {"publicUserId": "{merchant}"},
        {"merchantId": "{merchant}"},
        {"pubUserId": "{merchant}", "shareType": "MERCHANT"},
    ]),
    ("merchant/sharedInfo", [
        {"pubUserId": "{merchant}"},
        {"publicUserId": "{merchant}"},
    ]),
    ("tradingOrders/share", [
        {"orderId": "{order}"},
        {"id": "{order}"},
        {"orderNo": "{order}"},
    ]),
]


async def probe(merchant_id: str, order_id: str, user_id: int = 0) -> None:
    from curl_cffi.requests import AsyncSession as CurlSession

    db = await _open_db()
    headers_dict, cookies_dict, _ = await db.get_auth_session("OKX", user_id)
    if not headers_dict or not cookies_dict:
        sys.exit("Немає збереженої сесії OKX. Спочатку прогони SessionManager.")

    headers_dict = {k.lower(): v for k, v in headers_dict.items()}
    auth = headers_dict.get("authorization", "")

    req_headers = {
        "accept": "application/json",
        "content-type": "application/json",
        "app-type": "web",
        "x-locale": "ru_RU",
    }
    if auth:
        req_headers["authorization"] = auth
    for k in ("devid", "x-id-group", "x-site-info", "user-agent"):
        v = headers_dict.get(k)
        if v:
            req_headers[k] = v

    try:
        from config import settings
        proxy = getattr(settings, "proxy_url", None)
    except Exception:
        proxy = None
    proxies = {"http": proxy, "https": proxy} if proxy else None

    async with CurlSession(impersonate="chrome124", proxies=proxies) as session:
        for path, payloads in CASES:
            if "tradingOrders" in path and not order_id:
                continue
            print(f"\n{'=' * 70}\n{BASE}/{path}\n{'=' * 70}")
            for tpl in payloads:
                body = {
                    k: v.format(merchant=merchant_id, order=order_id)
                    if isinstance(v, str) else v
                    for k, v in tpl.items()
                }
                url = f"{BASE}/{path}?t={int(time.time() * 1000)}"
                try:
                    resp = await session.post(
                        url, json=body, headers=req_headers,
                        cookies=cookies_dict, timeout=12,
                    )
                    try:
                        data = resp.json()
                    except Exception:
                        print(f"  ?  {body} -> HTTP {resp.status_code}: {resp.text[:250]}")
                        continue

                    code = data.get("code")
                    ok = code in (0, "0") and data.get("data")
                    payload = json.dumps(data.get("data"), ensure_ascii=True)[:350]
                    print(f"  {'[OK]' if ok else '[FAIL]'} {body}")
                    print(f"     code={code} msg={data.get('msg') or data.get('error_message')}")
                    print(f"     data={payload}")

                    # Найцінніше: чи це /ul/-лінк, який можна класти прямо в кнопку TG.
                    blob = json.dumps(data, ensure_ascii=True)
                    if "/ul/" in blob:
                        print("     * Found /ul/ link in response - can send to Telegram directly!")
                except Exception as e:
                    print(f"  !  {body} -> {type(e).__name__}: {e}")


async def _open_db():
    from core.storage.merchant_db import MerchantDB
    db = MerchantDB()
    await db.start()
    return db


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--pub-user-id", required=True, help="публічний id мерчанта OKX")
    ap.add_argument("--order-id", default="", help="id ордера (необов'язково)")
    ap.add_argument("--user-id", type=int, default=0)
    args = ap.parse_args()
    asyncio.run(probe(args.pub_user_id, args.order_id, args.user_id))


if __name__ == "__main__":
    main()
