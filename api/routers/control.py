# api/routers/control.py
"""
Керування ботом із веб-дашборду: фільтри, ядро сканера, картки, моніторинг.

Ці домени раніше жили тільки в Telegram-меню. Дашборд бачив статистику й
спреди, але не міг ані змінити фільтри користувача, ані подивитись стан
сесій, ані зупинити ядро — усе це доводилось робити в боті.

Джерела правди не дублюються: фільтри пишуться в scanner_users, стан ядра —
в bot_settings через runtime_config, біржі — через ExchangeManager (щоб
оновився і in-memory стан, а не лише запис у БД).
"""
from __future__ import annotations

import logging
import time
from typing import Any, Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.encoders import jsonable_encoder
from pydantic import BaseModel, Field

from api.auth import optional_session, require_admin, resolve_user_id
from api.security import require_api_key
from api.utils import dict_to_camel, dict_to_snake

router = APIRouter(prefix="/api/v1", tags=["Control"], dependencies=[Depends(require_api_key)])
logger = logging.getLogger("ApiControl")


def _db():
    from bot.handlers.core import _db as db
    if db is None:
        raise HTTPException(status_code=503, detail="База даних ще не піднялась")
    return db


# ═══════════════════════════════════════════════════════════════════════════
# Фільтри користувача
# ═══════════════════════════════════════════════════════════════════════════

# Колонки scanner_users, які дозволено правити з дашборду. Усе, чого тут
# немає, ендпоінт мовчки не чіпає — так само як бот не дає редагувати
# службові поля через меню.
_EDITABLE_FILTERS: dict[str, type] = {
    "working_capital": float,
    "capital_mode": str,
    "min_amount_uah": float,
    "min_spread_pct": float,
    "max_spread_pct": float,
    "spread_strategy": str,
    "bank_codes": str,
    "buy_bank_codes": str,
    "sell_bank_codes": str,
    "scanner_mode": str,
    # Набір активних режимів через кому. Приймає і список, і рядок —
    # str-caster нижче зводить обидва до CSV.
    "scanner_modes": str,
    "is_alerts_active": int,
    "target_margin": float,
    "maker_buy_price": float,
    # ── TAKER SELL ────────────────────────────────────────────────────────
    "taker_sell_amount": float,
    "taker_sell_price": float,
    "taker_sell_exchange": str,
    "taker_sell_profit": float,
    "taker_sell_min_price": float,
    "taker_sell_speed": str,
    "taker_sell_price_strategy": str,
    "taker_sell_price_to": float,
    # ── TAKER BUY ─────────────────────────────────────────────────────────
    "taker_buy_amount": float,
    "taker_buy_max_price": float,
    "taker_buy_limit_min": float,
    "taker_buy_limit_max": float,
    "taker_buy_speed": str,
    "taker_buy_price_strategy": str,
    "taker_buy_price_from": float,
    # ── Баланс під купівлю ────────────────────────────────────────────────
    "buy_balance_mode": str,
    "buy_auto_scale_down": int,
    "buy_auto_scale_up": int,
}

# Порядок імпортуємо, щоб набір режимів записувався однаково і тут, і в
# UserRepo — інакше "TAKER_BUY,SPREAD" і "SPREAD,TAKER_BUY" були б різними
# рядками з однаковим змістом.
from core.storage.user_repo import SCANNER_MODES as _SCANNER_MODE_ORDER

_SCANNER_MODES = set(_SCANNER_MODE_ORDER)
_SPREAD_STRATEGIES = {"min", "max", "range"}
_CAPITAL_MODES = {"manual", "auto"}
# Значення читає core/engine/taker_scanner.py — розходження тут означає
# мовчазний фільтр, який ніколи не спрацює.
_SPEEDS = {"FAST", "ANY"}
_SELL_PRICE_STRATEGIES = {"roi", "min", "range", "exact", "any"}
_BUY_PRICE_STRATEGIES = {"any", "max", "range", "exact"}
_BUY_BALANCE_MODES = {"CARD_ENFORCED", "AUTO_SCALE", "FREE"}


# Фільтри поділені на три групи, які можна читати незалежно.
#
# Раніше все віддавалось одним GET /user/filters, і це впиралось у стелю на
# фронтенді: синхронізація не могла оновлювати пресети тейкера окремо від
# спредових порогів, бо це фізично одна відповідь. Групи не перетинаються,
# тож кожну можна тягнути, кешувати й перечитувати сама по собі.
#
# Повний ендпоінт лишається: він зручний, коли треба все одразу, і на нього
# спирається наявний фронтенд.

_CORE_FILTER_KEYS = (
    "user_id", "capital", "capital_mode", "min_amount", "min_spread", "max_spread",
    "spread_strategy", "bank_codes", "buy_bank_codes", "sell_bank_codes",
    "scanner_mode", "scanner_modes", "is_alerts_active",
)

_TAKER_FILTER_KEYS = (
    "user_id", "scanner_mode", "scanner_modes",
    "taker_sell_amount", "taker_sell_price", "taker_sell_exchange",
    "taker_sell_profit", "taker_sell_min_price", "taker_sell_speed",
    "taker_sell_price_strategy", "taker_sell_price_to",
    "taker_buy_amount", "taker_buy_max_price", "taker_buy_limit_min",
    "taker_buy_limit_max", "taker_buy_speed", "taker_buy_price_strategy",
    "taker_buy_price_from",
    "buy_balance_mode", "buy_auto_scale_down", "buy_auto_scale_up",
    "target_margin", "maker_buy_price",
)


