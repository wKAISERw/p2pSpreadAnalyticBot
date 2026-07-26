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
from urllib.parse import quote

logger = logging.getLogger(__name__)

BASE = "https://c2c.binance.com/bapi/c2c/v1/private/c2c/share"

_CACHE_TTL = 6 * 60 * 60          # dplk живе довго; 6 год з запасом
_NEGATIVE_TTL = 10 * 60           # не довбати ендпоінт після невдачі
_TIMEOUT = 8

# key -> (url, expires_at)
_cache: dict[str, tuple[str, float]] = {}
_lock = asyncio.Lock()

# Запобіжник. Коли сесія помирає, невдалим стає КОЖЕН мерчант окремо, і при
# потоці ордерів це десятки 401 щохвилини — з боку Binance виглядає як атака на
# власний акаунт. Тому після кількох поспіль невдач вимикаємо звернення
# глобально на пів години: усі кнопки тихо йдуть канонічним веб-шляхом.
_FAILS_TO_TRIP = 3
_COOLDOWN = 30 * 60
_consecutive_fails = 0
_disabled_until = 0.0


def _note_failure() -> None:
    global _consecutive_fails, _disabled_until
    _consecutive_fails += 1
    if _consecutive_fails >= _FAILS_TO_TRIP:
        _disabled_until = time.time() + _COOLDOWN
        logger.warning(
            "binance_share: %d невдачі поспіль — вимикаю dplk на %d хв, "
            "кнопки підуть у веб. Схоже, сесія Binance померла.",
            _consecutive_fails, _COOLDOWN // 60)


def _note_success() -> None:
    global _consecutive_fails, _disabled_until
    _consecutive_fails = 0
    _disabled_until = 0.0


def is_available() -> bool:
    """Чи не спрацював запобіжник. Зручно для статусу в адмінці."""
    return time.time() >= _disabled_until

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

    # Другий рубіж захисту. Сканер зберігає id оголошення як `bn_<advNo>`
    # (exchanges/binance.py). Якщо префікс долетить сюди, Binance відповість
    # `code=083626 Оголошення не існує`, і причина буде неочевидною.
    entity_id = str(entity_id)
    if entity_id.startswith("bn_"):
        entity_id = entity_id[3:]

    headers_dict, cookies_dict, _ = await db.get_auth_session("Binance", user_id)
    if not headers_dict or not cookies_dict:
        logger.warning("binance_share: немає сесії Binance")
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

    # ПОРЯДОК ТУТ КРИТИЧНИЙ, НЕ МІНЯТИ.
    #
    # `csrftoken` і кука `cr00` — це РІЗНІ значення. Доведено на живому cURL
    # з браузера: заголовок був `csrftoken: 0703c7bc…`, а кука `cr00: D73053C4…`.
    # Якщо підставити `cr00` замість справжнього csrftoken, Binance віддає
    # 401 "Please log in first", і кнопка тихо падає на веб.
    #
    # Тому спершу шукаємо СПРАВЖНІЙ заголовок серед збережених, і лише якщо
    # його там немає — пробуємо куку як останній шанс.
    _hl = {k.lower(): v for k, v in headers_dict.items()}
    csrf_val = _hl.get("csrftoken") or _hl.get("x-csrf-token") or cookies_dict.get("csrftoken")
    if not csrf_val:
        csrf_val = cookies_dict.get("cr00")
        if csrf_val:
            logger.warning(
                "binance_share: справжнього csrftoken немає в сесії, беру cr00 — "
                "запит найімовірніше дасть 401. Перезніми cURL з приватного "
                "запиту до c2c.binance.com.")
    if csrf_val:
        req_headers["X-CSRF-TOKEN"] = csrf_val
        req_headers["csrftoken"] = csrf_val

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
            logger.warning("binance_share %s: HTTP %s", path, resp.status_code)
            return ""
        payload = resp.json()
        if payload.get("code") != "000000":
            logger.warning("binance_share %s: code=%s msg=%s",
                         path, payload.get("code"), payload.get("message"))
            return ""
        link = _extract_link(payload.get("data"))
        if not link:
            logger.warning("binance_share %s: лінка немає у відповіді %s", path, payload)
        return link
    except Exception as e:
        logger.warning("binance_share %s: %s: %s", path, type(e).__name__, e)
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

    # Запобіжник спрацював — не звертаємось узагалі, одразу віддаємо порожньо.
    if now < _disabled_until:
        return ""

    async with _lock:
        cached = _cache.get(key)                       # могли встигнути поки чекали
        if cached and cached[1] > now:
            return cached[0]
        if time.time() < _disabled_until:
            return ""
        try:
            link = await _fetch(kind, entity_id, user_id, db)
        except Exception as e:
            logger.warning("binance_share: несподівана помилка %s", e)
            link = ""
        if link:
            _note_success()
        else:
            _note_failure()
        _cache[key] = (link, now + (_CACHE_TTL if link else _NEGATIVE_TTL))
        return link


def clear_cache() -> None:
    """Скидає кеш і запобіжник. Викликати після оновлення сесії."""
    global _consecutive_fails, _disabled_until
    _cache.clear()
    _consecutive_fails = 0
    _disabled_until = 0.0
