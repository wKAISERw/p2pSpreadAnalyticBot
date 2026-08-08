#!/usr/bin/env python3
"""
Пробник приватних share-ендпоінтів Binance C2C.

Навіщо. Кнопка «Поділитися» в застосунку віддає короткий лінк виду
`https://www.binance.com/uk-UA/qr/dplk<hash>`. Рядка `dplk` у dex немає взагалі —
отже код генерується на сервері, і вгадати його неможливо. Але в dex є три
приватні ендпоінти, які цей лінк і роблять:

    /bapi/c2c/v1/private/c2c/share/adv-share          — оголошення
    /bapi/c2c/v1/private/c2c/share/advertiser-share   — профіль мерчанта
    /bapi/c2c/v1/private/c2c/share/adv-search-share   — пошукова видача

У бота вже є авторизована сесія Binance (auth_sessions у БД), тобто він може
викликати їх сам і покласти готовий dplk у повідомлення.

Скрипт нічого не змінює — лише пробує кілька варіантів імені параметра і
показує сирі відповіді, щоб зафіксувати робочий контракт.

Запуск з кореня проєкту:
    python -m tools.deeplink.probe_binance_share --adv-no <НОМЕР_ОГОЛОШЕННЯ> \
                                                 --advertiser-no <ID_МЕРЧАНТА>
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys

BASE = "https://c2c.binance.com/bapi/c2c/v1/private/c2c/share"


def _find_link(data) -> str:
    """Рекурсивно шукає у відповіді будь-який http-рядок з `dplk`/`/qr/`."""
    if isinstance(data, str):
        if data.startswith("http") and ("dplk" in data or "/qr/" in data):
            return data
        return ""
    if isinstance(data, dict):
        for v in data.values():
            got = _find_link(v)
            if got:
                return got
    if isinstance(data, list):
        for v in data:
            got = _find_link(v)
            if got:
                return got
    return ""

ENDPOINTS = {
    "adv-share": [
        {"advNo": "{adv}"},
        {"advNo": "{adv}", "shareType": "ADV"},
        {"advOrderNumber": "{adv}"},
        {"adNo": "{adv}"},
    ],
    "advertiser-share": [
        {"advertiserNo": "{merchant}"},
        {"userNo": "{merchant}"},
        {"advertiserNo": "{merchant}", "shareType": "ADVERTISER"},
    ],
    "adv-search-share": [
        {"advNo": "{adv}"},
        {"asset": "USDT", "fiat": "UAH", "tradeType": "BUY"},
    ],
}


async def probe(adv_no: str, advertiser_no: str, user_id: int = 0) -> None:
    from curl_cffi.requests import AsyncSession as CurlSession

    from config import settings

    # Сесія береться тим самим шляхом, що і в SessionManager._try_binance_refresh.
    from core.workers.session_manager import SessionManager  # noqa: F401

    db = await _open_db()
    headers_dict, cookies_dict, _ = await db.get_auth_session("Binance", user_id)
    if not headers_dict or not cookies_dict:
        sys.exit("Немає збереженої сесії Binance. Спочатку прогони SessionManager.")

    # Перша версія збирала заголовки «з голови» і діставала 401 при живій сесії.
    # Тут форма з core/engine/route_executor.py — усі збережені заголовки плюс
    # X-CSRF-TOKEN з кукі.
    #
    # Це гіпотеза, а не еталон: route_executor ніколи не запускався на живому
    # акаунті. Якщо 401 лишиться — спершу прогони check_binance_session.py,
    # він показує, на якому рівні ламається авторизація, без здогадок.
    cookies_dict = cookies_dict or {}
    req_headers = dict(headers_dict)
    for k in ("content-length", "Content-Length", "accept-encoding", "Accept-Encoding"):
        req_headers.pop(k, None)
    req_headers["Accept"] = "application/json"
    req_headers["Content-Type"] = "application/json"
    req_headers["Referer"] = "https://p2p.binance.com/"
    csrf = cookies_dict.get("csrftoken", "")
    if csrf:
        req_headers["X-CSRF-TOKEN"] = csrf

    proxies = (
        {"http": settings.proxy_url, "https": settings.proxy_url}
        if getattr(settings, "proxy_url", None)
        else None
    )

    async with CurlSession(impersonate="chrome124", proxies=proxies) as session:
        for path, payloads in ENDPOINTS.items():
            print(f"\n{'=' * 70}\n{BASE}/{path}\n{'=' * 70}")
            for tpl in payloads:
                body = {
                    k: v.format(adv=adv_no, merchant=advertiser_no) if isinstance(v, str) else v
                    for k, v in tpl.items()
                }
                try:
                    resp = await session.post(
                        f"{BASE}/{path}",
                        headers=req_headers,
                        cookies=cookies_dict,
                        json=body,
                        timeout=12,
                    )
                    snippet = resp.text[:400]
                    try:
                        data = resp.json()
                        code = data.get("code")
                        msg = data.get("message")
                        verdict = "[OK]" if code == "000000" and data.get("data") else "[FAIL]"
                        print(f"  {verdict} {body}")
                        print(f"     code={code} message={msg}")

                        # Головне питання не «чи 000000», а «чи є в data лінк».
                        link = _find_link(data.get("data"))
                        if link:
                            print(f"     >>> ЛІНК ЗНАЙДЕНО: {link}")
                        else:
                            print("     >>> ЛІНКА В data НЕМАЄ — авторизація ок, "
                                  "але dplk не в цьому полі. Повна відповідь нижче:")
                            # друкуємо всю структуру, щоб побачити, де він
                            print("     " + json.dumps(data.get("data"),
                                                        ensure_ascii=False)[:1500])
                    except Exception:
                        print(f"  ?  {body} -> HTTP {resp.status_code}: {snippet}")
                except Exception as e:
                    print(f"  !  {body} -> {type(e).__name__}: {e}")


async def _open_db():
    """Відкриває БД за допомогою MerchantDB facade."""
    from core.storage.merchant_db import MerchantDB
    db = MerchantDB()
    await db.start()
    return db


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--adv-no", required=True, help="номер оголошення (Order.id)")
    ap.add_argument("--advertiser-no", required=True, help="id мерчанта (Order.merchant_id)")
    ap.add_argument("--user-id", type=int, default=0)
    args = ap.parse_args()
    asyncio.run(probe(args.adv_no, args.advertiser_no, args.user_id))


if __name__ == "__main__":
    main()
