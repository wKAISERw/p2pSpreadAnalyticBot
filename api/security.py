# api/security.py
"""
Автентифікація HTTP API.

Навіщо: до цього /api/v1/* був повністю відкритий. Будь-хто, хто дотягувався
до порту 8000, міг:
  * підкинути власні кукі біржі через POST /session/receive — і сканер ходив
    би на біржу з чужою сесією;
  * записати API-ключі на довільний telegram_id (POST /credentials/...);
  * прочитати баланси будь-якого юзера (GET /accounts/{telegram_id}).

Модель проста і достатня для одного інстансу: спільний секрет `API_KEY` з .env,
який передається заголовком `X-API-Key`. Букмарклет-скрипт заголовки слати не
вміє, тому додатково приймаємо `?api_key=` — рівно для цього випадку.

Якщо API_KEY не заданий, захист вимкнено, але при старті пишемо WARNING —
щоб «відкрито» ніколи не було випадковим станом.
"""
from __future__ import annotations

import hmac
import logging
from typing import Optional

from fastapi import Header, HTTPException, Query, status

from config import settings

logger = logging.getLogger("ApiSecurity")

_UNSET_WARNING = (
    "🔓 API_KEY не заданий — HTTP API відкритий без автентифікації. "
    "Це припустимо лише якщо порт слухає localhost і нікуди не проброшений. "
    "Згенеруй ключ (`python -c \"import secrets;print(secrets.token_urlsafe(32))\"`) "
    "і додай API_KEY=... у .env"
)


def api_auth_enabled() -> bool:
    return bool(getattr(settings, "api_key", "") or "")


def warn_if_unprotected() -> None:
    """Викликається один раз на старті застосунку."""
    if not api_auth_enabled():
        logger.warning(_UNSET_WARNING)
    else:
        logger.info("🔐 HTTP API захищено ключем (заголовок X-API-Key)")


async def require_api_key(
    x_api_key: Optional[str] = Header(default=None, alias="X-API-Key"),
    api_key: Optional[str] = Query(
        default=None,
        description="Запасний спосіб для клієнтів, які не можуть слати заголовки (букмарклет)",
    ),
) -> None:
    """
    FastAPI-залежність: пускає далі лише з валідним ключем.

    Порівняння через hmac.compare_digest — щоб не давати таймінгової підказки
    про правильний префікс ключа.
    """
    expected = getattr(settings, "api_key", "") or ""
    if not expected:
        return  # захист вимкнено усвідомлено; попередження вже в лозі

    provided = x_api_key or api_key or ""
    if not provided or not hmac.compare_digest(provided, expected):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Невірний або відсутній API-ключ",
            headers={"WWW-Authenticate": "X-API-Key"},
        )