async def _user_or_404(telegram_id: int) -> dict:
    user = await _db().get_user_by_id(telegram_id)
    if not user:
        raise HTTPException(status_code=404, detail="User not found")
    return user


@router.get("/user/filters")
async def get_user_filters(
    telegram_id: Optional[int] = None,
    session_user_id: Optional[int] = Depends(optional_session),
):
    """Повний набір персональних фільтрів — те саме, що показує меню «Фільтри»."""
    telegram_id = resolve_user_id(telegram_id, session_user_id)
    user = await _user_or_404(telegram_id)
    return dict_to_camel(jsonable_encoder(user))


@router.get("/user/filters/core")
async def get_core_filters(
    telegram_id: Optional[int] = None,
    session_user_id: Optional[int] = Depends(optional_session),
):
    """Капітал, спред, банки, режим сканера — без пресетів тейкера."""
    telegram_id = resolve_user_id(telegram_id, session_user_id)
    user = await _user_or_404(telegram_id)
    return dict_to_camel(jsonable_encoder(
        {k: user.get(k) for k in _CORE_FILTER_KEYS if k in user}
    ))


@router.get("/user/filters/taker")
async def get_taker_filters(
    telegram_id: Optional[int] = None,
    session_user_id: Optional[int] = Depends(optional_session),
):
    """Пресети TAKER_BUY / TAKER_SELL і параметри maker-ціни."""
    telegram_id = resolve_user_id(telegram_id, session_user_id)
    user = await _user_or_404(telegram_id)
    return dict_to_camel(jsonable_encoder(
        {k: user.get(k) for k in _TAKER_FILTER_KEYS if k in user}
    ))


class FiltersPayload(BaseModel):
    telegramId: Optional[int] = None
    # Решта полів — довільні camelCase ключі з _EDITABLE_FILTERS.
    model_config = {"extra": "allow"}


@router.post("/user/filters")
async def update_user_filters(
    payload: FiltersPayload,
    session_user_id: Optional[int] = Depends(optional_session),
):
    """
    Оновлює тільки передані поля.

    Свідомо не робимо UPDATE усіх колонок: відсутній ключ у частковому
    payload перетворився б на NULL і затер би налаштування, які юзер
    виставляв у боті.
    """
    db = _db()
    telegram_id = resolve_user_id(payload.telegramId, session_user_id)
    raw = dict_to_snake(payload.model_dump(exclude={"telegramId"}))

    updates: dict[str, Any] = {}
    rejected: list[str] = []

    for key, value in raw.items():
        caster = _EDITABLE_FILTERS.get(key)
        if caster is None or value is None:
            rejected.append(key)
            continue
        try:
            if key == "is_alerts_active":
                updates[key] = 1 if value in (True, 1, "1", "true", "True") else 0
            elif caster is str:
                text = ",".join(str(v) for v in value) if isinstance(value, list) else str(value)
                updates[key] = text
            else:
                updates[key] = caster(value)
        except (TypeError, ValueError):
            rejected.append(key)

    # Значення з фіксованим набором варіантів звіряємо тут, а не покладаємось
    # на те, що фронтенд надішле щось осмислене.
    for field, allowed in (
        ("scanner_mode", _SCANNER_MODES),
        ("spread_strategy", _SPREAD_STRATEGIES),
        ("capital_mode", _CAPITAL_MODES),
        ("taker_sell_speed", _SPEEDS),
        ("taker_buy_speed", _SPEEDS),
        ("taker_sell_price_strategy", _SELL_PRICE_STRATEGIES),
        ("taker_buy_price_strategy", _BUY_PRICE_STRATEGIES),
        ("buy_balance_mode", _BUY_BALANCE_MODES),
    ):
        if field in updates and updates[field] not in allowed:
            raise HTTPException(
                status_code=400,
                detail=f"{field}: очікується одне з {sorted(allowed)}, отримано {updates[field]!r}",
            )

    # Набір режимів. Порожній не приймаємо: користувач без жодного режиму
    # нічого не отримує і виглядає як зламаний, а не як «свідомо вимкнений» —
    # для тиші є is_alerts_active.
    if "scanner_modes" in updates:
        picked = [m.strip().upper() for m in str(updates["scanner_modes"]).split(",") if m.strip()]
        unknown = sorted(set(picked) - _SCANNER_MODES)
        if unknown:
            raise HTTPException(
                status_code=400,
                detail=f"scanner_modes: невідомі режими {unknown}; доступні {sorted(_SCANNER_MODES)}",
            )
        if not picked:
            raise HTTPException(
                status_code=400,
                detail="scanner_modes: потрібен хоча б один режим",
            )

        ordered = [m for m in _SCANNER_MODE_ORDER if m in set(picked)]
        updates["scanner_modes"] = ",".join(ordered)
        # scanner_mode лишається «основним» — його показує меню бота і на
        # нього падають старі рядки. Тримаємо їх узгодженими, інакше меню
        # показувало б режим, якого в наборі вже немає.
        if updates.get("scanner_mode") not in ordered:
            updates["scanner_mode"] = ordered[0]

    if not updates:
        if rejected:
            raise HTTPException(status_code=400, detail=f"Немає полів для оновлення: {sorted(rejected)}")
        return {"status": "success", "updated": [], "rejected": []}

    assignments = ", ".join(f"{col} = ?" for col in updates)
    cursor = await db._db.execute(
        f"UPDATE scanner_users SET {assignments} WHERE user_id = ?",
        (*updates.values(), telegram_id),
    )
    await db._db.commit()

    if cursor.rowcount == 0:
        raise HTTPException(status_code=404, detail="User not found")

    return {"status": "success", "updated": sorted(updates), "rejected": sorted(rejected)}


