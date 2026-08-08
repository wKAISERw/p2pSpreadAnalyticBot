#!/usr/bin/env python3
"""
Чистий тест: чи досяжний приватний C2C-ендпоінт Binance HTTP-клієнтом.

Ідея. Замість гадати, чому 401, повторюємо ДОСЛІВНО запит із браузера — той
самий, що там дав 200 — і дивимось, чи `curl_cffi` (з підробленим TLS Chrome)
дістане той самий результат.

  * якщо копія дає 200 -> HTTP-шлях робочий, справа лише в заголовках/рефері,
    які губилися в зонді; share-ендпоінт запрацює з правильним рефером;
  * якщо копія дає 401 -> справа не в куках і не в TLS (він емульований), а в
    порядку HTTP/2-заголовків або JA4H. Тоді HTTP-клієнтом Binance не пройти,
    і питання закрите чесно.

Безпека. Скрипт читає cURL з ФАЙЛУ, а не з аргументів (щоб секрети не осідали
в історії shell). Нічого чутливого не друкує — лише імена заголовків, статуси
і код відповіді. Файл із cURL раджу видалити одразу після прогону.

Використання:
    1. DevTools -> Network -> правий клік на приватному запиті до
       c2c.binance.com -> Copy -> Copy as cURL (bash).
    2. Вставити у файл, напр. C:\\p2p_scanner\\_curl.txt (він у .gitignore).
    3. python -m tools.deeplink.probe_from_curl _curl.txt
       (можна додати --share-merchant <advertiserNo>, щоб одразу перевірити
        цільовий share-ендпоінт з рефером, узятим із cURL)
"""

from __future__ import annotations

import argparse
import asyncio
import re
import shlex
import sys
from urllib.parse import urlparse


def parse_curl(text: str) -> tuple[str, str, dict, dict, str]:
    """
    Дуже терпимий парсер `Copy as cURL (bash)`. Повертає:
    (method, url, headers, cookies, body).
    """
    # Прибираємо переноси рядків bash (\ у кінці) і початкове "curl".
    text = text.replace("\\\n", " ").replace("^\n", " ").replace("`\n", " ")
    text = re.sub(r"\n", " ", text)
    try:
        tokens = shlex.split(text)
    except ValueError:
        tokens = text.split()

    url = ""
    method = "GET"
    headers: dict[str, str] = {}
    cookies: dict[str, str] = {}
    body = ""

    it = iter(range(len(tokens)))
    i = 0
    while i < len(tokens):
        t = tokens[i]
        if t == "curl":
            i += 1
            continue
        if t in ("-X", "--request"):
            method = tokens[i + 1]; i += 2; continue
        if t in ("-H", "--header"):
            h = tokens[i + 1]; i += 2
            if ":" in h:
                k, _, v = h.partition(":")
                headers[k.strip().lower()] = v.strip()
            continue
        if t in ("-b", "--cookie"):
            c = tokens[i + 1]; i += 2
            for pair in c.split(";"):
                if "=" in pair:
                    k, _, v = pair.strip().partition("=")
                    cookies[k.strip()] = v.strip()
            continue
        if t in ("--data", "--data-raw", "--data-binary", "-d"):
            body = tokens[i + 1]; i += 2
            if method == "GET":
                method = "POST"
            continue
        if t.startswith("http"):
            url = t
        elif t.startswith("'") or t.startswith('"'):
            inner = t.strip("'\"")
            if inner.startswith("http"):
                url = inner
        i += 1

    # cookie інколи приходить і заголовком "cookie:"
    if "cookie" in headers and not cookies:
        for pair in headers["cookie"].split(";"):
            if "=" in pair:
                k, _, v = pair.strip().partition("=")
                cookies[k.strip()] = v.strip()

    return method, url, headers, cookies, body


def clean_headers(headers: dict) -> dict:
    """Прибираємо те, що ламає повторний запит через інший клієнт."""
    out = dict(headers)
    for bad in ("content-length", "accept-encoding", "if-none-match",
                "if-modified-since", "host", "cookie", "connection"):
        out.pop(bad, None)
    return out


