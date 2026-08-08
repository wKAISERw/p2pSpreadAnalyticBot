# api/auth.py
"""
Автентифікація користувача дашборду.

Навіщо це окремо від api/security.py: X-API-Key — це секрет ОПЕРАТОРА, він
каже «цьому клієнту можна стукати в API». Він нічого не каже про те, ЯКИЙ
користувач стукає. Через це персональні ендпоінти (/user/filters, /cards,
/accounts/{id}) довіряли telegram_id з параметра запиту — тобто будь-хто,
хто має ключ, читав чужі картки й баланси, просто змінивши цифру в URL.

Тут з'являється друга, ортогональна річ: підтверджена особа. Telegram
підписує дані логіну ключем, похідним від токена бота, — id приходить
доведеним, а не введеним у поле. Далі ми видаємо власний session-токен і
персональні ендпоінти беруть user_id з нього.

Токен підписаний HMAC-SHA256 і не шифрований: усередині лежить лише
telegram_id і час протухання, секретів там немає. Реалізація на stdlib,
щоб не тягнути залежність заради тридцяти рядків.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import logging
import secrets
import time
from typing import Optional

from fastapi import Depends, Header, HTTPException, status

from config import settings

logger = logging.getLogger("ApiAuth")

# Скільки живе сесія дашборду.
SESSION_TTL_SECONDS = 30 * 24 * 3600

# Скільки живий payload віджета Telegram. Рекомендація Telegram — доба.
WIDGET_MAX_AGE_SECONDS = 86400

# Скільки живий одноразовий код з /login у боті.
LOGIN_CODE_TTL_SECONDS = 300


def _session_secret() -> bytes:
    """
    Ключ підпису сесій. Окремий SESSION_SECRET, якщо заданий; інакше
    похідна від токена бота — він є завжди і не покидає сервер.
    """
    explicit = getattr(settings, "session_secret", "") or ""
    if explicit:
        return explicit.encode()
    return hashlib.sha256(
        b"arbix-session:" + (settings.telegram_bot_token or "").encode()
    ).digest()


def _b64(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).decode().rstrip("=")


def _unb64(text: str) -> bytes:
    return base64.urlsafe_b64decode(text + "=" * (-len(text) % 4))


# ─── Session token ────────────────────────────────────────────────────────

def issue_session(user_id: int, ttl: int = SESSION_TTL_SECONDS) -> str:
    payload = {"tid": int(user_id), "exp": int(time.time()) + ttl}
    body = _b64(json.dumps(payload, separators=(",", ":")).encode())
    signature = hmac.new(_session_secret(), body.encode(), hashlib.sha256).digest()
    return f"{body}.{_b64(signature)}"


def read_session(token: str) -> Optional[int]:
    """Повертає telegram_id або None, якщо токен битий/протух."""
    try:
        body, signature = token.split(".", 1)
        expected = hmac.new(_session_secret(), body.encode(), hashlib.sha256).digest()
        if not hmac.compare_digest(_unb64(signature), expected):
            return None
        payload = json.loads(_unb64(body))
        if int(payload.get("exp", 0)) < time.time():
            return None
        return int(payload["tid"])
    except Exception:
        return None


# ─── Перевірка підпису Telegram Login Widget ──────────────────────────────

def verify_telegram_widget(payload: dict) -> int:
    """
    Перевіряє дані Telegram Login Widget за офіційним алгоритмом:
    secret = SHA256(bot_token), далі HMAC-SHA256 по рядку "k=v",
    відсортованому за ключем і склеєному \\n (поле hash виключається).

    Повертає telegram_id або кидає HTTPException.
    """
    data = {k: v for k, v in payload.items() if v is not None and k != "hash"}
    received_hash = str(payload.get("hash") or "")

    if not received_hash:
        raise HTTPException(status_code=400, detail="Немає поля hash")

    check_string = "\n".join(f"{k}={data[k]}" for k in sorted(data))
    secret_key = hashlib.sha256((settings.telegram_bot_token or "").encode()).digest()
    expected = hmac.new(secret_key, check_string.encode(), hashlib.sha256).hexdigest()

    if not hmac.compare_digest(expected, received_hash):
        raise HTTPException(status_code=401, detail="Підпис Telegram не збігається")

    auth_date = int(data.get("auth_date", 0) or 0)
    if not auth_date or time.time() - auth_date > WIDGET_MAX_AGE_SECONDS:
        raise HTTPException(status_code=401, detail="Дані логіну протухли, спробуй ще раз")

    telegram_id = int(data.get("id", 0) or 0)
    if not telegram_id:
        raise HTTPException(status_code=400, detail="Немає поля id")

    return telegram_id


# ─── Одноразові коди з бота ───────────────────────────────────────────────
#
# Віджет Telegram вимагає домену, зареєстрованого в BotFather, — на
# localhost він не працює. Код з /login працює будь-де і доводить володіння
# акаунтом не гірше: отримати його можна лише в діалозі з ботом.

async def _ensure_code_table(db) -> None:
    await db._db.execute(
        """CREATE TABLE IF NOT EXISTS web_login_codes (
               code       TEXT PRIMARY KEY,
               user_id    INTEGER NOT NULL,
               created_at REAL    NOT NULL,
               used       INTEGER NOT NULL DEFAULT 0
           )"""
    )
    await db._db.commit()


async def create_login_code(db, user_id: int) -> str:
    """Генерує код для /login. Старі коди цього юзера одразу гасимо."""
    await _ensure_code_table(db)
    code = f"{secrets.randbelow(1_000_000):06d}"
    await db._db.execute("DELETE FROM web_login_codes WHERE user_id = ?", (user_id,))
    await db._db.execute(
        "INSERT INTO web_login_codes (code, user_id, created_at, used) VALUES (?, ?, ?, 0)",
        (code, user_id, time.time()),
    )
    await db._db.commit()
    return code


async def redeem_login_code(db, code: str) -> Optional[int]:
    """Гасить код і повертає telegram_id. Один код — один вхід."""
    await _ensure_code_table(db)

    # Прибираємо протухлі, щоб таблиця не росла і щоб код не можна було
    # використати через годину після генерації.
    await db._db.execute(
        "DELETE FROM web_login_codes WHERE created_at < ?",
        (time.time() - LOGIN_CODE_TTL_SECONDS,),
    )
    await db._db.commit()

    async with db._db.execute(
        "SELECT user_id, created_at, used FROM web_login_codes WHERE code = ?",
        (str(code).strip(),),
    ) as cur:
        row = await cur.fetchone()

    if not row or row["used"]:
        return None
    if time.time() - row["created_at"] > LOGIN_CODE_TTL_SECONDS:
        return None

    await db._db.execute("DELETE FROM web_login_codes WHERE code = ?", (str(code).strip(),))
    await db._db.commit()
    return int(row["user_id"])


# ─── Прив'язка Google ─────────────────────────────────────────────────────

async def _ensure_identity_table(db) -> None:
    await db._db.execute(
        """CREATE TABLE IF NOT EXISTS web_identities (
               provider    TEXT    NOT NULL,
               external_id TEXT    NOT NULL,
               user_id     INTEGER NOT NULL,
               email       TEXT,
               linked_at   REAL    NOT NULL,
               PRIMARY KEY (provider, external_id)
           )"""
    )
    await db._db.commit()


async def link_identity(db, provider: str, external_id: str, user_id: int, email: str = "") -> None:
    await _ensure_identity_table(db)
    await db._db.execute(
        """INSERT INTO web_identities (provider, external_id, user_id, email, linked_at)
           VALUES (?, ?, ?, ?, ?)
           ON CONFLICT(provider, external_id)
           DO UPDATE SET user_id = excluded.user_id, email = excluded.email,
                         linked_at = excluded.linked_at""",
        (provider, str(external_id), int(user_id), email, time.time()),
    )
    await db._db.commit()


async def unlink_identity(db, provider: str, user_id: int) -> int:
    await _ensure_identity_table(db)
    cursor = await db._db.execute(
        "DELETE FROM web_identities WHERE provider = ? AND user_id = ?",
        (provider, int(user_id)),
    )
    await db._db.commit()
    return cursor.rowcount


async def resolve_identity(db, provider: str, external_id: str) -> Optional[int]:
    await _ensure_identity_table(db)
    async with db._db.execute(
        "SELECT user_id FROM web_identities WHERE provider = ? AND external_id = ?",
        (provider, str(external_id)),
    ) as cur:
        row = await cur.fetchone()
    return int(row["user_id"]) if row else None


async def list_identities(db, user_id: int) -> list[dict]:
    await _ensure_identity_table(db)
    async with db._db.execute(
        "SELECT provider, email, linked_at FROM web_identities WHERE user_id = ?",
        (int(user_id),),
    ) as cur:
        rows = await cur.fetchall()
    return [dict(r) for r in rows]


# ─── FastAPI-залежності ───────────────────────────────────────────────────

async def optional_session(
    authorization: Optional[str] = Header(default=None),
) -> Optional[int]:
    """telegram_id з Bearer-токена, або None якщо його немає/він битий."""
    if not authorization or not authorization.lower().startswith("bearer "):
        return None
    return read_session(authorization[7:].strip())


async def require_session(session_user_id: Optional[int] = Depends(optional_session)) -> int:
    if session_user_id is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Потрібен вхід через Telegram",
        )
    return session_user_id


async def require_admin(telegram_id: int = Depends(require_session)) -> int:
    """
    Дії, що впливають на всіх: старт/стоп ядра, глобальні налаштування,
    доступність бірж, спільний чорний список.

    Живе тут, а не в окремому роутері, бо потрібна всім трьом: control,
    dashboard і personal. Ховати кнопку у фронтенді недостатньо — HTTP
    залишається відкритим для будь-кого, хто вміє в curl.
    """
    from bot.handlers.core import _is_admin

    if not _is_admin(telegram_id):
        raise HTTPException(status_code=403, detail="Потрібні права адміністратора")
    return telegram_id


def resolve_user_id(requested: Optional[int], session_user_id: Optional[int]) -> int:
    """
    Кого саме читаємо/пишемо.

    * Є сесія — беремо id з неї. Чужий id дозволений тільки адміну,
      інакше 403: саме тут закривається підміна telegram_id у запиті.
    * Сесії немає — 401. Раніше тут був фолбек «X-API-Key і є авторизацією»,
      і він мав сенс, поки ключ знав лише оператор. У реальному деплої ключ
      підставляє проксі перед статикою (deploy/Caddyfile), тобто його має
      кожен відвідувач сайту — і фолбек перетворював `?telegram_id=` на
      публічний доступ до чужих балансів, ключів і карток.

      Повернути стару поведінку можна через API_KEY_IS_IDENTITY=true, якщо
      порт справді закритий і ходять тільки скрипти оператора.
    """
    from bot.handlers.core import _is_admin

    if session_user_id is not None:
        if requested and requested != session_user_id and not _is_admin(session_user_id):
            raise HTTPException(status_code=403, detail="Це не твої дані")
        return requested if (requested and _is_admin(session_user_id)) else session_user_id

    if not getattr(settings, "api_key_is_identity", False):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Потрібен вхід через Telegram",
        )

    if not requested:
        raise HTTPException(
            status_code=401,
            detail="Потрібен вхід через Telegram або явний telegram_id",
        )
    return requested
