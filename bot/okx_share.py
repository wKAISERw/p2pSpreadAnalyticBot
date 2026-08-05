"""
okx_share.py — отримання коротких універсальних лінків OKX (`/ul/`).

Контекст. Прогін на пристрої показав, що `okx://exchange/p2p/profile?userId=`
веде на вітрину P2P (`com.okinc.p2p.trade.HomePageActivity`), а не на картку
продавця. Нативного маршруту на профіль у нас немає.

Зате в манифесті OKX зареєстрований шлях `/ul/.*` — короткі універсальні лінки
виду `https://www.okx.com/ul/m3w2P7`. Це єдиний P2P-придатний **https**-шлях,
тому такий лінк можна класти прямо в `InlineKeyboardButton`, без redirect.html
і без custom scheme.

Код генерується сервером; віддають його ендпоінти, знайдені в dex:

    /v3/c2c/merchant/share        — мерчант
    /v3/c2c/tradingOrders/share   — ордер

Сесія береться з `auth_sessions` — та сама, що в `SessionManager` для
`/v3/c2c/review/history`.

Деградація така сама, як у binance_share:

    1. /ul/-лінк           → нативний екран, кнопка веде напряму
    2. okx:// через redirect.html → застосунок, але вітрина замість картки
    3. звичайний веб-URL   → браузер

Імена полів запиту і форма відповіді фіксуються після прогону
tools/deeplink/probe_okx_share.py — константа _REQUEST нижче.
"""

from __future__ import annotations

import asyncio
import logging
import re
import time
from typing import Literal
from urllib.parse import quote

logger = logging.getLogger(__name__)

BASE = "https://www.okx.com/v3/c2c"

_CACHE_TTL = 6 * 60 * 60
_NEGATIVE_TTL = 10 * 60
_TIMEOUT = 8

_cache: dict[str, tuple[str, float]] = {}
_lock = asyncio.Lock()

# Той самий запобіжник, що й у binance_share: після кількох невдач поспіль
# перестаємо звертатись зовсім, щоб не сипати помилками в бік біржі.
_FAILS_TO_TRIP = 3
_COOLDOWN = 30 * 60
_consecutive_fails = 0
_disabled_until = 0.0


def is_available() -> bool:
    return time.time() >= _disabled_until

Kind = Literal["order", "profile"]

_REQUEST = {
    "profile": ("merchant/share", "pubUserId"),
    "order":   ("tradingOrders/share", "orderId"),
}


def _extract_share_code(data) -> str:
    """
    Дістає `shareCode` з відповіді.

    Фактична відповідь ендпоінта:

        1. {"shareCode": "AysnnUZlACimN",
            "qrCode": "https://okx.com/ua/p2p?action=otcTransfer&shareCode=AysnnUZlACimN"}

        2. {"password": "Нажмите здесь для торговли криптой на OKX https://okx.com/otc/transfer?shareCode=9ZpaFCwJnn8my SQ7491 [Поделиться кодом:￥9ZpaFCwJnn8my￥]"}

    Брати треба саме код, а не `qrCode`. Шлях `/ua/p2p` у манифесті не
    зареєстрований, тому qrCode-посилання відкриє браузер. А код, підставлений
    у `okx://exchange/merchanthome.com?shareCode=`, відкриває нативний екран
    продавця — підтверджено на пристрої:
    `com.okinc.p2p.userinfo.profile.UserProfilePageActivity`.
    """
    if isinstance(data, dict):
        code = data.get("shareCode")
        if isinstance(code, str) and code.strip():
            return code.strip()
        for v in data.values():
            got = _extract_share_code(v)
            if got:
                return got
    elif isinstance(data, list):
        for v in data:
            got = _extract_share_code(v)
            if got:
                return got
    elif isinstance(data, str):
        m = re.search(r"shareCode=([A-Za-z0-9]+)", data)
        if m:
            return m.group(1)
        m2 = re.search(r"\[Поделиться кодом:[￥$]?([A-Za-z0-9]+)[￥$]?\]", data)
        if m2:
            return m2.group(1)
    return ""


async def _fetch(kind: Kind, entity_id: str, user_id: int, db) -> str:
    from curl_cffi.requests import AsyncSession as CurlSession

    path, field = _REQUEST[kind]

    headers_dict, cookies_dict, _ = await db.get_auth_session("OKX", user_id)
    if not headers_dict or not cookies_dict:
        logger.warning("okx_share: немає сесії OKX")
        return ""

    headers_dict = {k.lower(): v for k, v in headers_dict.items()}
    req_headers = {
        "accept": "application/json",
        "content-type": "application/json",
        "app-type": "web",
        "x-locale": "ru_RU",
    }
    auth = headers_dict.get("authorization")
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

    try:
        # GET із параметром у query — саме так відповідає ендпоінт.
        url = (f"{BASE}/{path}?{field}={quote(str(entity_id), safe='')}"
               f"&t={int(time.time() * 1000)}")
        async with CurlSession(impersonate="chrome124", proxies=proxies) as session:
            resp = await session.get(
                url,
                headers=req_headers,
                cookies=cookies_dict,
                timeout=_TIMEOUT,
            )
        if resp.status_code != 200:
            logger.warning("okx_share %s: HTTP %s", path, resp.status_code)
            return ""
        payload = resp.json()
        if payload.get("code") not in (0, "0"):
            logger.warning("okx_share %s: code=%s msg=%s",
                         path, payload.get("code"), payload.get("msg"))
            return ""
        code = _extract_share_code(payload.get("data") or payload)
        if not code:
            # Друкуємо саму відповідь: без неї незрозуміло, чи це інша форма
            # даних, чи мерчант просто не має share-коду (не верифікований).
            import json as _json
            logger.warning("okx_share %s: shareCode у відповіді немає; data=%s",
                           path, _json.dumps(payload.get("data"),
                                             ensure_ascii=False)[:400])
        return code
    except Exception as e:
        logger.warning("okx_share %s: %s: %s", path, type(e).__name__, e)
        return ""


