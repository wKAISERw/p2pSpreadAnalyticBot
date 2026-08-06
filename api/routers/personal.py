# api/routers/personal.py
"""
Персональні розділи, яких досі не було в HTTP API: снайпер-правила, ліміти
банків і карток, картковий модуль виводу, експериментальні фічі, звіти по
картках, субсидії новачків та адмінське керування юзерами.

Усе, що стосується конкретної людини, бере user_id із сесії (api/auth.py) —
явно передати чужий може лише адмін. Адмінські ендпоінти винесені під
окрему залежність, а не покладаються на те, що фронтенд сховає кнопку.
"""
from __future__ import annotations

import logging
from typing import Any, Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.encoders import jsonable_encoder
from pydantic import BaseModel, Field

from api.auth import optional_session, require_session, resolve_user_id
from api.security import require_api_key
from api.utils import dict_to_camel

router = APIRouter(prefix="/api/v1", tags=["Personal"], dependencies=[Depends(require_api_key)])
logger = logging.getLogger("ApiPersonal")


def _db():
    from bot.handlers.core import _db as db
    if db is None:
        raise HTTPException(status_code=503, detail="База даних ще не піднялась")
    return db


async def require_admin(telegram_id: int = Depends(require_session)) -> int:
    from bot.handlers.core import _is_admin

    if not _is_admin(telegram_id):
        raise HTTPException(status_code=403, detail="Потрібні права адміністратора")
    return telegram_id


async def _owned_card(card_id: str, telegram_id: int) -> dict:
    """
    Картка — персональна річ, тому перевіряємо власника перед будь-якою дією.
    Без цього знання чужого card_id давало б доступ до чужих лімітів.
    """
    from bot.handlers.core import _is_admin

    db = _db()
    async with db._db.execute(
        "SELECT id, owner_id, bank_name FROM cards WHERE id = ?", (card_id,)
    ) as cur:
        row = await cur.fetchone()

    if not row:
        raise HTTPException(status_code=404, detail="Картку не знайдено")
    if row["owner_id"] != telegram_id and not _is_admin(telegram_id):
        raise HTTPException(status_code=403, detail="Це не твоя картка")
    return dict(row)


# ═══════════════════════════════════════════════════════════════════════════
# Снайпер-правила
# ═══════════════════════════════════════════════════════════════════════════
#
# Правило пробиває беззвучний режим: якщо спред і об'єм більші за пороги і
# потрібна біржа збігається — алерт піде зі звуком навіть під час паузи.

_DIRECTIONS = {"BUY", "SELL"}


class SniperRule(BaseModel):
    exchange: str
    # BUY = стежимо за біржею, де продаємо; SELL = де купуємо.
    direction: str
    minSpread: float = Field(default=0, ge=0)
    minVolume: float = Field(default=0, ge=0)


class SniperRulesPayload(BaseModel):
    telegramId: Optional[int] = None
    rules: list[SniperRule]


@router.get("/user/sniper")
async def get_sniper_rules(
    telegram_id: Optional[int] = None,
    session_user_id: Optional[int] = Depends(optional_session),
):
    telegram_id = resolve_user_id(telegram_id, session_user_id)
    user = await _db().get_user_by_id(telegram_id)
    if not user:
        raise HTTPException(status_code=404, detail="User not found")

    return dict_to_camel(jsonable_encoder(user.get("sniper_rules") or []))


