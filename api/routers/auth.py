# api/routers/auth.py
"""
Вхід у дашборд.

Два способи довести, що ти — це ти в Telegram:
  1. Login Widget — один клік, але вимагає домену, прописаного в BotFather.
  2. Код з команди /login у боті — працює будь-де, зокрема на localhost.

Google — не окрема особа, а лише спосіб входу, прив'язаний до вже
підтвердженого Telegram-акаунта. Інакше довелось би десь брати telegram_id,
а єдиний спосіб його «взяти» без Telegram — повірити користувачу на слово.
"""
from __future__ import annotations

import logging
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from api import auth as auth_lib
from api.security import require_api_key

router = APIRouter(prefix="/api/v1/auth", tags=["Auth"])
logger = logging.getLogger("ApiAuthRouter")


def _db():
    from bot.handlers.core import _db as db
    if db is None:
        raise HTTPException(status_code=503, detail="База даних ще не піднялась")
    return db


async def _ensure_user(db, telegram_id: int) -> None:
    """Перший вхід із сайту реєструє підписника — як і /start у боті."""
    if not await db.get_user_by_id(telegram_id):
        await db.register_user(telegram_id, telegram_id)
        logger.info("👤 Зареєстровано нового користувача через вебдашборд: %s", telegram_id)


async def _session_response(db, telegram_id: int) -> dict:
    from bot.handlers.core import _is_admin

    await _ensure_user(db, telegram_id)
    return {
        "token": auth_lib.issue_session(telegram_id),
        "telegramId": telegram_id,
        "isAdmin": _is_admin(telegram_id),
        "identities": await auth_lib.list_identities(db, telegram_id),
    }


@router.get("/config")
async def auth_config():
    """
    Що фронтенду треба знати до логіну: ім'я бота для віджета.

    Дістаємо через getMe, а не з конфіга — окремої змінної з username немає,
    а помилитись у ній легко (віджет тоді просто мовчки не з'явиться).
    """
    username = ""
    try:
        from bot.handlers.core import _bot
        if _bot:
            me = await _bot.get_me()
            username = me.username or ""
    except Exception as e:
        logger.debug("auth_config get_me: %s", e)

    return {
        "botUsername": username,
        # Віджет має сенс лише коли є username; код з /login працює завжди.
        "widgetAvailable": bool(username),
        "codeAvailable": True,
    }


class WidgetPayload(BaseModel):
    id: int
    auth_date: int
    hash: str
    first_name: Optional[str] = None
    last_name: Optional[str] = None
    username: Optional[str] = None
    photo_url: Optional[str] = None


@router.post("/telegram/widget")
async def login_via_widget(payload: WidgetPayload):
    telegram_id = auth_lib.verify_telegram_widget(payload.model_dump(exclude_none=True))
    return await _session_response(_db(), telegram_id)


class CodePayload(BaseModel):
    code: str


@router.post("/telegram/code")
async def login_via_code(payload: CodePayload):
    db = _db()
    telegram_id = await auth_lib.redeem_login_code(db, payload.code)
    if not telegram_id:
        raise HTTPException(status_code=401, detail="Код невірний або протух")
    return await _session_response(db, telegram_id)


@router.get("/me")
async def whoami(telegram_id: int = Depends(auth_lib.require_session)):
    from bot.handlers.core import _is_admin

    db = _db()
    user = await db.get_user_by_id(telegram_id)
    return {
        "telegramId": telegram_id,
        "isAdmin": _is_admin(telegram_id),
        "isRegistered": bool(user),
        "identities": await auth_lib.list_identities(db, telegram_id),
    }


class GoogleLinkPayload(BaseModel):
    googleUid: str
    email: Optional[str] = ""


@router.post("/link/google")
async def link_google(
    payload: GoogleLinkPayload,
    telegram_id: int = Depends(auth_lib.require_session),
):
    """Прив'язує Google до поточного (вже підтвердженого) Telegram-акаунта."""
    db = _db()

    existing = await auth_lib.resolve_identity(db, "google", payload.googleUid)
    if existing and existing != telegram_id:
        raise HTTPException(
            status_code=409,
            detail="Цей Google-акаунт уже прив'язаний до іншого Telegram",
        )

    await auth_lib.link_identity(db, "google", payload.googleUid, telegram_id, payload.email or "")
    return {"status": "success", "identities": await auth_lib.list_identities(db, telegram_id)}


@router.post("/unlink/google")
async def unlink_google(telegram_id: int = Depends(auth_lib.require_session)):
    db = _db()
    removed = await auth_lib.unlink_identity(db, "google", telegram_id)
    return {"status": "success", "removed": removed}


class GoogleLoginPayload(BaseModel):
    googleUid: str


@router.post("/google")
async def login_via_google(payload: GoogleLoginPayload):
    """
    Вхід уже прив'язаним Google-акаунтом.

    Свідомо не створює користувача: Google-профіль сам по собі не вказує
    на жоден Telegram-акаунт, тому першим завжди має бути вхід через
    Telegram, а Google — прив'язкою до нього.
    """
    db = _db()
    telegram_id = await auth_lib.resolve_identity(db, "google", payload.googleUid)
    if not telegram_id:
        raise HTTPException(
            status_code=404,
            detail="Цей Google не прив'язаний. Увійди через Telegram і прив'яжи його в налаштуваннях.",
        )
    return await _session_response(db, telegram_id)
