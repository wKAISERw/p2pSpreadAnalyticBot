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
from config.banks import bank_display_name, bank_view_list, normalize_bank
from core.engine.terms_status import blind_label as blind_terms_label

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
    # Мережа, якою людина справді возить USDT між біржами. Порожнє —
    # найдешевша спільна, як було завжди.
    "preferred_network": str,
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
    "scanner_mode", "scanner_modes", "is_alerts_active", "preferred_network",
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


@router.get("/user/sync-state")
async def get_sync_state(
    telegram_id: Optional[int] = None,
    session_user_id: Optional[int] = Depends(optional_session),
):
    """
    Відбиток кожного розділу — щоб дашборд перечитував лише змінене.

    Відкрита вкладка опитувала всі десять розділів на кожному тіку: при
    інтервалі 10 секунд це 60 запитів на хвилину, з яких майже всі
    повертали ті самі дані, ще й перемальовуючи панелі. Тепер один дешевий
    запит каже, де саме сталась зміна.

    Значення — непрозорі рядки: порівнювати їх можна лише з попередніми,
    покладатись на вміст не можна. `null` означає «порахувати не вдалось»,
    і розділ треба перечитати звичайним шляхом.
    """
    from api.sync_state import build_sync_state

    telegram_id = resolve_user_id(telegram_id, session_user_id)
    await _user_or_404(telegram_id)
    return {"sections": await build_sync_state(_db(), telegram_id)}


@router.get("/banks")
async def get_banks():
    """Довідник банків — щоб фронтенд не тримав власну копію кодів."""
    from config.banks import BANK_NAMES
    return [{"code": code, "name": name} for code, name in sorted(BANK_NAMES.items())]


@router.get("/banks/profiles")
async def get_bank_profiles():
    """
    Операційні профілі банків: ліміти, комісії, нічні вікна, спільні ліцензії.

    Потрібні дашборду, щоб показувати, звідки взялись ефективні ліміти
    картки. Без цього поле «місячний ліміт 60 000» виглядає як магічне
    число, і незрозуміло, чи його задав користувач, чи довідник.

    Ключ — канонічний слаг (`normalize_bank`), а не код біржі: профілі є й
    для банків, яких біржі не знають (Таскомбанк, БВР), а їхні ліміти й
    спільна ліцензія на матчинг впливають.
    """
    from config.banks import (
        BANK_NAMES, BANK_PROFILES, UNLIMITED, bank_display_name, normalize_bank,
    )
    from config.card_limits import default_limits_for_bank

    # Слаги банків, які підтримує хоч одна біржа — решта доступна лише як
    # банк картки. Фронту це потрібно, щоб не пропонувати фільтр по банку,
    # якого в стакані не буде.
    tradable = {normalize_bank(code) for code in BANK_NAMES}

    result = []
    for slug, profile in sorted(BANK_PROFILES.items()):
        fee = profile.p2p_fee
        window = profile.night_window
        result.append({
            "slug": slug,
            "name": bank_display_name(slug),
            "tier": profile.tier,
            "tradable": slug in tradable,
            "safe_monthly_uah": profile.safe_monthly_uah,
            "max_monthly_uah": profile.max_monthly_uah,
            "safe_tx_per_day": profile.safe_tx_per_day,
            # UNLIMITED (-1) віддаємо як null: для фронта це «без стелі», і
            # -1 у полі суми він показав би як мінус тридцять тисяч.
            "single_tx_limit_uah": (
                None if profile.single_tx_limit_uah in (None, UNLIMITED)
                else profile.single_tx_limit_uah
            ),
            "business_days_only": profile.business_days_only,
            "license_group": profile.license_group,
            "termination_fee_pct": profile.termination_fee_pct,
            "third_party_friendly": profile.third_party_friendly,
            "note": profile.note,
            "p2p_fee": None if fee is None else {
                "pct": fee.pct,
                "fixed_uah": fee.fixed_uah,
                "free_until_uah": fee.free_until_uah,
                "free_tx_per_month": fee.free_tx_per_month,
                "cross_bank_only": fee.cross_bank_only,
                "label": fee.label,
            },
            "night_window": None if window is None else {
                "from_hour": window.from_hour,
                "to_hour": window.to_hour,
                "max_uah": window.max_uah,
            },
            # Те, що движок реально підставить картці цього банку, якщо
            # користувач нічого не задавав.
            "default_limits": default_limits_for_bank(slug),
        })
    # camelCase — як решта ендпоінтів. Чіпаються лише ключі: коди банків і
    # слаги лишаються значеннями й не мангляться.
    return dict_to_camel(result)


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
        # Назви поруч із кодами: одному банку відповідає кілька кодів
        # («43» і «1» — Monobank), і мапа для цього одна, на беку.
        "banks": bank_view_list(order.bank_codes or []),
        "link": order.link,
        "riskFlag": order.risk_flag or "",
        "compositeScore": order.composite_score,
        "reviewScore": order.review_score,
        "reviewNegPct": order.review_neg_pct,
        "tradeTerms": order.trade_terms or "",
        # Порожні умови означають дві протилежні речі: «мерчант нічого не
        # написав» і «ми не змогли дістати». Друге — факт про нас, і читати
        # його як факт про мерчанта не можна.
        "termsStatus": getattr(order, "terms_status", "") or "",
        "termsStatusLabel": blind_terms_label(getattr(order, "terms_status", "")),
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
    result: dict[str, Any] = {"buy": [], "sell": [], "rejected": {}, "scanned": True}

    wanted = ("buy", "sell") if side == "both" else (side,)
    for want in wanted:
        mode = "TAKER_BUY" if want == "buy" else "TAKER_SELL"
        try:
            scan = await scanner.scan(
                {**user, "scanner_mode": mode}, buy_grouped, sell_grouped
            )
        except Exception as e:
            logger.error("taker/orders %s: %s", mode, e)
            raise HTTPException(status_code=500, detail=f"{mode}: {e}") from e

        orders = scan.orders
        # TAKER_BUY шукає найдешевше, TAKER_SELL — найдорожче.
        orders.sort(key=lambda o: float(o.price), reverse=(mode == "TAKER_SELL"))
        result[want] = [_order_to_dict(o) for o in orders[:limit]]

        # Відкинуті — з причиною. Порожній список ордерів сам по собі не
        # каже, ринку немає чи карток не вистачило; тепер каже.
        result["rejected"][want] = dict_to_camel(
            [r.as_dict() for r in scan.rejections[:limit]]
        )

    # Вердикт LLM міг дозріти після того, як сканер віддав ордер: у чаті
    # повідомлення в такому разі редагується, тут підставляємо свіже.
    try:
        from api.verdict_refresh import refresh_flags

        for side_key in ("buy", "sell"):
            await refresh_flags(_db(), result.get(side_key) or [])
    except Exception as e:
        logger.debug("taker verdict refresh: %s", e)

    budget = await _buy_budget_view(telegram_id, user, result["buy"])
    result["budget"] = budget

    # Комісія рахується від суми, яку реально відправимо: у неї пороги
    # («до 20к — 0%»), тож від обсягу вона залежить прямо.
    for row in result["buy"]:
        amount = _fee_base_uah(row, budget)
        row["transferFee"] = _transfer_fee_view(row, amount)

    return result