@router.post("/user/sniper")
async def update_sniper_rules(
    payload: SniperRulesPayload,
    session_user_id: Optional[int] = Depends(optional_session),
):
    """Замінює весь набір правил — так само, як меню /sniper у боті."""
    telegram_id = resolve_user_id(payload.telegramId, session_user_id)
    db = _db()

    if not await db.get_user_by_id(telegram_id):
        raise HTTPException(status_code=404, detail="User not found")

    rules = []
    for rule in payload.rules:
        direction = rule.direction.upper()
        if direction not in _DIRECTIONS:
            raise HTTPException(
                status_code=400,
                detail=f"direction: очікується BUY або SELL, отримано {rule.direction!r}",
            )
        # Правило без жодного порога спрацьовує на кожен алерт і робить
        # беззвучний режим безглуздим — не даємо зберегти таке мовчки.
        if rule.minSpread <= 0 and rule.minVolume <= 0:
            raise HTTPException(
                status_code=400,
                detail="Правило має мати minSpread або minVolume більший за нуль",
            )
        rules.append({
            "exchange": rule.exchange,
            "direction": direction,
            "min_spread": rule.minSpread,
            "min_volume": rule.minVolume,
        })

    await db.update_sniper_rules(telegram_id, rules)
    return {"status": "success", "count": len(rules)}


# ═══════════════════════════════════════════════════════════════════════════
# Експериментальні фічі
# ═══════════════════════════════════════════════════════════════════════════

@router.get("/user/features")
async def get_features(
    telegram_id: Optional[int] = None,
    session_user_id: Optional[int] = Depends(optional_session),
):
    """Каталог фіч разом зі станом кожної для цього юзера."""
    from bot.handlers.core import EXPERIMENTAL_FEATURES

    telegram_id = resolve_user_id(telegram_id, session_user_id)
    db = _db()

    groups = []
    for group_key, group in EXPERIMENTAL_FEATURES.items():
        features = []
        for key, meta in group.get("features", {}).items():
            features.append({
                "key": key,
                "name": meta.get("name", key),
                "description": meta.get("desc", ""),
                "enabled": await db.get_feature_status(telegram_id, key),
            })
        groups.append({
            "key": group_key,
            "title": group.get("title", group_key),
            "features": features,
        })

    return groups


class FeaturePayload(BaseModel):
    telegramId: Optional[int] = None
    key: str


@router.post("/user/features/toggle")
async def toggle_feature(
    payload: FeaturePayload,
    session_user_id: Optional[int] = Depends(optional_session),
):
    from bot.handlers.core import EXPERIMENTAL_FEATURES

    telegram_id = resolve_user_id(payload.telegramId, session_user_id)

    known = {
        key
        for group in EXPERIMENTAL_FEATURES.values()
        for key in group.get("features", {})
    }
    if payload.key not in known:
        raise HTTPException(status_code=404, detail=f"Невідома фіча: {payload.key}")

    enabled = await _db().toggle_feature_status(telegram_id, payload.key)
    return {"status": "success", "key": payload.key, "enabled": enabled}


# ═══════════════════════════════════════════════════════════════════════════
# Субсидії новачків
# ═══════════════════════════════════════════════════════════════════════════

@router.get("/user/subsidies")
async def get_subsidies(
    telegram_id: Optional[int] = None,
    session_user_id: Optional[int] = Depends(optional_session),
):
    """
    Які субсидії новачка вже витрачені на яких біржах.
    Витрачену сканер більше не показує — інакше алерт вів би на пропозицію,
    якою вже не скористатись.
    """
    telegram_id = resolve_user_id(telegram_id, session_user_id)
    used = await _db().get_used_subsidies(telegram_id)
    return [{"exchange": ex, "used": types} for ex, types in sorted(used.items())]


# ═══════════════════════════════════════════════════════════════════════════
# Картковий модуль: як показувати картки в алертах
# ═══════════════════════════════════════════════════════════════════════════

_CARD_OUTPUT_MODES = {"inline", "reply"}
_CARD_DETAIL_LEVELS = {"full", "compact"}
_CARD_MODULE_MODES = {"off", "on"}

_CARD_DISPLAY_DEFAULTS = {
    "card_module_mode": "off",
    "card_output_mode": "inline",
    "enable_smart_spoiler": True,
    "card_detail_level": "full",
    "enable_in_single_modes": False,
    "show_balances_breakdown": True,
    "show_transfer_tips": True,
    "cold_card_limit": 2000.0,
}


