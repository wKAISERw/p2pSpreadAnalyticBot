"""
binance_share.py — отримання коротких share-лінків Binance (`dplk`).

Контекст. Диплінк-роутер застосунку Binance не має маршруту на картку
оголошення: `/fiat/ads/detail` він відкидає через `NoSupportRouterPathActivity`.
Єдиний шлях на нативний екран — короткий лінк виду
`https://www.binance.com/<lang>/qr/dplk<hash>`, який застосунок генерує сам при
натисканні «Поділитися». Хеш створюється сервером під конкретне оголошення,
тому зібрати його локально неможливо.

Ці ж лінки видають приватні ендпоінти, знайдені в dex:

    /bapi/c2c/v1/private/c2c/share/adv-share          — оголошення
    /bapi/c2c/v1/private/c2c/share/advertiser-share   — профіль мерчанта

Сесія береться з `auth_sessions` — та сама, що вже використовується в
`SessionManager._try_binance_refresh`.

Деградація навмисна і триступенева:

    1. dplk у webview-шлюзі  → нативний екран застосунку
    2. веб-URL у webview-шлюзі → потрібна сторінка в застосунку (авторизовано)
    3. звичайний веб-URL       → браузер

Навіть якщо приватний ендпоінт відвалиться або зміняться імена полів, рівень 2
працює без жодного мережевого виклику, тож кнопка ніколи не «ламається» —
вона лише стає трохи менш зручною.
"""

from __future__ import annotations

import asyncio
import logging
import time
from typing import Literal, Optional

logger = logging.getLogger(__name__)

BASE = "https://c2c.binance.com/bapi/c2c/v1/private/c2c/share"

_CACHE_TTL = 6 * 60 * 60          # dplk живе довго; 6 год з запасом
_NEGATIVE_TTL = 10 * 60           # не довбати ендпоінт після невдачі
_TIMEOUT = 8

# key -> (url, expires_at)
_cache: dict[str, tuple[str, float]] = {}
_lock = asyncio.Lock()

Kind = Literal["ad", "profile"]

# Імена полів фіксуються після прогону tools/deeplink/probe_binance_share.py.
_REQUEST = {
    "ad":      ("adv-share", "advNo"),
    "profile": ("advertiser-share", "advertiserNo"),
}


def _extract_link(data) -> str:
    """
    Витягує короткий share-лінк з відповіді.

    Форма нам точно не відома, тому логіка дві стадії:
      1. будь-який рядок будь-де в структурі, що схожий на короткий лінк
         (`/qr/dplk…` або містить `dplk`) — це і є ціль;
      2. якщо такого немає — рядок за відомими ключами (shareLink, link, …).

    Стадія 1 головна: `advertiser-share` повертає об'єкт з даними мерчанта,
    а сам dplk лежить в окремому полі, ім'я якого могло змінитись. Пошук за
    підрядком `dplk` не залежить від імені.
    """
    # Стадія 1 (головна): рядок з коротким кодом будь-де в структурі.
    hit = _find_dplk(data)
    if hit:
        return hit

    # Стадія 2: значення під відомими share-ключами, на будь-якій глибині.
    # НЕ «будь-який http» — інакше вихопимо аватар чи іконку.
    return _find_by_key(data, ("shareLink", "shortLink", "shortUrl", "shareUrl",
                               "qrUrl", "qrCode", "deepLink", "link", "url"))


def _find_by_key(data, keys) -> str:
    if isinstance(data, dict):
        for k in keys:
            v = data.get(k)
            if isinstance(v, str) and v.startswith("http"):
                return v
        for v in data.values():
            got = _find_by_key(v, keys)
            if got:
                return got
    if isinstance(data, list):
        for v in data:
            got = _find_by_key(v, keys)
            if got:
                return got
    return ""


def _find_dplk(data) -> str:
    """Рекурсивно шукає рядок з коротким share-кодом Binance (`dplk`/`/qr/`)."""
    if isinstance(data, str):
        if data.startswith("http") and ("dplk" in data or "/qr/" in data):
            return data
        return ""
    if isinstance(data, dict):
        for v in data.values():
            got = _find_dplk(v)
            if got:
                return got
    if isinstance(data, list):
        for v in data:
            got = _find_dplk(v)
            if got:
                return got
    return ""


