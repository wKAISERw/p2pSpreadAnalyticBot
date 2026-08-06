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

from api.auth import optional_session, resolve_user_id
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

_SCANNER_MODES = {"SPREAD", "MAKER_BUY", "MAKER_SELL", "TAKER_BUY", "TAKER_SELL"}
_SPREAD_STRATEGIES = {"min", "max", "range"}
_CAPITAL_MODES = {"manual", "auto"}
# Значення читає core/engine/taker_scanner.py — розходження тут означає
# мовчазний фільтр, який ніколи не спрацює.
_SPEEDS = {"FAST", "ANY"}
_SELL_PRICE_STRATEGIES = {"roi", "min", "range", "exact", "any"}
_BUY_PRICE_STRATEGIES = {"any", "max", "range", "exact"}
_BUY_BALANCE_MODES = {"CARD_ENFORCED", "AUTO_SCALE", "FREE"}


@router.get("/user/filters")
async def get_user_filters(
    telegram_id: Optional[int] = None,
    session_user_id: Optional[int] = Depends(optional_session),
):
    """Повний набір персональних фільтрів — те саме, що показує меню «Фільтри»."""
    telegram_id = resolve_user_id(telegram_id, session_user_id)
    user = await _db().get_user_by_id(telegram_id)
    if not user:
        raise HTTPException(status_code=404, detail="User not found")
    return dict_to_camel(jsonable_encoder(user))


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


@router.post("/scanner/start")
async def start_scanner():
    from config.runtime import runtime_config
    if not await runtime_config.set("is_scanner_active", "true"):
        raise HTTPException(status_code=500, detail="Не вдалось записати стан у bot_settings")
    logger.info("▶️ Ядро сканера запущено через HTTP API")
    return {"status": "success", "isScannerActive": True}


@router.post("/scanner/stop")
async def stop_scanner():
    from config.runtime import runtime_config
    if not await runtime_config.set("is_scanner_active", "false"):
        raise HTTPException(status_code=500, detail="Не вдалось записати стан у bot_settings")
    logger.info("⏸ Ядро сканера зупинено через HTTP API")
    return {"status": "success", "isScannerActive": False}


class MutePayload(BaseModel):
    # 0 = зняти паузу, >0 = пауза на N годин.
    hours: float = Field(default=1.0, ge=0, le=8760)


@router.post("/scanner/mute")
async def set_mute(payload: MutePayload):
    """
    Пауза алертів. Стан процесний (bot.handlers.core._mute_until), тому
    працює лише поки живий цей інстанс — так само, як і з боку бота.
    """
    from bot.handlers import core

    core._mute_until = 0.0 if payload.hours == 0 else time.monotonic() + payload.hours * 3600
    return {"status": "success", "isMuted": core.is_muted(), "hours": payload.hours}


@router.post("/exchanges/{name}/enable")
async def enable_exchange(name: str):
    from core.engine.exchange_manager import exchange_manager
    from config.runtime import runtime_config

    if not await exchange_manager.enable(name, runtime_config=runtime_config):
        raise HTTPException(status_code=404, detail=f"Невідома біржа: {name}")
    return {"status": "success", "exchange": name, "enabled": True}


class DisableExchangePayload(BaseModel):
    reason: str = "manual (web)"
    cooldownHours: float = Field(default=0, ge=0, le=720)


@router.post("/exchanges/{name}/disable")
async def disable_exchange(name: str, payload: DisableExchangePayload):
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
# Картки
# ═══════════════════════════════════════════════════════════════════════════

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

        enriched.append({
            **dict(card),
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
async def get_sessions(telegram_id: int = 0):
    """
    Свіжість перехоплених сесій бірж. Протухла сесія — головна причина,
    чому біржа раптом перестає віддавати дані, і побачити це в дашборді
    досі було ніяк.
    """
    from core.engine.exchange_manager import ALL_EXCHANGES

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