# ── Банки окремо для режиму ───────────────────────────────────────────────
#
# Базові списки (bank_codes / buy_bank_codes / sell_bank_codes) лишаються
# спільними і працюють у всіх режимах. Тут — лише винятки: {"TAKER_BUY":
# {"buy": ["43"]}}. Порожньо = режим бере спільні, тобто поведінка як була.


class ModeBankOverridePayload(BaseModel):
    mode: str
    side: str
    # Порожній список знімає перевизначення.
    banks: list[str] = []


@router.get("/user/bank-scopes")
async def get_bank_scopes(
    telegram_id: Optional[int] = None,
    session_user_id: Optional[int] = Depends(optional_session),
):
    """Спільні списки + перевизначення по режимах, разом із тим, що вийде."""
    from core.engine.bank_scope import base_banks, has_override, resolve_banks

    telegram_id = resolve_user_id(telegram_id, session_user_id)
    user = await _user_or_404(telegram_id)

    # Без dict_to_camel: назви режимів — це константи ("TAKER_BUY"), а не
    # імена полів. Камелізація перетворила б їх на takerBuy і зробила б
    # відповідь неспівставною зі значеннями, які приймає POST.
    resolved = {
        mode: {
            side: {
                "banks": resolve_banks(user, mode, side),
                "isOverride": has_override(user, mode, side),
            }
            for side in ("buy", "sell")
        }
        for mode in _SCANNER_MODE_ORDER
    }

    return jsonable_encoder({
        "base": {"buy": base_banks(user, "buy"), "sell": base_banks(user, "sell")},
        "overrides": user.get("mode_bank_overrides") or {},
        "resolved": resolved,
    })


@router.post("/user/bank-scopes")
async def set_bank_scope(
    payload: ModeBankOverridePayload,
    telegram_id: Optional[int] = None,
    session_user_id: Optional[int] = Depends(optional_session),
):
    import json

    from core.engine.bank_scope import SIDES, normalize_overrides

    telegram_id = resolve_user_id(telegram_id, session_user_id)
    mode = payload.mode.upper()

    if mode not in _SCANNER_MODES:
        raise HTTPException(
            status_code=400,
            detail=f"mode: очікується одне з {sorted(_SCANNER_MODES)}",
        )
    if payload.side not in SIDES:
        raise HTTPException(status_code=400, detail=f"side: очікується одне з {list(SIDES)}")

    db = _db()
    user = await _user_or_404(telegram_id)

    current = dict(user.get("mode_bank_overrides") or {})
    per_mode = dict(current.get(mode) or {})
    banks = [b.strip() for b in payload.banks if b.strip()]
    if banks:
        per_mode[payload.side] = banks
    else:
        per_mode.pop(payload.side, None)
    current[mode] = per_mode

    cleaned = normalize_overrides(current, _SCANNER_MODE_ORDER)
    await db._db.execute(
        "UPDATE scanner_users SET mode_bank_overrides_json = ? WHERE user_id = ?",
        (json.dumps(cleaned), telegram_id),
    )
    await db._db.commit()

    return {
        "status": "success",
        "mode": mode,
        "side": payload.side,
        # Порожній список у відповіді означає «повернулись до спільних».
        "banks": banks,
        # Ключі — назви режимів, тому без камелізації (див. GET вище).
        "overrides": cleaned,
    }


# ── Ціновий фільтр входу (спред-режим) ────────────────────────────────────
#
# Зберігається як JSON у scanner_users.price_range_json і застосовується в
# alert_dispatcher до ціни купівлі. Окремим ендпоінтом, а не полем у
# /user/filters, бо це структура, а не скаляр: у решти редагованих полів
# рівно одне значення.

_PRICE_RANGE_MODES = {"range", "exact", "max", "min"}


class PriceRangePayload(BaseModel):
    """Порожній mode вимикає фільтр."""
    mode: str = ""
    min: float = 0.0
    max: float = 0.0
    value: float = 0.0


@router.get("/user/price-range")
async def get_price_range(
    telegram_id: Optional[int] = None,
    session_user_id: Optional[int] = Depends(optional_session),
):
    telegram_id = resolve_user_id(telegram_id, session_user_id)
    user = await _user_or_404(telegram_id)
    return dict_to_camel(jsonable_encoder(user.get("price_range") or {}))