@router.get("/user/card-display")
async def get_card_display(
    telegram_id: Optional[int] = None,
    session_user_id: Optional[int] = Depends(optional_session),
):
    telegram_id = resolve_user_id(telegram_id, session_user_id)
    current = await _db().get_user_card_settings(telegram_id)
    return dict_to_camel(jsonable_encoder({**_CARD_DISPLAY_DEFAULTS, **(current or {})}))


class CardDisplayPayload(BaseModel):
    telegramId: Optional[int] = None
    cardModuleMode: Optional[str] = None
    cardOutputMode: Optional[str] = None
    enableSmartSpoiler: Optional[bool] = None
    cardDetailLevel: Optional[str] = None
    enableInSingleModes: Optional[bool] = None
    showBalancesBreakdown: Optional[bool] = None
    showTransferTips: Optional[bool] = None
    coldCardLimit: Optional[float] = Field(default=None, ge=0)


@router.post("/user/card-display")
async def update_card_display(
    payload: CardDisplayPayload,
    session_user_id: Optional[int] = Depends(optional_session),
):
    """
    Оновлює конфіг виводу карток.

    update_user_card_settings робить UPSERT усього рядка з дефолтами для
    відсутніх ключів, тож частковий payload без merge скидав би решту.
    """
    telegram_id = resolve_user_id(payload.telegramId, session_user_id)
    db = _db()

    patch = {
        "card_module_mode": payload.cardModuleMode,
        "card_output_mode": payload.cardOutputMode,
        "enable_smart_spoiler": payload.enableSmartSpoiler,
        "card_detail_level": payload.cardDetailLevel,
        "enable_in_single_modes": payload.enableInSingleModes,
        "show_balances_breakdown": payload.showBalancesBreakdown,
        "show_transfer_tips": payload.showTransferTips,
        "cold_card_limit": payload.coldCardLimit,
    }
    patch = {k: v for k, v in patch.items() if v is not None}

    for field, allowed in (
        ("card_module_mode", _CARD_MODULE_MODES),
        ("card_output_mode", _CARD_OUTPUT_MODES),
        ("card_detail_level", _CARD_DETAIL_LEVELS),
    ):
        if field in patch and patch[field] not in allowed:
            raise HTTPException(
                status_code=400,
                detail=f"{field}: очікується одне з {sorted(allowed)}",
            )

    if not patch:
        return {"status": "success", "updated": []}

    current = await db.get_user_card_settings(telegram_id) or {}
    merged = {**_CARD_DISPLAY_DEFAULTS, **current, **patch}
    await db.update_user_card_settings(telegram_id, merged)

    return {"status": "success", "updated": sorted(patch)}


# ═══════════════════════════════════════════════════════════════════════════
# Ліміти банків і карток
# ═══════════════════════════════════════════════════════════════════════════

# Набір, який приймає card_repo.set_user_bank_limit / update_card_limit_override.
# Тримаємо копію тут, щоб віддати 400 замість мовчазного ігнорування:
# репозиторій на невідоме поле просто робить return.
_LIMIT_FIELDS = {
    "daily_out_max", "daily_in_max", "monthly_out_max", "monthly_in_max",
    "max_single_tx_out", "max_single_tx_in", "max_tx_per_day", "cooldown_hours",
}


@router.get("/user/bank-limits")
async def get_bank_limits(
    telegram_id: Optional[int] = None,
    session_user_id: Optional[int] = Depends(optional_session),
):
    """Глобальні ліміти по банках. Картка може мати власний override."""
    telegram_id = resolve_user_id(telegram_id, session_user_id)
    db = _db()

    async with db._db.execute(
        "SELECT * FROM user_bank_limits WHERE user_id = ? ORDER BY bank_name",
        (telegram_id,),
    ) as cur:
        rows = await cur.fetchall()

    return dict_to_camel(jsonable_encoder([dict(r) for r in rows]))