def _fee_base_uah(order_row: dict, budget: Optional[dict]) -> float:
    """Скільки ₴ реально піде в цей ордер — у межах його min/max."""
    price = float(order_row.get("price") or 0.0)
    lo = float(order_row.get("minLimit") or 0.0)
    hi = float(order_row.get("maxLimit") or 0.0)

    if budget and price > 0:
        wanted = float(budget.get("effectiveUsdt") or 0.0) * price
        if wanted > 0:
            return max(lo, min(wanted, hi)) if hi > 0 else max(lo, wanted)
    return lo


async def _buy_budget_view(
    telegram_id: int, user: dict, buy_orders: list[dict]
) -> Optional[dict]:
    """
    Бажана сума проти того, з чим реально можна зайти зараз.

    Раніше різниця між ними ніде не існувала: авто-масштабування
    перезаписувало `taker_buy_amount`, і введені 700 USDT зникали назавжди.
    Тепер бажане лишається недоторканим, а «скільки виходить сьогодні» —
    похідна величина. Але похідну треба показати, інакше людина бачить 700
    і не розуміє, чому бот заходить на 480.

    Рахуємо за найкращою ціною з видачі: це найоптимістичніший варіант, і
    якщо навіть він менший за бажаний — упор точно в гроші, а не в ціну.
    """
    desired = float(user.get("taker_buy_amount") or 0.0)
    if desired <= 0 or not buy_orders:
        return None

    try:
        from core.engine.buy_budget import resolve_buy_budget

        breakdown = await _db().get_user_capital_breakdown(telegram_id)
        available = float(breakdown.get("usable") or 0.0)
        price = min(float(o["price"]) for o in buy_orders if o.get("price"))

        budget = resolve_buy_budget(desired, available, price)
        return {
            "desiredUsdt": round(budget.desired_usdt, 2),
            "effectiveUsdt": round(budget.effective_usdt, 2),
            "availableUah": round(budget.available_uah, 2),
            "price": round(budget.price, 2),
            "scaled": budget.scaled,
            "blocked": budget.blocked,
            # Порожній bestBank при interBank=true — не втрата даних: маршрут
            # іде з кількох банків, і одна назва вводила б в оману.
            "bestBank": breakdown.get("best_bank") or "",
            "interBank": bool(breakdown.get("inter_bank")),
            "totalUah": round(float(breakdown.get("total") or 0.0), 2),
        }
    except Exception as e:  # показ бюджету не має ламати видачу ордерів
        logger.debug("buy budget view: %s", e)
        return None