async def _fetch(kind: Kind, entity_id: str, user_id: int, db) -> str:
    from curl_cffi.requests import AsyncSession as CurlSession

    path, field = _REQUEST[kind]

    headers_dict, cookies_dict, _ = await db.get_auth_session("Binance", user_id)
    if not headers_dict or not cookies_dict:
        logger.debug("binance_share: немає сесії Binance")
        return ""
    cookies_dict = cookies_dict or {}

    # Форма запиту взята з core/engine/route_executor.py.
    #
    # ЗАСТЕРЕЖЕННЯ: той код ніколи не виконувався на живому акаунті (мейкера не
    # було), тобто це НЕ перевірений еталон, а лише розумніша за попередню
    # гіпотеза. Але вона розумніша з двох причин:
    #   1. беруться ВСІ збережені заголовки, а не довільна вибірка з п'яти —
    #      Binance перевіряє і device-fingerprint, і трасувальні заголовки,
    #      яких у вибірці просто не було;
    #   2. csrf іде як `X-CSRF-TOKEN` і береться з КУКІВ. Перша версія слала
    #      `csrftoken` і шукала його в заголовках — там його немає.
    # Що з цього справді потрібне, покаже tools/deeplink/check_binance_session.py:
    # він б'є по драбинці і не спирається на жодне припущення.
    req_headers = dict(headers_dict)
    for bad in ("Content-Length", "content-length", "Accept-Encoding",
                "accept-encoding", "if-none-match", "If-None-Match"):
        req_headers.pop(bad, None)
    req_headers["Accept"] = "application/json"
    req_headers["Content-Type"] = "application/json"

    # Реферер має вказувати на сторінку самого мерчанта/оголошення — приватні
    # C2C-ендпоінти це звіряють. Загальний p2p.binance.com/ дає 401.
    eid = quote(str(entity_id), safe="")
    req_headers["Referer"] = (
        f"https://c2c.binance.com/uk-UA/advertiserDetail?advertiserNo={eid}"
    )

    # csrftoken НЕ дорівнює куці cr00 — це окреме значення, і воно вже лежить
    # серед збережених заголовків. Дублюємо його в X-CSRF-TOKEN лише якщо є
    # справжній заголовок; кука cr00 сюди НЕ підставляється (перевірено на
    # живому cURL: csrftoken != cr00).
    csrf = headers_dict.get("csrftoken") or headers_dict.get("Csrftoken")
    if csrf:
        req_headers.setdefault("X-CSRF-TOKEN", csrf)

    try:
        from config import settings
        proxy = getattr(settings, "proxy_url", None)
    except Exception:
        proxy = None
    proxies = {"http": proxy, "https": proxy} if proxy else None

    try:
        async with CurlSession(impersonate="chrome124", proxies=proxies) as session:
            resp = await session.post(
                f"{BASE}/{path}",
                headers=req_headers,
                cookies=cookies_dict,
                json={field: str(entity_id)},
                timeout=_TIMEOUT,
            )
        if resp.status_code != 200:
            logger.debug("binance_share %s: HTTP %s", path, resp.status_code)
            return ""
        payload = resp.json()
        if payload.get("code") != "000000":
            logger.debug("binance_share %s: code=%s msg=%s",
                         path, payload.get("code"), payload.get("message"))
            return ""
        link = _extract_link(payload.get("data"))
        if not link:
            logger.debug("binance_share %s: лінка немає у відповіді %s", path, payload)
        return link
    except Exception as e:
        logger.debug("binance_share %s: %s: %s", path, type(e).__name__, e)
        return ""


async def get_share_link(kind: Kind, entity_id: str, db, user_id: int = 0) -> str:
    """
    Повертає короткий dplk-лінк або "" якщо не вийшло.

    Ніколи не кидає виняток — виклик у білдері повідомлення не має права
    завалити відправку алерту.
    """
    if kind not in _REQUEST or not entity_id or db is None:
        return ""

    key = f"{kind}:{entity_id}:{user_id}"
    now = time.time()

    cached = _cache.get(key)
    if cached and cached[1] > now:
        return cached[0]

    async with _lock:
        cached = _cache.get(key)                       # могли встигнути поки чекали
        if cached and cached[1] > now:
            return cached[0]
        try:
            link = await _fetch(kind, entity_id, user_id, db)
        except Exception as e:
            logger.debug("binance_share: несподівана помилка %s", e)
            link = ""
        _cache[key] = (link, now + (_CACHE_TTL if link else _NEGATIVE_TTL))
        return link


def clear_cache() -> None:
    _cache.clear()
