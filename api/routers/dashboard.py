# api/routers/dashboard.py
import logging
from fastapi import APIRouter, Depends, HTTPException
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

from api.security import require_api_key

# Автентифікація на рівні роутера — жоден ендпоінт не може випадково
# лишитись без неї.
router = APIRouter(prefix="/api/v1", tags=["Dashboard"], dependencies=[Depends(require_api_key)])
logger = logging.getLogger("ApiDashboard")

@router.get("/stats")
async def get_stats():
    return dict_to_camel(jsonable_encoder(state.stats))

@router.get("/exchanges")
async def get_exchanges():
    """Стан доступності бірж (enabled/disabled/cooldown)."""
    from core.engine.exchange_manager import exchange_manager
    return dict_to_camel(jsonable_encoder(exchange_manager.get_status_all()))

@router.get("/stats/detailed")
async def get_detailed_stats(period: int = 30):
    """Детальна статистика: daily, exchanges, banks, heatmap, weekly."""
    from bot.handlers.core import _db as db
    from core.analytics.stats_engine import StatsEngine
    engine = StatsEngine(db)
    data = await engine.get_full_stats(period_days=period)
    return dict_to_camel(jsonable_encoder(data))

@router.get("/opportunities")
async def get_opportunities():
    return dict_to_camel(jsonable_encoder(state.opportunities))

@router.get("/logs")
async def get_logs(limit: int = 200):
    """Останні записи логу (найновіші першими)."""
    recent = list(state.logs)[-max(1, min(limit, 500)):]
    recent.reverse()
    return dict_to_camel(jsonable_encoder(recent))

@router.post("/credentials/{exchange}")
async def save_credentials(exchange: str, payload: ApiKeyPayload, telegram_id: int = 0):
    from bot.handlers.core import _db as db
    try:
        ok = await db.save_credentials(
            exchange=exchange.capitalize(),
            api_key=payload.key,
            api_secret=payload.secret,
            passphrase=payload.passphrase,
            label="api",
            user_id=telegram_id,
        )
        return {"status": "success" if ok else "error"}
    except Exception as e:
        return {"status": "error", "detail": str(e)}

@router.delete("/credentials/{exchange}")
async def delete_credentials(exchange: str, telegram_id: int = 0):
    """
    Відв'язує біржу від акаунта.

    Кнопка «Disconnect» на фронтенді досі лише прибирала ключі з локального
    сховища браузера — у боті вони лишались і сканер далі ходив на біржу
    під ними.
    """
    from bot.handlers.core import _db as db
    name = exchange.capitalize()
    try:
        # delete_credentials рапортує True навіть коли рядка не було —
        # тому наявність перевіряємо окремо, інакше 404 був би недосяжним.
        if not await db.has_credentials(name, user_id=telegram_id):
            raise HTTPException(status_code=404, detail="Credentials not found")
        if not await db.delete_credentials(exchange=name, user_id=telegram_id):
            raise HTTPException(status_code=500, detail="Delete failed")
        return {"status": "success"}
    except HTTPException:
        raise
    except Exception as e:
        logger.error("delete_credentials: %s", e)
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/blacklist")
async def get_blacklist():
    from bot.handlers.core import _db as db
    try:
        if hasattr(db, "get_all_blacklist"):
            records = await db.get_all_blacklist()
            return dict_to_camel(records)
        return []
    except Exception as e:
        logger.error(f"Error fetching blacklist: {e}")
        return []

@router.post("/blacklist")
async def add_to_blacklist(payload: BlacklistPayload):
    from bot.handlers.core import _db as db
    try:
        await db.add_to_blacklist(
            payload.exchange,
            payload.merchantId,
            payload.merchantName,
            payload.reason,
            payload.source or "api",
        )
        return {"status": "success"}
    except Exception as e:
        logger.error("add_to_blacklist: %s", e)
        return {"status": "error", "detail": str(e)}

@router.delete("/blacklist/{exchange}/{merchant_id}")
async def remove_from_blacklist(exchange: str, merchant_id: str):
    from bot.handlers.core import _db as db
    try:
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
        return {"status": "success", "deleted": cursor.rowcount}
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
async def update_global_settings(settings: dict):
    """
    Пише глобальні налаштування в bot_settings (runtime_config) — сканер
    перечитує їх раз на 10с.

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
async def get_user_settings(telegram_id: int):
    """Персональні налаштування юзера зі scanner_users."""
    from bot.handlers.core import _db as db
    users = await db.get_active_users()
    user = next((u for u in users if u["user_id"] == telegram_id), None)
    if not user:
        raise HTTPException(status_code=404, detail="User not found")
    return dict_to_camel(jsonable_encoder(user))

@router.get("/telegram/sync/{telegram_id}")
async def sync_telegram(telegram_id: int):
    from bot.handlers.core import _db as db
    users = await db.get_active_users()
    user_data = next((u for u in users if u["user_id"] == telegram_id), None)

    if not user_data:
        return {"error": "User not found in bot database"}

    creds = await db.get_all_credentials(user_id=telegram_id)
    is_admin = _is_admin(telegram_id)

    data = {
        "settings": {
            "minCapital": 1000,
            "maxCapital": user_data.get("capital", 0),
            "minSpread": user_data.get("min_spread", 0),
            "banks": user_data.get("bank_codes") if isinstance(user_data.get("bank_codes"), list) else (user_data.get("bank_codes", "").split(",") if user_data.get("bank_codes") else [])
        },
        "keys": [k.lower() for k in creds.keys()],
        "isAdmin": is_admin
    }
    return dict_to_camel(data)

@router.post("/settings/user")
async def update_local_user_settings(settings: dict):
    """
    Аліас на /user/settings — лишений щоб не ламати наявний фронтенд.
    Раніше писав у in-memory state.user_settings, який ніхто не читав.
    """
    return await update_telegram_settings(settings)


@router.post("/user/settings")
async def update_telegram_settings(settings: dict):
    from bot.handlers.core import _db as db
    snake_settings = dict_to_snake(settings)
    telegram_id = settings.get("telegramUserId")

    if not telegram_id:
        raise HTTPException(
            status_code=400,
            detail="telegramUserId обов'язковий — без нього немає кого оновлювати",
        )

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
async def get_real_exchange_accounts(telegram_id: int):
    from bot.handlers.core import _db as db
    creds = await db.get_all_credentials(user_id=telegram_id)

    if not creds:
        return []

    accounts = []

    for exchange_name, keys in creds.items():
        api_key = keys.get("api_key", "")
        api_secret = keys.get("api_secret", "")
        passphrase = keys.get("passphrase", "")

        account_data = {
            "id": exchange_name.lower(),
            "exchange": exchange_name.capitalize(),
            "balanceUAH": 0.0,
            "balanceUSDT": 0.0,
            "kycLevel": "Verified",
            "merchantStatus": "None",
            "tradingVolume30d": 0,
            "volumeLimit": 100000
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
                info = await client.get_account_info()
                account_data["kycLevel"] = "Verified"

            for b in balances:
                if b["coin"] == "USDT":
                    account_data["balanceUSDT"] = b["total"]
                elif b["coin"] == "UAH":
                    account_data["balanceUAH"] = b["total"]

        except Exception as e:
            logger.error(f"Помилка даних акаунта {exchange_name}: {e}")
            account_data["kycLevel"] = "API Error"
            account_data["merchantStatus"] = "API Error"

        accounts.append(account_data)

    return dict_to_camel(accounts)