async def run(curl_file: str, share_merchant: str) -> None:
    from curl_cffi.requests import AsyncSession as CurlSession

    text = open(curl_file, encoding="utf-8", errors="replace").read()
    method, url, headers, cookies, body = parse_curl(text)
    if not url:
        sys.exit("Не знайшов URL у cURL. Перевір, що це саме 'Copy as cURL (bash)'.")

    ref = headers.get("referer", "(немає)")
    print(f"метод      : {method}")
    print(f"url        : {urlparse(url).path}")
    print(f"реферер    : {ref}")
    print(f"заголовків : {len(headers)}  ({', '.join(sorted(headers)[:12])}…)")
    print(f"кукі       : {len(cookies)}  (p20t: {'є' if cookies.get('p20t') else 'НЕМА'},"
          f" cr00: {'є' if cookies.get('cr00') else 'НЕМА'},"
          f" csrftoken-заг: {'є' if headers.get('csrftoken') else 'НЕМА'})")

    hdrs = clean_headers(headers)

    try:
        from config import settings
        proxy = getattr(settings, "proxy_url", None)
    except Exception:
        proxy = None
    proxies = {"http": proxy, "https": proxy} if proxy else None

    async with CurlSession(impersonate="chrome124", proxies=proxies) as session:
        # ── Крок 1: дослівна копія браузерного запиту ────────────────────────
        print("\n[1] Дослівна копія запиту з браузера")
        try:
            if method == "POST":
                r = await session.post(url, headers=hdrs, cookies=cookies,
                                       data=body or None, timeout=15)
            else:
                r = await session.get(url, headers=hdrs, cookies=cookies, timeout=15)
            code = _binance_code(r.text)
            print(f"    HTTP {r.status_code}  code={code}  {_verdict(r.status_code, code)}")
        except Exception as e:
            print(f"    {type(e).__name__}: {e}")
            return

        # ── Крок 2: цільовий share-ендпоінт з тим самим рефером ──────────────
        if share_merchant:
            share_url = ("https://c2c.binance.com/bapi/c2c/v1/private/c2c/"
                         "share/advertiser-share")
            sh = dict(hdrs)
            sh["content-type"] = "application/json"
            # реферер, який очікує share: сторінка того ж мерчанта
            sh["referer"] = (f"https://c2c.binance.com/uk-UA/advertiserDetail"
                             f"?advertiserNo={share_merchant}")
            print("\n[2] Цільовий advertiser-share із рефером на сторінку мерчанта")
            for field in ("advertiserNo", "userNo"):
                try:
                    r = await session.post(share_url, headers=sh, cookies=cookies,
                                           json={field: share_merchant}, timeout=15)
                    code = _binance_code(r.text)
                    tag = "shareCode" if "shareCode" in r.text or "dplk" in r.text else ""
                    print(f"    {{{field}}}  HTTP {r.status_code}  code={code}  "
                          f"{_verdict(r.status_code, code)} {tag}")
                    if r.status_code == 200 and ("dplk" in r.text or "shareCode" in r.text):
                        print("    ✅ ПРАЦЮЄ — share-ендпоінт віддав лінк.")
                        break
                except Exception as e:
                    print(f"    {{{field}}}  {type(e).__name__}: {e}")

    print("\nВисновок:")
    print("  [1] 200 -> HTTP-шлях робочий. Якщо [2] теж 200 — Binance закрито,")
    print("           лишається лише прокинути правильний реферер у binance_share.py.")
    print("  [1] 401 -> навіть точна копія не проходить. Тоді це HTTP/2 header-order")
    print("           / JA4H, і HTTP-клієнтом Binance не взяти. Чесний глухий кут.")
    print("\nВидали файл із cURL: секрети.")


def _binance_code(text: str) -> str:
    m = re.search(r'"code"\s*:\s*"?([0-9A-Za-z]+)"?', text or "")
    return m.group(1) if m else "?"


def _verdict(status: int, code: str) -> str:
    if status == 200 and code in ("000000", "0"):
        return "OK"
    if status in (401, 403) or code.startswith("1000"):
        return "ВІДМОВА АВТОРИЗАЦІЇ"
    return ""


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("curl_file", help="файл із 'Copy as cURL (bash)'")
    ap.add_argument("--share-merchant", default="",
                    help="advertiserNo мерчанта — перевірити цільовий share-ендпоінт")
    args = ap.parse_args()
    asyncio.run(run(args.curl_file, args.share_merchant))


if __name__ == "__main__":
    main()
