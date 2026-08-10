# api/routers/dashboard.py
import logging
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.encoders import jsonable_encoder

from state import state
from api.schemas import ApiKeyPayload, BlacklistPayload
from api.utils import dict_to_camel, dict_to_snake

# Імпортуємо офіційні outward-клієнти бірж
from infrastructure.api.binance_account import BinanceAccountClient
from infrastructure.api.bybit_account import BybitAccountClient
from infrastructure.api.okx_account import OKXAccountClient
from infrastructure.api.mexc_account import MEXCAccountClient

# Імпортуємо перевірку прав з хендлерів боту
from bot.handlers.core import _is_admin

from api.auth import optional_session, require_admin, require_session, resolve_user_id
from api.security import require_api_key
from core.exchange_names import CANONICAL_EXCHANGES, canonical_exchange

# Автентифікація на рівні роутера — жоден ендпоінт не може випадково
# лишитись без неї.
#
# X-API-Key каже лише «цьому клієнту можна стукати в API». Хто саме стукає,
# знає тільки session-токен, тому персональні ендпоінти нижче додатково
# беруть user_id із сесії, а спільні дії — з-під require_admin.
router = APIRouter(prefix="/api/v1", tags=["Dashboard"], dependencies=[Depends(require_api_key)])
logger = logging.getLogger("ApiDashboard")


def _require_exchange(raw: str) -> str:
    """
    Канонічне написання назви біржі або 400.

    Тут роками стояв `.capitalize()`, який мовчки перетворював "okx" на
    "Okx" — назву, за якою бот креденшли вже не знаходив (див.
    core/exchange_names.py).
    """
    name = canonical_exchange(raw)
    if not name:
        raise HTTPException(
            status_code=400,
            detail=f"Невідома біржа: {raw}. Доступні: {', '.join(CANONICAL_EXCHANGES)}",
        )
    return name

@router.get("/stats")
async def get_stats():
    return dict_to_camel(jsonable_encoder(state.stats))

@router.get("/exchanges")
async def get_exchanges():
    """Стан доступності бірж (enabled/disabled/cooldown)."""
    from core.engine.exchange_manager import exchange_manager
    return dict_to_camel(jsonable_encoder(exchange_manager.get_status_all()))

@router.get("/stats/detailed")
async def get_detailed_stats(
    period: int = 30,
    mode: str = "ALL",
    scope: str = "mine",
    telegram_id: int = Depends(require_session),
):
    """
    Детальна статистика: summary, proposals, daily, exchanges, banks,
    heatmap, weekly.

    scope="mine" (за замовчуванням) рахує лише угоди цього користувача —
    так само, як «💼 Моя статистика» в боті. Раніше сюди не передавався
    owner_user_id узагалі, тож StatsEngine брав дефолт 0 = «всі юзери», і
    кожен бачив зведений PnL усіх разом.

    scope="all" лишається для адміна: це погляд оператора на систему.
    """
    from bot.handlers.core import _db as db
    from core.analytics.stats_engine import StatsEngine

    if scope not in {"mine", "all"}:
        raise HTTPException(status_code=400, detail="scope: очікується 'mine' або 'all'")
    if scope == "all" and not _is_admin(telegram_id):
        raise HTTPException(status_code=403, detail="Зведена статистика доступна лише адміністратору")

    engine = StatsEngine(db)
    data = await engine.get_full_stats(
        period_days=period,
        owner_user_id=0 if scope == "all" else telegram_id,
        mode=mode,
    )
    return dict_to_camel(jsonable_encoder(data))