@router.post("/user/price-range")
async def set_price_range(
    payload: PriceRangePayload,
    telegram_id: Optional[int] = None,
    session_user_id: Optional[int] = Depends(optional_session),
):
    import json

    telegram_id = resolve_user_id(telegram_id, session_user_id)
    db = _db()

    mode = (payload.mode or "").strip().lower()
    if mode and mode not in _PRICE_RANGE_MODES:
        raise HTTPException(
            status_code=400,
            detail=f"mode: очікується одне з {sorted(_PRICE_RANGE_MODES)} або порожнє",
        )

    if not mode:
        config: dict[str, Any] = {}
    elif mode == "range":
        if payload.min <= 0 or payload.max <= 0:
            raise HTTPException(status_code=400, detail="range: потрібні додатні min і max")
        if payload.min >= payload.max:
            raise HTTPException(status_code=400, detail="range: min має бути меншим за max")
        config = {"mode": "range", "min": payload.min, "max": payload.max}
    else:
        if payload.value <= 0:
            raise HTTPException(status_code=400, detail=f"{mode}: потрібне додатне value")
        config = {"mode": mode, "value": payload.value}

    cursor = await db._db.execute(
        "UPDATE scanner_users SET price_range_json = ? WHERE user_id = ?",
        (json.dumps(config), telegram_id),
    )
    await db._db.commit()
    if cursor.rowcount == 0:
        raise HTTPException(status_code=404, detail="User not found")

    return {"status": "success", "priceRange": dict_to_camel(config)}


@router.get("/user/merchant-filters")
async def get_merchant_filters(
    telegram_id: Optional[int] = None,
    session_user_id: Optional[int] = Depends(optional_session),
):
    """Пороги по мерчантах: загальні та перевизначені по біржах."""
    telegram_id = resolve_user_id(telegram_id, session_user_id)
    user = await _db().get_user_by_id(telegram_id)
    if not user:
        raise HTTPException(status_code=404, detail="User not found")
    return dict_to_camel(jsonable_encoder({
        "merchant_filters": user.get("merchant_filters", {}),
        "exchange_merchant_filters": user.get("exchange_merchant_filters", {}),
    }))


# Пороги мерчанта. Ключі — рівно ті, що читають alert_dispatcher і
# taker_scanner; per-exchange правило перебиває загальне (ex_filters or mf).
_VERIFIED_FILTERS = {"all", "verified", "unverified"}
_BLACKLIST_MODES = {"block", "hide", "show"}


class MerchantFiltersPayload(BaseModel):
    telegramId: Optional[int] = None
    exchange: Optional[str] = None

    minOrders: Optional[int] = Field(default=None, ge=0)
    minRate: Optional[float] = Field(default=None, ge=0, le=100)
    # "all" | "verified" | "unverified"
    verifiedFilter: Optional[str] = None
    minAccountAgeDays: Optional[int] = Field(default=None, ge=0)
    minPositiveRate: Optional[float] = Field(default=None, ge=0, le=100)
    maxOfflineMins: Optional[int] = Field(default=None, ge=0)
    # "block" | "hide" | "show" — що робити з мерчантами в чорному списку
    blacklistMode: Optional[str] = None


@router.post("/user/merchant-filters")
async def update_merchant_filters(
    payload: MerchantFiltersPayload,
    session_user_id: Optional[int] = Depends(optional_session),
):
    """
    Пише пороги мерчанта. Якщо переданий exchange — правило кладеться в
    exchange_merchant_filters_json і перебиває загальне для цієї біржі.
    """
    import json

    db = _db()
    telegram_id = resolve_user_id(payload.telegramId, session_user_id)
    user = await db.get_user_by_id(telegram_id)
    if not user:
        raise HTTPException(status_code=404, detail="User not found")

    if payload.verifiedFilter and payload.verifiedFilter not in _VERIFIED_FILTERS:
        raise HTTPException(
            status_code=400,
            detail=f"verifiedFilter: очікується одне з {sorted(_VERIFIED_FILTERS)}",
        )
    if payload.blacklistMode and payload.blacklistMode not in _BLACKLIST_MODES:
        raise HTTPException(
            status_code=400,
            detail=f"blacklistMode: очікується одне з {sorted(_BLACKLIST_MODES)}",
        )

    # camelCase поля → ключі, під якими їх шукає движок.
    incoming = {
        "min_orders": payload.minOrders,
        "min_rate": payload.minRate,
        "verified_filter": payload.verifiedFilter,
        "min_account_age_days": payload.minAccountAgeDays,
        "min_positive_rate": payload.minPositiveRate,
        "max_offline_mins": payload.maxOfflineMins,
        "blacklist_mode": payload.blacklistMode,
    }
    changes = {k: v for k, v in incoming.items() if v is not None}

    if not changes:
        raise HTTPException(status_code=400, detail="Потрібне хоча б одне поле порогів")

    if payload.exchange:
        per_exchange = dict(user.get("exchange_merchant_filters") or {})
        rule = dict(per_exchange.get(payload.exchange) or {})
        rule.update(changes)
        per_exchange[payload.exchange] = rule
        column, value = "exchange_merchant_filters_json", json.dumps(per_exchange)
    else:
        general = dict(user.get("merchant_filters") or {})
        general.update(changes)
        column, value = "merchant_filters_json", json.dumps(general)

    await db._db.execute(
        f"UPDATE scanner_users SET {column} = ? WHERE user_id = ?",
        (value, telegram_id),
    )
    await db._db.commit()
    return {
        "status": "success",
        "scope": payload.exchange or "global",
        "updated": sorted(changes),
    }


# ═══════════════════════════════════════════════════════════════════════════
# Налаштування виводу повідомлень
# ═══════════════════════════════════════════════════════════════════════════