class BankLimitPayload(BaseModel):
    telegramId: Optional[int] = None
    bankName: str
    # {"daily_out_max": 100000, …} — ключі з _LIMIT_FIELDS.
    limits: dict[str, float]


@router.post("/user/bank-limits")
async def set_bank_limits(
    payload: BankLimitPayload,
    session_user_id: Optional[int] = Depends(optional_session),
):
    telegram_id = resolve_user_id(payload.telegramId, session_user_id)
    db = _db()

    unknown = sorted(set(payload.limits) - _LIMIT_FIELDS)
    if unknown:
        raise HTTPException(status_code=400, detail=f"Невідомі поля лімітів: {unknown}")
    if not payload.limits:
        raise HTTPException(status_code=400, detail="Порожній набір лімітів")

    for field, value in payload.limits.items():
        await db.set_user_bank_limit(telegram_id, payload.bankName, field, float(value))

    return {"status": "success", "bank": payload.bankName, "updated": sorted(payload.limits)}


class CardLimitPayload(BaseModel):
    limits: dict[str, float]


@router.post("/cards/{card_id}/limits")
async def set_card_limits(
    card_id: str,
    payload: CardLimitPayload,
    telegram_id: int = Depends(require_session),
):
    """Локальні override-и лімітів картки. Мають пріоритет над банківськими."""
    await _owned_card(card_id, telegram_id)

    unknown = sorted(set(payload.limits) - _LIMIT_FIELDS)
    if unknown:
        raise HTTPException(status_code=400, detail=f"Невідомі поля лімітів: {unknown}")
    if not payload.limits:
        raise HTTPException(status_code=400, detail="Порожній набір лімітів")

    db = _db()
    for field, value in payload.limits.items():
        await db.update_card_limit_override(card_id, field, float(value))

    return {"status": "success", "updated": sorted(payload.limits)}


class CustomLimitsPayload(BaseModel):
    enabled: bool


@router.post("/cards/{card_id}/custom-limits")
async def toggle_custom_limits(
    card_id: str,
    payload: CustomLimitsPayload,
    telegram_id: int = Depends(require_session),
):
    """Вимкнений тумблер повертає картку на загальні ліміти банку."""
    await _owned_card(card_id, telegram_id)
    await _db().toggle_card_custom_limits(card_id, payload.enabled)
    return {"status": "success", "enabled": payload.enabled}


# ═══════════════════════════════════════════════════════════════════════════
# Monobank-трекер
# ═══════════════════════════════════════════════════════════════════════════

_TRACKER_MODES = {"ALL", "INCOME"}
_TRACKER_FIELDS = {"amount", "sender", "comment", "time", "card", "balance", "p2p"}


@router.get("/cards/{card_id}/mono-tracker")
async def get_mono_tracker(card_id: str, telegram_id: int = Depends(require_session)):
    """Налаштування сповіщень Monobank для картки."""
    import json

    await _owned_card(card_id, telegram_id)
    settings = await _db().get_card_mono_settings(card_id) or {}

    raw_fields = settings.get("tracker_fields") or "{}"
    try:
        fields = json.loads(raw_fields) if isinstance(raw_fields, str) else raw_fields
    except (json.JSONDecodeError, TypeError):
        fields = {}

    return {
        "enabled": bool(int(settings.get("tracker_enabled", 1) or 0)),
        "mode": settings.get("tracker_mode", "INCOME"),
        "fields": {name: bool(fields.get(name, 1)) for name in sorted(_TRACKER_FIELDS)},
        # Сам токен і секрет вебхука назовні не віддаємо — лише факт наявності.
        "hasToken": bool(settings.get("x_token_encrypted")),
        "hasWebhook": bool(settings.get("webhook_secret")),
    }


class MonoTrackerPayload(BaseModel):
    enabled: Optional[bool] = None
    mode: Optional[str] = None
    fields: Optional[dict[str, bool]] = None