@router.get("/opportunities")
async def get_opportunities(
    personal: bool = Query(default=True, description="Застосувати фільтри користувача"),
    show_rejected: bool = Query(default=False, description="Лишити відсіяні, з причиною"),
    telegram_id: Optional[int] = None,
    session_user_id: Optional[int] = Depends(optional_session),
):
    """
    Спред-зв'язки останнього циклу — за фільтрами того, хто питає.

    Довго віддавався глобальний `state.opportunities`: те, що знайшов
    сканер, без жодної персоналізації. На сайті були видні зв'язки, яких
    цей користувач у Telegram не отримав би ніколи — не проходили ні за
    капіталом, ні за спредом, ні за банками, ні за порогами мерчанта. І
    навпаки: розбіжність «на сайті густо, у чаті тихо» пояснити було нічим.

    Рішення ухвалює той самий `AlertDispatcher._user_wants`, що й для
    алертів: друга копія цих перевірок неминуче розійшлася б із першою.

    Без сесії або з `personal=false` поведінка стара — сирий список.
    """
    opps = state.opportunities

    # Вердикт міг дозріти вже після того, як цикл поклав зв'язку в стан:
    # LLM працює асинхронно. У Telegram повідомлення в такому разі
    # редагується, тут — підставляємо свіже на момент запиту.
    try:
        from bot.handlers.core import _db as _verdict_db
        from api.verdict_refresh import refresh_opportunity_flags

        await refresh_opportunity_flags(_verdict_db, opps)
    except Exception as e:
        logger.debug("opportunities verdict refresh: %s", e)

    if not personal or not opps:
        return dict_to_camel(jsonable_encoder(opps))

    try:
        telegram_id = resolve_user_id(telegram_id, session_user_id)
    except HTTPException:
        # Не залогінений — показуємо як було. Це вітрина, а не чужі дані.
        return dict_to_camel(jsonable_encoder(opps))

    from bot.handlers.core import _db as db
    from core.engine.alert_dispatcher import AlertDispatcher

    try:
        verdicts = await AlertDispatcher(db, None).wants_which(
            telegram_id, state.opportunities_raw or {}
        )
    except Exception as e:
        logger.warning("opportunities personal filter: %s", e)
        return dict_to_camel(jsonable_encoder(opps))

    result = []
    for opp in opps:
        reason = verdicts.get(opp["id"])
        # Немає вердикту — зв'язка з попереднього циклу, сирого opp під неї
        # вже немає. Ховати її було б гірше: людина побачила б порожньо там,
        # де насправді просто не встигли перерахувати.
        if reason is None or reason == "":
            result.append(opp)
        elif show_rejected:
            result.append({**opp, "rejectedReason": reason})

    return dict_to_camel(jsonable_encoder(result))

@router.get("/logs")
async def get_logs(limit: int = 200):
    """Останні записи логу (найновіші першими)."""
    recent = list(state.logs)[-max(1, min(limit, 500)):]
    recent.reverse()
    return dict_to_camel(jsonable_encoder(recent))

@router.post("/credentials/{exchange}")
async def save_credentials(
    exchange: str,
    payload: ApiKeyPayload,
    telegram_id: Optional[int] = None,
    session_user_id: Optional[int] = Depends(optional_session),
):
    """
    Кладе ключі біржі в зашифроване сховище бота.

    Раніше telegram_id брався з query і за замовчуванням дорівнював 0 —
    тобто ключі можна було записати будь-кому, а без параметра вони лягали
    в службовий слот власника, де їх не бачив жоден реальний користувач.
    """
    from bot.handlers.core import _db as db

    telegram_id = resolve_user_id(telegram_id, session_user_id)
    name = _require_exchange(exchange)

    try:
        ok = await db.save_credentials(
            exchange=name,
            api_key=payload.key,
            api_secret=payload.secret,
            passphrase=payload.passphrase,
            label="api",
            user_id=telegram_id,
        )
        if not ok:
            raise HTTPException(status_code=500, detail="Не вдалось зберегти ключі")
        return {"status": "success", "exchange": name}
    except HTTPException:
        raise
    except Exception as e:
        logger.error("save_credentials [%s]: %s", name, e)
        raise HTTPException(status_code=500, detail=str(e))


@router.delete("/credentials/{exchange}")
async def delete_credentials(
    exchange: str,
    telegram_id: Optional[int] = None,
    session_user_id: Optional[int] = Depends(optional_session),
):
    """
    Відв'язує біржу від акаунта.

    Кнопка «Disconnect» на фронтенді досі лише прибирала ключі з локального
    сховища браузера — у боті вони лишались і сканер далі ходив на біржу
    під ними.
    """
    from bot.handlers.core import _db as db

    telegram_id = resolve_user_id(telegram_id, session_user_id)
    name = _require_exchange(exchange)

    try:
        # delete_credentials рапортує True навіть коли рядка не було —
        # тому наявність перевіряємо окремо, інакше 404 був би недосяжним.
        if not await db.has_credentials(name, user_id=telegram_id):
            raise HTTPException(status_code=404, detail="Credentials not found")
        if not await db.delete_credentials(exchange=name, user_id=telegram_id):
            raise HTTPException(status_code=500, detail="Delete failed")
        return {"status": "success", "exchange": name}
    except HTTPException:
        raise
    except Exception as e:
        logger.error("delete_credentials: %s", e)
        raise HTTPException(status_code=500, detail=str(e))