# Прапорці меню «Вивід» у боті. Тримаємо їх у явному списку, бо
# update_user_display_settings перезаписує ВСІ колонки одразу: якщо
# віддати їй частковий словник, решта тихо повернеться до дефолтів.
_DISPLAY_FLAGS = (
    "show_ai_terms_summary",
    "show_full_terms",
    "show_ai_logic",
    "show_bank_details",
    "show_llm_summary",
    "is_hybrid_routes_enabled",
    "group_active_alerts",
    "group_scanner_alerts",
)
_FILTER_MODES = {"hide", "show", "only"}
_PROFILE_MODES = {"chat", "profile"}


@router.get("/user/display")
async def get_display_settings(
    telegram_id: Optional[int] = None,
    session_user_id: Optional[int] = Depends(optional_session),
):
    """Налаштування виводу алертів — те саме, що меню «Вивід» у боті."""
    telegram_id = resolve_user_id(telegram_id, session_user_id)
    db = _db()

    user = await db.get_user_by_id(telegram_id)
    if not user:
        raise HTTPException(status_code=404, detail="User not found")

    # Налаштування виводу читаються за telegram_chat_id, а не за user_id.
    chat_id = user.get("chat_id") or telegram_id
    return dict_to_camel(jsonable_encoder(await db.get_user_display_settings(chat_id)))


class DisplayPayload(BaseModel):
    telegramId: Optional[int] = None
    showAiTermsSummary: Optional[bool] = None
    showFullTerms: Optional[bool] = None
    showAiLogic: Optional[bool] = None
    showBankDetails: Optional[bool] = None
    showLlmSummary: Optional[bool] = None
    isHybridRoutesEnabled: Optional[bool] = None
    groupActiveAlerts: Optional[bool] = None
    groupScannerAlerts: Optional[bool] = None
    # -1 = використати авто-кулдаун, інакше пауза між алертами в секундах.
    alertCooldown: Optional[float] = Field(default=None, ge=-1, le=3600)
    filterFopTov: Optional[str] = None
    filterBankaJar: Optional[str] = None
    cryptobotProfileMode: Optional[str] = None


@router.post("/user/display")
async def update_display_settings(
    payload: DisplayPayload,
    session_user_id: Optional[int] = Depends(optional_session),
):
    """
    Пише налаштування виводу.

    Часткові зміни зливаємо з поточними перед записом: репозиторій оновлює
    всі колонки одним UPDATE, тож без merge вимкнення одного прапорця
    скидало б решту до дефолтів.
    """
    telegram_id = resolve_user_id(payload.telegramId, session_user_id)
    db = _db()

    user = await db.get_user_by_id(telegram_id)
    if not user:
        raise HTTPException(status_code=404, detail="User not found")
    chat_id = user.get("chat_id") or telegram_id

    patch = dict_to_snake(payload.model_dump(exclude={"telegramId"}, exclude_none=True))

    for field, allowed in (
        ("filter_fop_tov", _FILTER_MODES),
        ("filter_banka_jar", _FILTER_MODES),
        ("cryptobot_profile_mode", _PROFILE_MODES),
    ):
        if field in patch and patch[field] not in allowed:
            raise HTTPException(
                status_code=400,
                detail=f"{field}: очікується одне з {sorted(allowed)}, отримано {patch[field]!r}",
            )

    current = await db.get_user_display_settings(chat_id)
    merged = {**current, **patch}
    await db.update_user_display_settings(chat_id, merged)

    return {"status": "success", "updated": sorted(patch)}


class AutoCooldownPayload(BaseModel):
    telegramId: Optional[int] = None
    windowSeconds: float = Field(default=5.0, gt=0, le=600)
    # [{"threshold": 10, "delay": 0.0}, …] — чим більше алертів у вікні,
    # тим більша пауза між ними.
    tiers: list[dict]


@router.post("/user/display/auto-cooldown")
async def update_auto_cooldown(
    payload: AutoCooldownPayload,
    session_user_id: Optional[int] = Depends(optional_session),
):
    """Драбинка авто-кулдауну: скільки чекати між алертами при напливі."""
    telegram_id = resolve_user_id(payload.telegramId, session_user_id)
    db = _db()

    user = await db.get_user_by_id(telegram_id)
    if not user:
        raise HTTPException(status_code=404, detail="User not found")

    tiers = []
    for tier in payload.tiers:
        try:
            tiers.append({
                "threshold": int(tier["threshold"]),
                "delay": float(tier["delay"]),
            })
        except (KeyError, TypeError, ValueError):
            raise HTTPException(
                status_code=400,
                detail="Кожен рівень має бути {'threshold': int, 'delay': float}",
            )

    if not tiers:
        raise HTTPException(status_code=400, detail="Потрібен хоча б один рівень")

    # Сканер іде по списку зверху вниз і бере перший рівень, поріг якого
    # ще не перевищено, — неупорядкований список зробив би частину рівнів
    # недосяжними.
    tiers.sort(key=lambda t: t["threshold"])

    chat_id = user.get("chat_id") or telegram_id
    await db.update_user_auto_cooldown_json(
        chat_id, {"window_seconds": payload.windowSeconds, "tiers": tiers}
    )
    return {"status": "success", "tiers": tiers}


@router.get("/banks")
async def get_banks():
    """Довідник банків — щоб фронтенд не тримав власну копію кодів."""
    from config.banks import BANK_NAMES
    return [{"code": code, "name": name} for code, name in sorted(BANK_NAMES.items())]


# ═══════════════════════════════════════════════════════════════════════════
# Керування ядром
# ═══════════════════════════════════════════════════════════════════════════