@router.post("/cards/{card_id}/mono-tracker")
async def update_mono_tracker(
    card_id: str,
    payload: MonoTrackerPayload,
    telegram_id: int = Depends(require_session),
):
    await _owned_card(card_id, telegram_id)

    if payload.mode and payload.mode not in _TRACKER_MODES:
        raise HTTPException(
            status_code=400, detail=f"mode: очікується одне з {sorted(_TRACKER_MODES)}"
        )
    if payload.fields:
        unknown = sorted(set(payload.fields) - _TRACKER_FIELDS)
        if unknown:
            raise HTTPException(status_code=400, detail=f"Невідомі поля: {unknown}")

    await _db().update_mono_tracker_config(
        card_id,
        enabled=None if payload.enabled is None else int(payload.enabled),
        mode=payload.mode,
        fields={k: int(v) for k, v in payload.fields.items()} if payload.fields else None,
    )
    return {"status": "success"}


# ═══════════════════════════════════════════════════════════════════════════
# Звіти по картках
# ═══════════════════════════════════════════════════════════════════════════

@router.get("/cards/report")
async def get_cards_report(telegram_id: int = Depends(require_session)):
    """Зведення по всіх картках користувача — обіг, кількість транзакцій."""
    db = _db()
    cards = await db.get_cards(owner_id=telegram_id)

    report = []
    for card in cards:
        stats = await db.get_card_report_stats(card["id"])
        report.append({
            "cardId": card["id"],
            "label": card.get("label") or "",
            "bankName": card.get("bank_name") or "",
            "lastFour": card.get("last_four") or "",
            "balance": card.get("balance", 0.0),
            "status": card.get("status") or "",
            **dict_to_camel(jsonable_encoder(stats or {})),
        })

    return report


# ═══════════════════════════════════════════════════════════════════════════
# Адміністрування користувачів
# ═══════════════════════════════════════════════════════════════════════════

@router.get("/admin/users")
async def list_users(_: int = Depends(require_admin)):
    db = _db()
    async with db._db.execute(
        """SELECT user_id, telegram_chat_id, is_active,
                  COALESCE(is_alerts_active, 1) AS is_alerts_active,
                  COALESCE(scanner_mode, 'SPREAD') AS scanner_mode,
                  working_capital, min_spread_pct, created_at
           FROM scanner_users
           ORDER BY created_at DESC"""
    ) as cur:
        rows = await cur.fetchall()

    return dict_to_camel(jsonable_encoder([dict(r) for r in rows]))


class UserStatePayload(BaseModel):
    isActive: Optional[bool] = None
    isAlertsActive: Optional[bool] = None


@router.post("/admin/users/{user_id}")
async def update_user_state(
    user_id: int,
    payload: UserStatePayload,
    admin_id: int = Depends(require_admin),
):
    """Вмикає/вимикає підписника або його алерти."""
    updates: dict[str, Any] = {}
    if payload.isActive is not None:
        updates["is_active"] = int(payload.isActive)
    if payload.isAlertsActive is not None:
        updates["is_alerts_active"] = int(payload.isAlertsActive)

    if not updates:
        raise HTTPException(status_code=400, detail="Немає полів для оновлення")

    # Адмін, що вимикає сам себе, лишиться без алертів і без очевидного
    # способу це помітити — краще спитати ще раз у явному вигляді.
    if user_id == admin_id and updates.get("is_active") == 0:
        raise HTTPException(
            status_code=400,
            detail="Не можна деактивувати власний акаунт через цей ендпоінт",
        )

    db = _db()
    assignments = ", ".join(f"{col} = ?" for col in updates)
    cursor = await db._db.execute(
        f"UPDATE scanner_users SET {assignments} WHERE user_id = ?",
        (*updates.values(), user_id),
    )
    await db._db.commit()

    if cursor.rowcount == 0:
        raise HTTPException(status_code=404, detail="User not found")

    return {"status": "success", "updated": sorted(updates)}