# ═══════════════════════════════════════════════════════════════════════════
# Чорний список
# ═══════════════════════════════════════════════════════════════════════════
#
# Списків два, і це не дублювання:
#
#   personal — «мені цей мерчант не подобається». Свій у кожного, правиться
#              без жодних прав, впливає лише на власні алерти.
#   global   — спільний. Наповнюють ризик-движок і адміністратор; діє на всіх,
#              тому редагувати може лише адмін.
#
# Читають обидва всі: бачити, що система вже позначила скамера, корисно
# кожному. Раніше ендпоінт віддавав тільки спільний і не давав пересічному
# користувачу забанити нікого взагалі.


@router.get("/blacklist")
async def get_blacklist(telegram_id: int = Depends(require_session)):
    """Особистий список користувача плюс спільний, з поміткою scope."""
    from bot.handlers.core import _db as db

    entries: list[dict] = []
    try:
        for row in await db.get_user_blacklist(telegram_id):
            entries.append({**row, "source": "personal", "scope": "personal"})
    except Exception as e:
        logger.error("get_user_blacklist: %s", e)

    try:
        if hasattr(db, "get_all_blacklist"):
            for row in await db.get_all_blacklist():
                entries.append({**dict(row), "scope": "global"})
    except Exception as e:
        logger.error("Error fetching blacklist: %s", e)

    return dict_to_camel(entries)


@router.post("/blacklist")
async def add_to_blacklist(
    payload: BlacklistPayload,
    scope: str = "personal",
    telegram_id: int = Depends(require_session),
):
    from bot.handlers.core import _db as db

    if scope not in {"personal", "global"}:
        raise HTTPException(status_code=400, detail="scope: очікується 'personal' або 'global'")
    if scope == "global" and not _is_admin(telegram_id):
        raise HTTPException(
            status_code=403,
            detail="Спільний чорний список редагує лише адміністратор",
        )

    exchange = _require_exchange(payload.exchange)
    try:
        if scope == "personal":
            await db.add_user_blacklist(
                telegram_id, exchange, payload.merchantId,
                payload.merchantName, payload.reason,
            )
        else:
            await db.add_to_blacklist(
                exchange, payload.merchantId, payload.merchantName,
                payload.reason, payload.source or f"web:{telegram_id}",
            )
        return {"status": "success", "scope": scope}
    except HTTPException:
        raise
    except Exception as e:
        logger.error("add_to_blacklist: %s", e)
        raise HTTPException(status_code=500, detail=str(e))


@router.delete("/blacklist/{exchange}/{merchant_id}")
async def remove_from_blacklist(
    exchange: str,
    merchant_id: str,
    scope: str = "personal",
    telegram_id: int = Depends(require_session),
):
    from bot.handlers.core import _db as db

    if scope not in {"personal", "global"}:
        raise HTTPException(status_code=400, detail="scope: очікується 'personal' або 'global'")
    if scope == "global" and not _is_admin(telegram_id):
        raise HTTPException(
            status_code=403,
            detail="Спільний чорний список редагує лише адміністратор",
        )

    try:
        if scope == "personal":
            if not await db.remove_user_blacklist(telegram_id, exchange, merchant_id):
                raise HTTPException(status_code=404, detail="Merchant not found in blacklist")
            return {"status": "success", "scope": scope}

        # Таблиця називається global_blacklist. Тут роками стояло
        # `merchant_blacklist`, якої не існує — DELETE завжди падав у except
        # і ендпоінт мовчки повертав error.
        cursor = await db._db.execute(
            "DELETE FROM global_blacklist WHERE exchange=? AND merchant_id=?",
            (exchange, merchant_id)
        )
        await db._db.commit()
        if cursor.rowcount == 0:
            raise HTTPException(status_code=404, detail="Merchant not found in blacklist")
        return {"status": "success", "scope": scope, "deleted": cursor.rowcount}
    except HTTPException:
        raise
    except Exception as e:
        logger.error("remove_from_blacklist: %s", e)
        raise HTTPException(status_code=500, detail=str(e))

@router.get("/settings/global")
async def get_global_settings():
    """Глобальні налаштування — читаються з того ж джерела, що й сканер."""
    from config.runtime import runtime_config, ALLOWED_KEYS
    return dict_to_camel({key: runtime_config.get(key) for key in sorted(ALLOWED_KEYS)})