@router.get("/scanner/state")
async def get_scanner_state():
    """Стан ядра та пауза алертів в одному місці."""
    from config.runtime import runtime_config
    from bot.handlers import core

    mute_left = max(0.0, core._mute_until - time.monotonic())
    return {
        "isScannerActive": runtime_config.get("is_scanner_active", "false") == "true",
        "isMuted": core.is_muted(),
        "muteSecondsLeft": round(mute_left),
    }


# Ядро одне на всіх: зупинка через веб гасить сканування і алерти всім
# користувачам одразу. Меню в боті показує ці кнопки лише адміну
# (bot/keyboards/menu.py), HTTP до цього моменту не показував нікому — тобто
# пускав будь-кого.


@router.post("/scanner/start")
async def start_scanner(admin_id: int = Depends(require_admin)):
    from config.runtime import runtime_config
    if not await runtime_config.set("is_scanner_active", "true"):
        raise HTTPException(status_code=500, detail="Не вдалось записати стан у bot_settings")
    logger.info("▶️ Ядро сканера запущено через HTTP API (admin=%s)", admin_id)
    return {"status": "success", "isScannerActive": True}


@router.post("/scanner/stop")
async def stop_scanner(admin_id: int = Depends(require_admin)):
    from config.runtime import runtime_config
    if not await runtime_config.set("is_scanner_active", "false"):
        raise HTTPException(status_code=500, detail="Не вдалось записати стан у bot_settings")
    logger.info("⏸ Ядро сканера зупинено через HTTP API (admin=%s)", admin_id)
    return {"status": "success", "isScannerActive": False}


class MutePayload(BaseModel):
    # 0 = зняти паузу, >0 = пауза на N годин.
    hours: float = Field(default=1.0, ge=0, le=8760)


@router.post("/scanner/mute")
async def set_mute(payload: MutePayload, admin_id: int = Depends(require_admin)):
    """
    Пауза алертів. Стан процесний (bot.handlers.core._mute_until), тому
    працює лише поки живий цей інстанс — так само, як і з боку бота.

    Пауза глобальна: вона замовкає алерти всім. Персональний перемикач —
    це is_alerts_active у POST /user/filters, він доступний кожному.
    """
    from bot.handlers import core

    core._mute_until = 0.0 if payload.hours == 0 else time.monotonic() + payload.hours * 3600
    logger.info("🔕 Пауза алертів: %s год (admin=%s)", payload.hours, admin_id)
    return {"status": "success", "isMuted": core.is_muted(), "hours": payload.hours}


@router.post("/exchanges/{name}/enable")
async def enable_exchange(name: str, admin_id: int = Depends(require_admin)):
    from core.engine.exchange_manager import exchange_manager
    from config.runtime import runtime_config

    if not await exchange_manager.enable(name, runtime_config=runtime_config):
        raise HTTPException(status_code=404, detail=f"Невідома біржа: {name}")
    return {"status": "success", "exchange": name, "enabled": True}


class DisableExchangePayload(BaseModel):
    reason: str = "manual (web)"
    cooldownHours: float = Field(default=0, ge=0, le=720)


@router.post("/exchanges/health")
async def check_exchanges_health(admin_id: int = Depends(require_admin)):
    """
    Опитує всі біржі — те саме, що «🏥 Health check» у меню моніторингу.

    На сайті цього не було взагалі: побачити, що біржа мовчить, можна було
    лише за лічильником відмов, який росте вже після того, як цикли почали
    падати. Три спроби на біржу робить сам ExchangeManager, тому запит
    повільний — і викликається тільки руками, без polling.
    """
    import asyncio

    from core.engine.exchange_manager import ALL_EXCHANGES, exchange_manager

    results = await asyncio.gather(
        *(exchange_manager.health_check(name) for name in ALL_EXCHANGES),
        return_exceptions=True,
    )

    checked = []
    for name, result in zip(ALL_EXCHANGES, results):
        if isinstance(result, BaseException):
            checked.append({"exchange": name, "ok": False, "message": str(result)[:200]})
            continue
        ok, message = result
        checked.append({"exchange": name, "ok": bool(ok), "message": message})

    logger.info("🏥 Health check через HTTP API (admin=%s)", admin_id)
    return checked


@router.post("/exchanges/{name}/disable")
async def disable_exchange(
    name: str,
    payload: DisableExchangePayload,
    admin_id: int = Depends(require_admin),
):
    """cooldownHours=0 → вимкнено до ручного ввімкнення."""
    from core.engine.exchange_manager import exchange_manager
    from config.runtime import runtime_config

    ok = await exchange_manager.disable(
        name,
        reason=payload.reason,
        cooldown_hours=payload.cooldownHours,
        runtime_config=runtime_config,
    )
    if not ok:
        raise HTTPException(status_code=404, detail=f"Невідома біржа: {name}")
    return {"status": "success", "exchange": name, "enabled": False}