async def load_cached_code(db, merchant_id: str) -> str:
    """
    Постійний кеш `shareCode` у БД.

    Код мерчанта сталий — він не протухає разом із сесією, якою його дістали.
    Тому сесія потрібна лише в МОМЕНТ збору: далі кнопка відкриває нативну
    картку без будь-якої авторизації, скільки б часу не минуло.

    Саме це прибирає головну ваду попередньої схеми, коли кеш жив у пам'яті
    процесу й помирав разом із перезапуском бота.
    """
    if not merchant_id or db is None:
        return ""
    try:
        async with db._db.execute(
            "SELECT share_code FROM okx_share_codes WHERE merchant_id = ?",
            (str(merchant_id),),
        ) as cur:
            row = await cur.fetchone()
        return (row[0] if row else "") or ""
    except Exception as e:
        logger.debug("okx_share: читання кешу впало: %s", e)
    return ""


async def save_cached_code(db, merchant_id: str, code: str) -> None:
    """Зберігає код назавжди. Помилка запису не має ламати кнопку."""
    if not merchant_id or not code or db is None:
        return
    try:
        await db._db.execute(
            "INSERT OR REPLACE INTO okx_share_codes (merchant_id, share_code, updated_at) "
            "VALUES (?, ?, ?)", (str(merchant_id), str(code), time.time()))
        await db._db.commit()
    except Exception as e:
        logger.debug("okx_share: запис кешу впав: %s", e)


async def get_share_code(kind: Kind, entity_id: str, db, user_id: int = 0) -> str:
    """Повертає `shareCode` або "". Ніколи не кидає виняток."""
    global _consecutive_fails, _disabled_until

    if kind not in _REQUEST or not entity_id or db is None:
        return ""

    key = f"{kind}:{entity_id}:{user_id}"
    now = time.time()

    cached = _cache.get(key)
    if cached and cached[1] > now:
        return cached[0]

    # ВИМКНЕНО. Задум був: код сталий, тому зберігаємо назавжди і кнопка
    # відкриває нативну картку без сесії. Прогін на пристрої 05.08 спростував
    # обидві передумови:
    #
    #   * коди з масового збору НЕ вказують на потрібного мерчанта —
    #     `Bno26f7YUndsE` (мав бути FastDealX) відкрив ВЛАСНИЙ профіль
    #     користувача, ще два — головну застосунку. Відповідь ендпоінта — це
    #     реферальне запрошення («Поделиться кодом:￥…￥» + код виду SQ7491),
    #     а не посилання на картку продавця. Тому кожен виклик повертає новий
    #     унікальний код, і відсутність дублікатів нічого не доводила;
    #   * код ПРОТУХАЄ: `IwngxnsTgPimM`, який о 14:04 відкривав картку Varked
    #     (UserProfilePageActivity), о 23:09 вже вів на головну.
    #
    # Протухлий код гірший за його відсутність: замість робочого webview
    # користувач отримав би головну застосунку. Тому кеш не читаємо, а профіль
    # іде через `okx://app/web` — він працює вічно і без сесії.
    #
    # Таблиця і функції лишені навмисно: якщо колись знайдеться ендпоінт, що
    # видає саме код мерчанта, інфраструктура вже готова.




    if now < _disabled_until:
        return ""

    async with _lock:
        cached = _cache.get(key)
        if cached and cached[1] > now:
            return cached[0]
        if time.time() < _disabled_until:
            return ""
        try:
            link = await _fetch(kind, entity_id, user_id, db)
        except Exception as e:
            logger.warning("okx_share: несподівана помилка %s", e)
            link = ""
        if link:
            _consecutive_fails = 0
            _disabled_until = 0.0
            if kind == "profile":
                # Кладемо назавжди — далі цей мерчант відкривається нативно
                # незалежно від стану сесії.
                await save_cached_code(db, entity_id, link)
        else:
            _consecutive_fails += 1
            if _consecutive_fails >= _FAILS_TO_TRIP:
                _disabled_until = time.time() + _COOLDOWN
                logger.warning(
                    "okx_share: %d невдачі поспіль — вимикаю shareCode на %d хв. "
                    "Схоже, сесія OKX померла.", _consecutive_fails, _COOLDOWN // 60)
        _cache[key] = (link, now + (_CACHE_TTL if link else _NEGATIVE_TTL))
        return link


def clear_cache() -> None:
    """Скидає кеш і запобіжник. Викликати після оновлення сесії."""
    global _consecutive_fails, _disabled_until
    _cache.clear()
    _consecutive_fails = 0
    _disabled_until = 0.0