@router.post("/settings/global")
async def update_global_settings(settings: dict, admin_id: int = Depends(require_admin)):
    """
    Пише глобальні налаштування в bot_settings (runtime_config) — сканер
    перечитує їх раз на 10с. Впливає на всіх користувачів, тому лише адмін:
    UI ховав цю секцію від решти, але HTTP лишався відкритим.

    Раніше цей ендпоінт складав значення в in-memory `state.global_settings`,
    який НІХТО не читав: сканер бере конфіг з runtime_config. Тобто UI
    рапортував "success", а налаштування не діяли взагалі.
    """
    from config.runtime import runtime_config, ALLOWED_KEYS

    # Ключі ваг ризик-движка лежать у ALLOWED_KEYS у верхньому регістрі
    # (W_REGEX, W_LLM…). Дорога туди й назад їх ламає: to_camel робить
    # W_REGEX → WRegex, а to_snake з WRegex → w_regex. Точного збігу немає,
    # і ці шість ключів відхилялись завжди, хоч UI і рапортував успіх.
    # Звіряємось без урахування регістру і пишемо в канонічному вигляді.
    canonical = {key.lower(): key for key in ALLOWED_KEYS}

    snake_settings = dict_to_snake(settings)
    applied, rejected = {}, []
    for key, value in snake_settings.items():
        canonical_key = canonical.get(key.lower())
        if not canonical_key:
            rejected.append(key)
            continue
        if await runtime_config.set(canonical_key, value):
            applied[canonical_key] = str(value)
        else:
            rejected.append(key)

    if rejected and not applied:
        raise HTTPException(
            status_code=400,
            detail=f"Невідомі ключі налаштувань: {', '.join(sorted(rejected))}",
        )
    return {"status": "success", "applied": applied, "rejected": sorted(rejected)}


@router.get("/settings/user")
async def get_user_settings(
    telegram_id: Optional[int] = None,
    session_user_id: Optional[int] = Depends(optional_session),
):
    """Персональні налаштування юзера зі scanner_users."""
    from bot.handlers.core import _db as db

    telegram_id = resolve_user_id(telegram_id, session_user_id)
    users = await db.get_active_users()
    user = next((u for u in users if u["user_id"] == telegram_id), None)
    if not user:
        raise HTTPException(status_code=404, detail="User not found")
    return dict_to_camel(jsonable_encoder(user))

@router.get("/telegram/sync/{telegram_id}")
async def sync_telegram(
    telegram_id: int,
    session_user_id: Optional[int] = Depends(optional_session),
):
    from bot.handlers.core import _db as db

    telegram_id = resolve_user_id(telegram_id, session_user_id)
    users = await db.get_active_users()
    user_data = next((u for u in users if u["user_id"] == telegram_id), None)

    if not user_data:
        return {"error": "User not found in bot database"}

    creds = await db.get_all_credentials(user_id=telegram_id)
    is_admin = _is_admin(telegram_id)

    data = {
        "settings": {
            # Мінімального капіталу як налаштування не існує — колонки під
            # нього немає ні в scanner_users, ні в bot_settings. Тут роками
            # стояла константа 1000, яку фронтенд міг прийняти за реальне
            # значення користувача.
            "maxCapital": user_data.get("capital", 0),
            "minSpread": user_data.get("min_spread", 0),
            "banks": user_data.get("bank_codes") if isinstance(user_data.get("bank_codes"), list) else (user_data.get("bank_codes", "").split(",") if user_data.get("bank_codes") else [])
        },
        # Канонічні назви, як вони лежать у базі: фронтенд звіряє їх
        # без урахування регістру, а «okx» проти «OKX» тут уже ламалось.
        "keys": sorted(creds.keys()),
        "isAdmin": is_admin
    }
    return dict_to_camel(data)

@router.post("/settings/user")
async def update_local_user_settings(
    settings: dict,
    session_user_id: Optional[int] = Depends(optional_session),
):
    """
    Аліас на /user/settings — лишений щоб не ламати наявний фронтенд.
    Раніше писав у in-memory state.user_settings, який ніхто не читав.
    """
    return await update_telegram_settings(settings, session_user_id)