# ═══════════════════════════════════════════════════════════════════════════
# Тейкер-ордери
# ═══════════════════════════════════════════════════════════════════════════
#
# Досі веб бачив лише спред-зв'язки: `state.opportunities` наповнює
# SPREAD-гілка, а тейкер-шлях (core/engine/scanner_helpers.process_taker_path)
# віддає знайдене одразу в Telegram і більше нікуди. Тому в режимах
# TAKER_BUY / TAKER_SELL дашборд виглядав порожнім, хоча бот у цей самий час
# слав ордери.
#
# Тут ордери рахуються на запит із останнього зрізу циклу
# (state.last_buy_grouped / last_sell_grouped) тим самим TakerScanner і тими
# самими фільтрами. Два наслідки, обидва бажані:
#
#   * дедуп не застосовується. Він потрібен, щоб не слати те саме в чат
#     двічі; для списку на екрані «вже надіслане» — це рівно те, що треба
#     показувати;
#   * scanner_mode юзера не обмежує вибірку. Подивитись бік купівлі, сидячи
#     в режимі продажу, можна без перемикання режиму — а отже, і без зміни
#     того, що бот шле в Telegram.


def _app_link(order) -> str:
    """
    Посилання, яке на телефоні відкриє мерчанта просто в застосунку біржі.

    Те саме, що бот кладе в кнопку алерта (bot/deeplinks.py) — але досі це
    жило тільки в Telegram. На сайті лишалось `order.link`, тобто звичайна
    веб-сторінка: з телефона вона веде в мобільний браузер, де треба ще раз
    логінитись, замість застосунку з живою сесією.

    Порожній рядок означає, що підтвердженого маршруту для цієї біржі немає
    (перевірено прогоном на пристрої, див. tools/deeplink/FINDINGS.md) —
    фронтенд у такому разі показує лише звичайне посилання.
    """
    try:
        from bot.deeplinks import app_https_url, resolve_target

        kind, entity_id = resolve_target(order)
        return app_https_url(getattr(order, "exchange", ""), kind, entity_id) or ""
    except Exception as e:  # диплінк не має ламати видачу ордерів
        logger.debug("app_link: %s", e)
        return ""


def _order_to_dict(order) -> dict:
    """Ордер у вигляді, придатному для JSON. Decimal → float."""
    return {
        "appLink": _app_link(order),
        "id": order.id,
        "exchange": order.exchange,
        "price": float(order.price),
        "availableAmount": float(order.available_amount),
        "minLimit": float(order.min_limit),
        "maxLimit": float(order.max_limit),
        "merchantId": order.merchant_id,
        "merchantName": order.merchant_name,
        "monthOrderCount": order.month_order_count,
        "finishRatePct": order.finish_rate_pct,
        "positiveRate": order.positive_rate,
        "isVerified": order.is_verified,
        "accountAgeDays": order.account_age_days,
        "lastOnlineMins": order.last_online_mins,
        "bankCodes": list(order.bank_codes or []),
        "link": order.link,
        "riskFlag": order.risk_flag or "",
        "compositeScore": order.composite_score,
        "reviewScore": order.review_score,
        "reviewNegPct": order.review_neg_pct,
        "tradeTerms": order.trade_terms or "",
        "isNewUserSubsidy": order.is_new_user_subsidy,
        "side": order.side or "",
    }


@router.get("/taker/orders")
async def get_taker_orders(
    side: str = Query(default="both", description="buy | sell | both"),
    limit: int = Query(default=50, ge=1, le=200),
    telegram_id: Optional[int] = None,
    session_user_id: Optional[int] = Depends(optional_session),
):
    """
    Ордери, які проходять тейкер-фільтри користувача, — те саме, що бот шле
    в режимах TAKER_BUY / TAKER_SELL, але без дедупу й без прив'язки до
    поточного scanner_mode.
    """
    from core.engine.taker_scanner import TakerScanner
    from state import state

    if side not in {"buy", "sell", "both"}:
        raise HTTPException(status_code=400, detail="side: очікується buy, sell або both")

    telegram_id = resolve_user_id(telegram_id, session_user_id)
    user = await _user_or_404(telegram_id)

    buy_grouped = state.last_buy_grouped or {}
    sell_grouped = state.last_sell_grouped or {}
    if not buy_grouped and not sell_grouped:
        # Сканер ще не завершив жодного циклу — це не помилка, просто рано.
        return {"buy": [], "sell": [], "scanned": False}

    scanner = TakerScanner(_db())
    result: dict[str, Any] = {"buy": [], "sell": [], "scanned": True}

    wanted = ("buy", "sell") if side == "both" else (side,)
    for want in wanted:
        mode = "TAKER_BUY" if want == "buy" else "TAKER_SELL"
        try:
            orders = await scanner.find_orders_for_user(
                {**user, "scanner_mode": mode}, buy_grouped, sell_grouped
            )
        except Exception as e:
            logger.error("taker/orders %s: %s", mode, e)
            raise HTTPException(status_code=500, detail=f"{mode}: {e}") from e

        # TAKER_BUY шукає найдешевше, TAKER_SELL — найдорожче.
        orders.sort(key=lambda o: float(o.price), reverse=(mode == "TAKER_SELL"))
        result[want] = [_order_to_dict(o) for o in orders[:limit]]

    return result


# ═══════════════════════════════════════════════════════════════════════════
# Картки
# ═══════════════════════════════════════════════════════════════════════════

# Колонки, які не покидають бекенд. Зберігаються вони свідомо: повний номер
# потрібен, щоб звіряти надходження з випискою, а токен — щоб ходити в
# Monobank. Але у відповіді API їм робити нічого.
_CARD_PRIVATE_FIELDS = frozenset({
    "card_number",
    "mono_x_token_encrypted",
    "mono_webhook_secret",
    "mono_account_id",
})