def _transfer_fee_view(order_row: dict, amount_uah: float) -> Optional[dict]:
    """
    Комісія банку за переказ фіату під цей ордер — і курс із нею всередині.

    Те саме, що бот уже пише в алерті (`bot/taker_builder._transfer_fee`),
    але на сайті ордер досі виглядав вигіднішим, ніж є: при спреді 0.5–1%
    комісія А-Банку 2% з'їдає весь профіт.

    Тільки для купівлі: у TAKER_SELL фіат відправляє мерчант і комісію
    свого банку платить він.
    """
    banks = order_row.get("bankCodes") or []
    price = float(order_row.get("price") or 0.0)
    if not banks or price <= 0 or amount_uah <= 0:
        return None

    try:
        from config.banks import bank_display_name, normalize_bank
        from core.utils.fees import bank_transfer_fee

        bank = normalize_bank(banks[0])
        # У тейкері переказ іде на той самий банк: движок добирає картку
        # рівно того банку, який приймає мерчант. Тож «міжбанківські»
        # комісії (ПриватБанк, Monobank) тут не виникають.
        fee_obj = bank_transfer_fee(bank, bank)
        if fee_obj is None:
            return None

        result = fee_obj.calculate(amount_uah, price)
        if result.amount <= 0:
            return None

        return {
            "bank": bank_display_name(bank),
            "amountUah": round(result.amount, 2),
            "description": result.description,
            # Курс, у який комісія вже закладена: саме його треба порівнювати
            # з цінами інших ордерів, а не «чисту» ціну.
            "effectivePrice": round(price * (1 + result.amount / amount_uah), 4),
            "onAmountUah": round(amount_uah, 2),
        }
    except Exception as e:  # комісія не має ламати видачу ордерів
        logger.debug("transfer fee view: %s", e)
        return None


@router.get("/inventory/usdt")
async def get_usdt_inventory(
    force: bool = Query(default=False, description="Обійти кеш балансів"),
    telegram_id: Optional[int] = None,
    session_user_id: Optional[int] = Depends(optional_session),
):
    """
    Де саме лежить USDT — по біржах і по гаманцях.

    «Є на Bybit 500 USDT» не означає «можу продати зараз»: після купівлі на
    P2P монети падають на спот, а продаються з фандингу. `get_balance()` у
    клієнтів зливає обидва гаманці в одне число, тож різницю не було видно
    ніде — вона з'ясовувалась уже під таймер угоди.

    `known: false` означає «не знаємо» — ключів немає або біржі не
    відповіли. Це не те саме, що нуль: нуль веде до висновку «треба
    переказувати», а невідоме не веде ні до якого висновку.
    """
    from core.engine.usdt_inventory import usdt_by_exchange

    telegram_id = resolve_user_id(telegram_id, session_user_id)
    await _user_or_404(telegram_id)

    balances = await usdt_by_exchange(_db(), telegram_id, force=force)
    if balances is None:
        return {"known": False, "exchanges": [], "totals": {}}

    rows = []
    for name, w in sorted(balances.items()):
        rows.append({
            "exchange": name,
            # Три різні відстані до угоди, а не три однакові кошики.
            "funding": round(w.funding, 2),   # продається зараз
            "spot": round(w.spot, 2),         # один клік усередині біржі
            "earn": round(w.earn, 2),         # спершу викупити
            "earnKnown": w.earn_known,
            "total": round(w.total, 2),
        })

    return {
        "known": True,
        "exchanges": rows,
        "totals": {
            "funding": round(sum(r["funding"] for r in rows), 2),
            "spot": round(sum(r["spot"] for r in rows), 2),
            "earn": round(sum(r["earn"] for r in rows), 2),
            "total": round(sum(r["total"] for r in rows), 2),
        },
    }