@router.post("/user/settings")
async def update_telegram_settings(
    settings: dict,
    session_user_id: Optional[int] = Depends(optional_session),
):
    from bot.handlers.core import _db as db
    snake_settings = dict_to_snake(settings)
    telegram_id = resolve_user_id(settings.get("telegramUserId"), session_user_id)

    # Оновлюємо тільки ті поля, що реально прийшли. Раніше сюди летіли
    # безумовні .get() — відсутній ключ перетворювався на NULL і затирав
    # капітал/спред юзера.
    updates: dict[str, object] = {}
    if snake_settings.get("max_capital") is not None:
        updates["working_capital"] = float(snake_settings["max_capital"])
    if snake_settings.get("min_spread") is not None:
        updates["min_spread_pct"] = float(snake_settings["min_spread"])
    if "banks" in snake_settings:
        banks_data = snake_settings.get("banks") or []
        updates["bank_codes"] = (
            ",".join(str(b) for b in banks_data)
            if isinstance(banks_data, list) else str(banks_data)
        )

    if not updates:
        return {"status": "success", "updated": []}

    assignments = ", ".join(f"{col} = ?" for col in updates)
    await db._db.execute(
        f"UPDATE scanner_users SET {assignments} WHERE user_id = ?",
        (*updates.values(), telegram_id),
    )
    await db._db.commit()

    return {"status": "success", "updated": sorted(updates)}

@router.get("/accounts/{telegram_id}")
async def get_real_exchange_accounts(
    telegram_id: int,
    session_user_id: Optional[int] = Depends(optional_session),
):
    from bot.handlers.core import _db as db

    telegram_id = resolve_user_id(telegram_id, session_user_id)
    creds = await db.get_all_credentials(user_id=telegram_id)

    if not creds:
        return []

    accounts = []

    for exchange_name, keys in creds.items():
        api_key = keys.get("api_key", "")
        api_secret = keys.get("api_secret", "")
        passphrase = keys.get("passphrase", "")

        # kycLevel, merchantStatus і tradingVolume30d нижче заповнюються
        # тільки для тих бірж, у яких для цього є виклик. Для решти вони
        # лишаються None — фронтенд покаже «немає даних» замість
        # правдоподібних «Verified / None / 0», які нічого не означали.
        #
        # volumeLimit тут теж колись стояв константою 100000. Ліміт обігу
        # залежить від рівня верифікації на кожній біржі, ми його не знаємо
        # і вигадувати не будемо.
        account_data = {
            "id": exchange_name.lower(),
            "exchange": canonical_exchange(exchange_name) or exchange_name,
            "balanceUAH": 0.0,
            "balanceUSDT": 0.0,
            "kycLevel": None,
            "merchantStatus": None,
            "tradingVolume30d": None,
            "volumeLimit": None,
        }

        try:
            balances = []

            if exchange_name.lower() == "binance":
                client = BinanceAccountClient(api_key, api_secret)
                balances = await client.get_balance()
                info = await client.get_account_info()

                account_data["kycLevel"] = "Verified Plus" if info.get("canTrade") else "Unverified"

                p2p_buy = await client.get_my_p2p_orders("BUY", rows=50)
                p2p_sell = await client.get_my_p2p_orders("SELL", rows=50)
                all_p2p = p2p_buy + p2p_sell

                account_data["merchantStatus"] = "Active" if len(all_p2p) > 0 else "None"
                account_data["tradingVolume30d"] = sum(float(o.get("amount", 0)) for o in all_p2p)

            elif exchange_name.lower() == "bybit":
                client = BybitAccountClient(api_key, api_secret)
                balances = await client.get_balance()

            elif exchange_name.lower() == "okx":
                client = OKXAccountClient(api_key, api_secret, passphrase)
                balances = await client.get_funding_balance()

                info = await client.get_account_info()
                p2p_orders = await client.get_my_p2p_orders(limit=50)

                acct_data = info.get("data", [{}])[0]
                account_data["kycLevel"] = f"Level {acct_data.get('acctLv', '1')}"
                account_data["merchantStatus"] = "Active" if len(p2p_orders) > 0 else "None"
                account_data["tradingVolume30d"] = sum(float(o.get("amount", 0)) for o in p2p_orders)

            elif exchange_name.lower() == "mexc":
                client = MEXCAccountClient(api_key, api_secret)
                balances = await client.get_balance()
                # Рівень верифікації MEXC тут не віддає: раніше в цьому
                # місці стояло безумовне "Verified", хоча відповідь клієнта
                # навіть не читалась.

            for b in balances:
                if b["coin"] == "USDT":
                    account_data["balanceUSDT"] = b["total"]
                elif b["coin"] == "UAH":
                    account_data["balanceUAH"] = b["total"]

        except Exception as e:
            logger.error(f"Помилка даних акаунта {exchange_name}: {e}")
            # Помилка запиту — це окремий стан, а не «рівень KYC = API Error».
            # Фронтенд має показати, що баланс не приїхав, а не намалювати
            # нулі так, ніби на акаунті справді порожньо.
            account_data["error"] = str(e)[:200]

        accounts.append(account_data)

    return dict_to_camel(accounts)