@router.get("/cards")
async def get_cards(
    telegram_id: Optional[int] = None,
    bank: Optional[str] = None,
    status: Optional[str] = None,
    session_user_id: Optional[int] = Depends(optional_session),
):
    """
    Картки користувача разом з ефективними лімітами та вибіркою за добу
    й календарний місяць — те, що бот показує в меню «Картки».
    """
    telegram_id = resolve_user_id(telegram_id, session_user_id)
    db = _db()
    cards = await db.get_cards(owner_id=telegram_id, bank_name=bank, status=status)

    enriched = []
    for card in cards:
        card_id = card["id"]
        # Ліміти банку скидаються 1-го числа, тому місячну вибірку рахуємо
        # по календарному місяцю, а денну — ковзними 24 годинами.
        await db.lazy_monthly_reset(card_id)
        limits = await db.get_card_effective_limits(card_id, owner_id=telegram_id)

        # `SELECT *` тягне і те, чому нема місця у відповіді HTTP: повний
        # номер картки та секрети Monobank. Вони потрібні боту всередині,
        # але браузеру — ніколи: PAN лежав би у відповіді, в кеші SWR і в
        # девтулзах, а `has*` прапорців для UI цілком достатньо.
        payload = {k: v for k, v in dict(card).items() if k not in _CARD_PRIVATE_FIELDS}

        enriched.append({
            **payload,
            "hasCardNumber": bool(card.get("card_number")),
            "hasMonoToken": bool(card.get("mono_x_token_encrypted")),
            "limits": limits,
            "usedDaily": {
                "in": await db.get_rolling_used(card_id, "in", hours=24),
                "out": await db.get_rolling_used(card_id, "out", hours=24),
            },
            "usedMonthly": {
                "in": await db.get_monthly_used(card_id, "in"),
                "out": await db.get_monthly_used(card_id, "out"),
            },
        })

    return dict_to_camel(jsonable_encoder(enriched))


@router.get("/cards/{card_id}/transactions")
async def get_card_transactions(card_id: str, limit: int = Query(default=50, ge=1, le=500)):
    db = _db()
    async with db._db.execute(
        """SELECT id, card_id, amount, direction, type, source, timestamp,
                  linked_order_id
           FROM card_transactions
           WHERE card_id = ?
           ORDER BY timestamp DESC
           LIMIT ?""",
        (card_id, limit),
    ) as cur:
        rows = await cur.fetchall()
    return dict_to_camel(jsonable_encoder([dict(r) for r in rows]))


@router.get("/cards/{card_id}/stats")
async def get_card_stats(card_id: str):
    return dict_to_camel(jsonable_encoder(await _db().get_card_report_stats(card_id)))


# ═══════════════════════════════════════════════════════════════════════════
# Моніторинг
# ═══════════════════════════════════════════════════════════════════════════

# Скільки сесія вважається свіжою. Довші живуть, але вже підозріло.
_SESSION_FRESH_HOURS = 12.0


@router.get("/monitoring/sessions")
async def get_sessions(
    telegram_id: Optional[int] = None,
    session_user_id: Optional[int] = Depends(optional_session),
):
    """
    Свіжість перехоплених сесій бірж. Протухла сесія — головна причина,
    чому біржа раптом перестає віддавати дані, і побачити це в дашборді
    досі було ніяк.

    Сесії персональні: у кожного свої кукі бірж. Ендпоінт брав telegram_id
    просто з query — тобто показував, коли саме інший користувач востаннє
    логінився на біржу.
    """
    from core.engine.exchange_manager import ALL_EXCHANGES

    telegram_id = resolve_user_id(telegram_id, session_user_id)
    db = _db()
    now = time.time()
    result = []

    for exchange in ALL_EXCHANGES:
        try:
            _headers, cookies, updated_at = await db.get_auth_session(exchange, user_id=telegram_id)
        except Exception as e:
            logger.debug("get_auth_session %s: %s", exchange, e)
            _headers, cookies, updated_at = {}, {}, 0.0

        age_hours = (now - updated_at) / 3600 if updated_at else None
        result.append({
            "exchange": exchange,
            "hasSession": bool(cookies),
            "updatedAt": updated_at or None,
            "ageHours": round(age_hours, 1) if age_hours is not None else None,
            "isStale": age_hours is not None and age_hours > _SESSION_FRESH_HOURS,
        })

    return result


@router.get("/monitoring/orders")
async def get_monitoring_orders(
    statuses: str = Query(
        default="PENDING_PAYMENT,PAID_PENDING_RELEASE,SELL_PENDING",
        description="Статуси через кому",
    ),
):
    """Незавершені угоди — те, що order_monitor тримає під наглядом."""
    wanted = [s.strip() for s in statuses.split(",") if s.strip()]
    if not wanted:
        raise HTTPException(status_code=400, detail="Порожній список статусів")

    trades = await _db().get_active_trades_by_status(*wanted)
    return dict_to_camel(jsonable_encoder([dict(t) for t in trades]))


@router.get("/monitoring/queues")
async def get_queues():
    """Черги фонових воркерів — LLM та збір відгуків."""
    from state import state
    return {
        "llmQueue": state.stats.get("llm_queue", 0),
        "reviewQueue": state.stats.get("review_queue", 0),
        "cbStatus": state.stats.get("cb_status", {}),
        "internetConnected": state.stats.get("internet_connected", True),
    }
