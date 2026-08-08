#!/usr/bin/env python3
"""
Показує, які саме URL згенерує поточний код — без бота, без Telegram, без
GitHub Pages.

Навіщо. «Посилання не працює» може означати три різні речі:

  1. код генерує неправильний URL;
  2. код правильний, але кнопка веде на redirect.html, а на GitHub Pages
     лежить стара версія сторінки;
  3. URL правильний, але сам маршрут у застосунку не той.

Ці випадки треба розрізняти, інакше правки йдуть навмання. Скрипт друкує
готовий URL і одразу каже, чи залежить він від зовнішнього деплою.

Запуск з кореня проєкту:
    python -m tools.deeplink.show_links --ad <ID_ОГОЛОШЕННЯ> \
                                        --merchant <ID_МЕРЧАНТА> \
                                        --order <ID_ОРДЕРА>

Додатково друкує готові команди adb, щоб перевірити кожен URL напряму.
"""

from __future__ import annotations

import argparse
import asyncio
import sys
import base64
import time
from urllib.parse import parse_qs, urlparse

from bot.deeplinks import REDIRECT_BASE, app_scheme_url, tg_button_url

PKG = {
    "Binance": "com.binance.dev",
    "OKX": "com.okinc.okex.gp",
    "Bybit": "com.bybit.app",
}


def explain(url: str) -> str:
    """Одним рядком: звідки цей URL і від чого він залежить."""
    if not url:
        return "порожньо — маршруту немає"
    if url.startswith(REDIRECT_BASE):
        return ("⚠️ через redirect.html на GitHub Pages — залежить від ДЕПЛОЮ. "
                "Якщо там стара версія, локальні правки не діють")
    if "/webview/webview" in url:
        try:
            token = parse_qs(urlparse(url).query).get("url", [""])[0]
            inner = base64.b64decode(token + "=" * (-len(token) % 4)).decode()
        except Exception:
            inner = "?"
        return f"webview-шлюз Binance, всередині: {inner}"
    if url.startswith("https://app.binance.com/"):
        return "нативний маршрут Binance"
    if url.startswith("https://app.bybit.com/inapp"):
        return "нативний шлюз Bybit"
    if "/ul/" in url:
        return "універсальний лінк OKX"
    return "звичайний веб-URL — відкриється в браузері"


async def live(ad: str, merchant: str, order: str) -> None:
    """
    Показує, що бот віддасть ПРЯМО ЗАРАЗ — з реальною базою і живою сесією.

    Це той самий шлях, яким іде білдер повідомлення (`tg_button_url_async`),
    тому результат тут = те, що впаде в кнопку Telegram. Якщо тут веб-URL, а
    очікувався dplk — значить справа в сесії або в запобіжнику, і нижче видно,
    в чому саме.
    """
    sys.stdout.reconfigure(encoding="utf-8")
    from core.storage.merchant_db import MerchantDB
    import bot.binance_share as bs
    import bot.okx_share as oks
    from bot.deeplinks import tg_button_url_async

    db = MerchantDB()
    await db.start()

    print(f"\n{'=' * 74}\nСТАН СЕСІЙ\n{'=' * 74}")
    for ex in ("Binance", "OKX", "Bybit"):
        h, c, updated = await db.get_auth_session(ex, 0)
        if not c:
            print(f"  {ex:8} — активної сесії НЕМАЄ")
            continue
        age_h = (time.time() - updated) / 3600 if updated else -1
        marks = []
        if ex == "Binance":
            marks.append("p20t " + ("є" if c.get("p20t") else "НЕМА"))
            hl = {k.lower(): v for k, v in (h or {}).items()}
            marks.append("csrftoken-заголовок " + ("є" if hl.get("csrftoken") else "НЕМА"))
        print(f"  {ex:8} — кукі {len(c)}, оновлено {age_h:.1f} год тому"
              + (("; " + ", ".join(marks)) if marks else ""))

    print(f"\n  запобіжник Binance: {'вільний' if bs.is_available() else 'СПРАЦЮВАВ'}")
    print(f"  запобіжник OKX    : {'вільний' if oks.is_available() else 'СПРАЦЮВАВ'}")

    print(f"\n{'=' * 74}\nЩО ПІДЕ В КНОПКУ ЗАРАЗ\n{'=' * 74}")
    cases = []
    for ex in ("Binance", "OKX", "Bybit"):
        if ad:
            cases.append((ex, "ad", ad))
        if merchant:
            cases.append((ex, "profile", merchant))
        if order:
            cases.append((ex, "order", order))

    for ex, kind, eid in cases:
        url = await tg_button_url_async(ex, kind, eid, db=db)
        print(f"\n  {ex} · {kind}")
        print(f"    {url or '(нічого)'}")
        print(f"    → {explain(url)}")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--ad", default="", help="id оголошення (Order.id)")
    ap.add_argument("--merchant", default="", help="id мерчанта (Order.merchant_id)")
    ap.add_argument("--order", default="", help="id ордера")
    ap.add_argument("--exchange", default="", help="лише одна біржа")
    ap.add_argument("--live", action="store_true",
                    help="звернутись до реальної БД і показати, що бот віддасть зараз")
    args = ap.parse_args()

    if args.live:
        import asyncio
        asyncio.run(live(args.ad, args.merchant, args.order))
        return

    cases = []
    for ex in ("Binance", "OKX", "Bybit"):
        if args.exchange and ex != args.exchange:
            continue
        if args.ad:
            cases.append((ex, "ad", args.ad))
        if args.merchant:
            cases.append((ex, "profile", args.merchant))
        if args.order:
            cases.append((ex, "order", args.order))

    if not cases:
        ap.error("вкажи хоча б один з --ad / --merchant / --order")

    adb_cmds = []

    for ex, kind, eid in cases:
        url = tg_button_url(ex, kind, eid)
        scheme = app_scheme_url(ex, kind, eid)

        print(f"\n{'─' * 74}")
        print(f"{ex} · {kind} · {eid}")
        print(f"{'─' * 74}")
        print(f"  кнопка в Telegram:\n    {url or '(нічого)'}")
        print(f"  → {explain(url)}")
        if scheme:
            print(f"  сира схема (не для кнопки TG):\n    {scheme}")

        for target in filter(None, (url, scheme)):
            if target.startswith(REDIRECT_BASE):
                continue  # це не тестується напряму, це сторінка
            adb_cmds.append((f"{ex}/{kind}", target))

    print(f"\n\n{'═' * 74}")
    print("Перевірити кожен URL напряму, без бота і без GitHub Pages:")
    print(f"{'═' * 74}\n")
    for label, target in adb_cmds:
        pkg = PKG.get(label.split("/")[0], "")
        print(f"# {label}")
        print(f"adb shell am force-stop {pkg}")
        print("adb shell am start -W -a android.intent.action.VIEW "
              f"-c android.intent.category.BROWSABLE -d \"{target}\"")
        print()

    print("Дивись у виводі рядок Activity:")
    print("  NoSupportRouterPathActivity  → роутер не знає такого шляху")
    print("  FiatMainActivity             → Binance відкрив C2C-модуль")
    print("  OrderDetailActivity          → OKX відкрив деталі ордера")
    print("  MainActivity (Bybit)          → нічого не означає, потрібен скріншот")


if __name__ == "__main__":
    main()
