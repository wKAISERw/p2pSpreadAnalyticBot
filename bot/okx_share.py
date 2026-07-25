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

Kind = Literal["order", "profile"]

_REQUEST = {
    "profile": ("merchant/share", "pubUserId"),
    "order":   ("tradingOrders/share", "orderId"),
}


def _extract_share_code(data) -> str:
    """
    Дістає `shareCode` з відповіді.

    Фактична відповідь ендпоінта:

        {"shareCode": "AysnnUZlACimN",
         "qrCode": "https://okx.com/ua/p2p?action=otcTransfer&shareCode=AysnnUZlACimN"}

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
    if isinstance(data, list):
        for v in data:
            got = _extract_share_code(v)
            if got:
                return got
    return ""


async def _fetch(kind: Kind, entity_id: str, user_id: int, db) -> str:
    from curl_cffi.requests import AsyncSession as CurlSession

    path, field = _REQUEST[kind]

    headers_dict, cookies_dict, _ = await db.get_auth_session("OKX", user_id)
    if not headers_dict or not cookies_dict:
        logger.debug("okx_share: немає сесії OKX")
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
            logger.debug("okx_share %s: HTTP %s", path, resp.status_code)
            return ""
        payload = resp.json()
        if payload.get("code") not in (0, "0"):
            logger.debug("okx_share %s: code=%s msg=%s",
                         path, payload.get("code"), payload.get("msg"))
            return ""
        code = _extract_share_code(payload.get("data") or payload)
        if not code:
            logger.debug("okx_share %s: shareCode у відповіді немає", path)
        return code
    except Exception as e:
        logger.debug("okx_share %s: %s: %s", path, type(e).__name__, e)
        return ""


async def get_share_code(kind: Kind, entity_id: str, db, user_id: int = 0) -> str:
    """Повертає `shareCode` або "". Ніколи не кидає виняток."""
    if kind not in _REQUEST or not entity_id or db is None:
        return ""

    key = f"{kind}:{entity_id}:{user_id}"
    now = time.time()

    cached = _cache.get(key)
    if cached and cached[1] > now:
        return cached[0]

    async with _lock:
        cached = _cache.get(key)
        if cached and cached[1] > now:
            return cached[0]
        try:
            link = await _fetch(kind, entity_id, user_id, db)
        except Exception as e:
            logger.debug("okx_share: несподівана помилка %s", e)
            link = ""
        _cache[key] = (link, now + (_CACHE_TTL if link else _NEGATIVE_TTL))
        return link


def clear_cache() -> None:
    _cache.clear()
