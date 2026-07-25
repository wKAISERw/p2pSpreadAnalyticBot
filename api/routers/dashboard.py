# api/routers/dashboard.py
import logging
from fastapi import APIRouter, HTTPException, Request
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

router = APIRouter(prefix="/api/v1", tags=["Dashboard"])
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
async def get_logs():
    return dict_to_camel(jsonable_encoder(state.logs))

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
        await db._db.execute(
            "DELETE FROM merchant_blacklist WHERE exchange=? AND merchant_id=?",
            (exchange, merchant_id)
        )
        await db._db.commit()
        return {"status": "success"}
    except Exception as e:
        return {"status": "error", "detail": str(e)}

@router.get("/settings/global")
async def get_global_settings():
    if hasattr(state, "global_settings"):
        return dict_to_camel(jsonable_encoder(state.global_settings))
    return dict_to_camel({"min_spread": 0.5, "min_profit": 100, "max_risk_score": 50, "scan_interval": 5,
                          "active_exchanges": ["binance", "bybit"]})

@router.get("/settings/user")
async def get_user_settings():
    if hasattr(state, "user_settings"):
        return dict_to_camel(jsonable_encoder(state.user_settings))
    return dict_to_camel(
        {"telegram_notifications": True, "telegram_chat_id": "", "sound_alerts": True, "auto_trade": None})

@router.post("/settings/global")
async def update_global_settings(settings: dict):
    from state import state
    snake_settings = dict_to_snake(settings)
    if hasattr(state, "global_settings"):
        if isinstance(state.global_settings, dict):
            state.global_settings.update(snake_settings)
        else:
            for key, value in snake_settings.items():
                setattr(state.global_settings, key, value)
    return {"status": "success", "updated_settings": snake_settings}

@router.post("/settings/user")
async def update_local_user_settings(settings: dict):
    from state import state
    snake_settings = dict_to_snake(settings)
    if hasattr(state, "user_settings"):
        if isinstance(state.user_settings, dict):
            state.user_settings.update(snake_settings)
        else:
            for key, value in snake_settings.items():
                setattr(state.user_settings, key, value)
    return {"status": "success", "updated_settings": snake_settings}

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

@router.post("/user/settings")
async def update_telegram_settings(settings: dict):
    from bot.handlers.core import _db as db
    snake_settings = dict_to_snake(settings)
    telegram_id = settings.get("telegramUserId")

    if telegram_id:
        banks_data = snake_settings.get("banks", [])
        banks_str = ",".join(banks_data) if isinstance(banks_data, list) else str(banks_data)

        await db._db.execute(
            "UPDATE scanner_users SET working_capital = ?, min_spread_pct = ?, bank_codes = ? WHERE user_id = ?",
            (snake_settings.get("max_capital"), snake_settings.get("min_spread"), banks_str, telegram_id)
        )
        await db._db.commit()

    if hasattr(state, "user_settings"):
        state.user_settings.update(snake_settings)

    return {"status": "success"}

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