@router.get("/taker/readiness")
async def get_taker_readiness(
    mode: str = Query(default="", description="TAKER_BUY | TAKER_SELL; порожнє — режим користувача"),
    telegram_id: Optional[int] = None,
    session_user_id: Optional[int] = Depends(optional_session),
):
    """
    Що завадить тейкер-режиму працювати так, як його щойно налаштували.

    Ці перевірки жили в циклі сканера й спрацьовували вже після запуску:
    людина вмикала режим і чекала, а причина тиші лежала в налаштуваннях і
    була видна одразу — обрано банки, карток яких немає; обсяг більший за
    все, що є на картках; місячна межа банку майже вибрана.

    Порожній список означає «все сходиться».
    """
    from core.engine.readiness import check_taker_readiness
    from core.engine.scanner_helpers import _user_modes

    telegram_id = resolve_user_id(telegram_id, session_user_id)
    user = await _user_or_404(telegram_id)

    # Без явного режиму перевіряємо всі увімкнені тейкерські — так само, як
    # /checkup у боті. Брати тут `scanner_mode` було б помилкою: режимів
    # може бути кілька одночасно, і одиничне поле показало б лише один.
    if mode:
        modes = [mode] if mode in ("TAKER_BUY", "TAKER_SELL") else []
    else:
        modes = [m for m in _user_modes(user) if m in ("TAKER_BUY", "TAKER_SELL")]

    db = _db()
    checks: list[dict] = []
    for m in modes:
        for c in await check_taker_readiness(db, user, m):
            checks.append({"level": c.level, "text": c.text, "hint": c.hint, "mode": m})

    return {
        "mode": mode or ",".join(modes),
        "modes": modes,
        "checks": checks,
        "hasBlockers": any(c["level"] == "blocker" for c in checks),
    }


@router.get("/taker/rejections")
async def get_taker_rejections(
    days: int = Query(default=7, ge=1, le=14),
    telegram_id: Optional[int] = None,
    session_user_id: Optional[int] = Depends(optional_session),
):
    """
    Статистика причин відмов карткового модуля за N днів.

    Дедуп за ордером і днем уже застосований на записі, тож числа тут — це
    скільки РІЗНИХ ордерів відсіялось, а не скільки кіл зробив сканер.
    """
    telegram_id = resolve_user_id(telegram_id, session_user_id)
    await _user_or_404(telegram_id)
    stats = await _db().get_rejection_stats(telegram_id, days=days)

    from config.banks import bank_display_name
    from core.engine.rejection_codes import label

    for row in stats["codes"] + stats.get("observations", []):
        row["title"] = label(row["code"])
        # У базі лежать слаги й сирі коди бірж. «545» серед назв банків
        # читається як банк, і людина шукає помилку у своїх картках — тоді
        # як під цей код картка не підбереться, поки його немає в реєстрі.
        row["banks"] = [bank_display_name(b) for b in row.get("banks", [])]
    return dict_to_camel(stats)


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


@router.get("/cards/match")
async def match_cards(
    bank: str = Query(..., description="Банк мерчанта: код або слаг"),
    amount: float = Query(..., gt=0, description="Сума угоди, ₴"),
    direction: str = Query(default="buy", description="buy | sell"),
    telegram_id: Optional[int] = None,
    session_user_id: Optional[int] = Depends(optional_session),
):
    """
    Які картки підходять під цю угоду, які ні — і що зробити, щоб підійшли.

    Бот шле це окремим повідомленням після кожного алерта («💳 Рекомендований
    пластик під угоду»), а на сайті блоку не було взагалі: людина бачила
    ордер, але не знала, чи зможе його взяти, поки не відкриє Telegram.

    Причини відмов приходять структуровано — тими самими кодами, що й у
    статистиці, тож «не вистачає балансу» тут і в дайджесті означає те саме.
    """
    from core.engine.card_matching_engine import CardMatchingEngine
    from core.engine.transfer_advice import suggest_transfers

    if direction not in ("buy", "sell"):
        raise HTTPException(status_code=400, detail="direction: очікується buy або sell")

    telegram_id = resolve_user_id(telegram_id, session_user_id)
    await _user_or_404(telegram_id)
    db = _db()

    slug = normalize_bank(bank)
    result = await CardMatchingEngine(db).run(telegram_id, slug, amount, direction)

    # Баланси всіх активних карток — той самий блок, що бот друкує під
    # порадами: без нього незрозуміло, звідки брати нестачу.
    cards = await db.get_cards(owner_id=telegram_id, status="active")
    balances = [
        {
            "id": c["id"],
            "bank": c.get("bank_name", ""),
            "bankName": bank_display_name(c.get("bank_name", "")),
            "lastFour": c.get("last_four", ""),
            "label": c.get("label") or "",
            "balance": float(c.get("balance") or 0.0),
            "isWarmedUp": bool(c.get("is_warmed_up")),
        }
        for c in cards
    ]

    # Поради потрібні лише коли грошей на цільовому банку бракує — для
    # продажу переказувати нічого не треба, там фіат приходить нам.
    tips = []
    if direction == "buy" and result.status != "success":
        tips = [t.as_dict() for t in await suggest_transfers(db, telegram_id, slug, amount)]

    return {
        "bank": slug,
        "bankName": bank_display_name(slug),
        "amountUah": amount,
        "direction": direction,
        "status": result.status,
        "bestCard": result.best_card,
        "splitOptions": result.split_options,
        "availableUah": round(result.available_uah, 2),
        "rejections": dict_to_camel(result.rejection_report or []),
        "balances": balances,
        "transferTips": tips,
    }


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
