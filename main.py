import re
import asyncio
import logging
import sys
import os
from logging.handlers import RotatingFileHandler
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.encoders import jsonable_encoder
import uvicorn
from pydantic import BaseModel
from core.storage.merchant_db import MerchantDB
from bot.commands import _is_admin
from infrastructure.api.binance_account import BinanceAccountClient
from infrastructure.api.bybit_account import BybitAccountClient
from infrastructure.api.okx_account import OKXAccountClient
from infrastructure.api.mexc_account import MEXCAccountClient
from bot.notifier import TelegramNotifier
from scanner import run_scanner
from state import state
from core.workers.db_maintenance import DBMaintenanceTask

db = MerchantDB()

# --- КОНВЕРТЕРИ ДЛЯ GET (Snake -> Camel) ---
def to_camel(snake_str: str) -> str:
    components = snake_str.split('_')
    return components[0] + ''.join(x.title() for x in components[1:])

def dict_to_camel(obj):
    if isinstance(obj, list):
        return [dict_to_camel(item) for item in obj]
    elif isinstance(obj, dict):
        return {to_camel(k): dict_to_camel(v) for k, v in obj.items()}
    return obj

# --- КОНВЕРТЕРИ ДЛЯ POST (Camel -> Snake) ---
def to_snake(camel_str: str) -> str:
    return re.sub(r'(?<!^)(?=[A-Z])', '_', camel_str).lower()

def dict_to_snake(obj):
    if isinstance(obj, list):
        return [dict_to_snake(item) for item in obj]
    elif isinstance(obj, dict):
        return {to_snake(k): dict_to_snake(v) for k, v in obj.items()}
    return obj

def setup_logging():
    os.makedirs("logs", exist_ok=True)
    logger = logging.getLogger()
    logger.setLevel(logging.DEBUG)

    formatter = logging.Formatter("%(asctime)s | %(levelname)s | %(name)s | %(message)s")

    console_handler = logging.StreamHandler(sys.stdout)
    console_handler.setLevel(logging.INFO)
    console_handler.setFormatter(formatter)

    debug_handler = RotatingFileHandler("logs/debug.log", maxBytes=5 * 1024 * 1024, backupCount=2, encoding="utf-8")
    debug_handler.setLevel(logging.DEBUG)
    debug_handler.setFormatter(formatter)

    error_handler = RotatingFileHandler("logs/error.log", maxBytes=5 * 1024 * 1024, backupCount=2, encoding="utf-8")
    error_handler.setLevel(logging.ERROR)
    error_handler.setFormatter(formatter)

    logger.addHandler(console_handler)
    logger.addHandler(debug_handler)
    logger.addHandler(error_handler)

# Глобальні змінні
scanner_task = None
notifier = None
stop_event = asyncio.Event()
db_maintainer = None

@asynccontextmanager
async def lifespan(app: FastAPI):
    global scanner_task, notifier, stop_event

    setup_logging()
    logger = logging.getLogger("Main")
    logger.info("🚀 Ініціалізація P2P Сканера + API (Production Mode)...")

    # ДОДАНО: Ініціалізуємо підключення до БД ДО запуску ендпоінтів
    await db.start()
    logger.info("✅ База даних успішно підключена для API")

    global db_maintainer
    db_maintainer = DBMaintenanceTask(db)
    db_maintainer.start()

    notifier = TelegramNotifier()
    await notifier.start()

    async def run_scanner_safe():
        try:
            # Передаємо єдиний екземпляр db — без дублювання MerchantDB
            await run_scanner(notifier, stop_event, shared_db=db)
        except Exception as e:
            logger.critical("🔥 КРИТИЧНА ПОМИЛКА СКАНЕРА: %s", e, exc_info=True)
            stop_event.set()

    scanner_task = asyncio.create_task(run_scanner_safe())

    yield

    # --- GRACEFUL SHUTDOWN ---
    logger.info("🧹 Початок завершення процесів...")
    stop_event.set()

    if scanner_task:
        scanner_task.cancel()
        try:
            await asyncio.wait_for(scanner_task, timeout=5.0)
        except (asyncio.CancelledError, asyncio.TimeoutError):
            pass

    if db_maintainer:
        await db_maintainer.stop()

    if notifier:
        try:
            await asyncio.wait_for(notifier.stop(), timeout=10.0)
            logger.info("✅ Telegram Notifier успішно зупинено.")
        except asyncio.TimeoutError:
            logger.error("⚠️ Timeout при зупинці нотифікатора.")

    # ДОДАНО: Закриваємо з'єднання з базою
    await db.stop()
    logger.info("🏁 Систему повністю зупинено. До зустрічі!")

app = FastAPI(title="Arbix Quantum API", lifespan=lifespan)

# CORS: wildcard origin несумісний з allow_credentials=True
# Використовуємо конкретні origins або вимикаємо credentials
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=False,  # True + wildcard = broken CORS spec
    allow_methods=["*"],
    allow_headers=["*"],
)

class ApiKeyPayload(BaseModel):
    key: str
    secret: str
    passphrase: str = ""

@app.get("/api/v1/stats")
async def get_stats():
    return dict_to_camel(jsonable_encoder(state.stats))

@app.get("/api/v1/exchanges")
async def get_exchanges():
    """Стан доступності бірж (enabled/disabled/cooldown)."""
    from core.engine.exchange_manager import exchange_manager
    return dict_to_camel(jsonable_encoder(exchange_manager.get_status_all()))

@app.get("/api/v1/stats/detailed")
async def get_detailed_stats(period: int = 30):
    """Детальна статистика: daily, exchanges, banks, heatmap, weekly."""
    from core.analytics.stats_engine import StatsEngine
    engine = StatsEngine(db)
    data = await engine.get_full_stats(period_days=period)
    return dict_to_camel(jsonable_encoder(data))

@app.get("/api/v1/opportunities")
async def get_opportunities():
    return dict_to_camel(jsonable_encoder(state.opportunities))

@app.get("/api/v1/logs")
async def get_logs():
    return dict_to_camel(jsonable_encoder(state.logs))

@app.post("/api/v1/credentials/{exchange}")
async def save_credentials(exchange: str, payload: ApiKeyPayload, telegram_id: int = 0):
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

class BlacklistPayload(BaseModel):
    exchange: str
    merchantId: str
    merchantName: str
    reason: str
    source: str

# ДОДАНО: GET-ендпоінт для вирішення помилки 405 (Method Not Allowed)
@app.get("/api/v1/blacklist")
async def get_blacklist():
    try:
        if hasattr(db, "get_all_blacklist"):
            records = await db.get_all_blacklist()
            return dict_to_camel(records)
        return []
    except Exception as e:
        logging.getLogger("Main").error(f"Error fetching blacklist: {e}")
        return []

@app.post("/api/v1/blacklist")
async def add_to_blacklist(payload: BlacklistPayload):
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
        logging.getLogger("Main").error("add_to_blacklist: %s", e)
        return {"status": "error", "detail": str(e)}

@app.delete("/api/v1/blacklist/{exchange}/{merchant_id}")
async def remove_from_blacklist(exchange: str, merchant_id: str):
    try:
        await db._db.execute(
            "DELETE FROM merchant_blacklist WHERE exchange=? AND merchant_id=?",
            (exchange, merchant_id)
        )
        await db._db.commit()
        return {"status": "success"}
    except Exception as e:
        return {"status": "error", "detail": str(e)}

@app.get("/api/v1/settings/global")
async def get_global_settings():
    if hasattr(state, "global_settings"):
        return dict_to_camel(jsonable_encoder(state.global_settings))
    return dict_to_camel({"min_spread": 0.5, "min_profit": 100, "max_risk_score": 50, "scan_interval": 5,
                          "active_exchanges": ["binance", "bybit"]})

@app.get("/api/v1/settings/user")
async def get_user_settings():
    if hasattr(state, "user_settings"):
        return dict_to_camel(jsonable_encoder(state.user_settings))
    return dict_to_camel(
        {"telegram_notifications": True, "telegram_chat_id": "", "sound_alerts": True, "auto_trade": None})

@app.post("/api/v1/settings/global")
async def update_global_settings(settings: dict):
    snake_settings = dict_to_snake(settings)
    if hasattr(state, "global_settings"):
        if isinstance(state.global_settings, dict):
            state.global_settings.update(snake_settings)
        else:
            for key, value in snake_settings.items():
                setattr(state.global_settings, key, value)
    return {"status": "success", "updated_settings": snake_settings}

@app.post("/api/v1/settings/user")
async def update_local_user_settings(settings: dict): # Змінено ім'я функції щоб не було конфлікту
    snake_settings = dict_to_snake(settings)
    if hasattr(state, "user_settings"):
        if isinstance(state.user_settings, dict):
            state.user_settings.update(snake_settings)
        else:
            for key, value in snake_settings.items():
                setattr(state.user_settings, key, value)
    return {"status": "success", "updated_settings": snake_settings}

@app.get("/api/v1/telegram/sync/{telegram_id}")
async def sync_telegram(telegram_id: int):
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
            # Перевіряємо, чи це вже список. Якщо так - віддаємо його, якщо рядок - сплітимо, якщо нічого - порожній список.
            "banks": user_data.get("bank_codes") if isinstance(user_data.get("bank_codes"), list) else (user_data.get("bank_codes", "").split(",") if user_data.get("bank_codes") else [])
        },
        "keys": [k.lower() for k in creds.keys()],
        "isAdmin": is_admin
    }
    return dict_to_camel(data)

@app.post("/api/v1/user/settings")
async def update_telegram_settings(settings: dict): # Змінено ім'я функції
    snake_settings = dict_to_snake(settings)
    telegram_id = settings.get("telegramUserId")

    if telegram_id:
        # Безпечний синтаксис запису до SQLite
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


@app.get("/api/v1/accounts/{telegram_id}")
async def get_real_exchange_accounts(telegram_id: int):
    # Отримуємо ключі з БД
    creds = await db.get_all_credentials(user_id=telegram_id)

    if not creds:
        return []

    accounts = []

    for exchange_name, keys in creds.items():
        api_key = keys.get("api_key", "")
        api_secret = keys.get("api_secret", "")
        passphrase = keys.get("passphrase", "")

        # Базова модель (буде перезаписана реальними даними)
        account_data = {
            "id": exchange_name.lower(),
            "exchange": exchange_name.capitalize(),
            "balanceUAH": 0.0,
            "balanceUSDT": 0.0,
            "kycLevel": "Verified",
            "merchantStatus": "None",
            "tradingVolume30d": 0,
            "volumeLimit": 100000  # Залишаємо базовий ліміт для візуалізації прогрес-бару
        }

        try:
            balances = []

            if exchange_name.lower() == "binance":
                client = BinanceAccountClient(api_key, api_secret)
                balances = await client.get_balance()
                info = await client.get_account_info()

                # Реальний KYC: якщо біржа дозволяє торгувати, значить KYC пройдено
                account_data["kycLevel"] = "Verified Plus" if info.get("canTrade") else "Unverified"

                # Реальний об'єм та статус: тягнемо останні 100 угод (50 на покупку, 50 на продаж)
                p2p_buy = await client.get_my_p2p_orders("BUY", rows=50)
                p2p_sell = await client.get_my_p2p_orders("SELL", rows=50)
                all_p2p = p2p_buy + p2p_sell

                account_data["merchantStatus"] = "Active" if len(all_p2p) > 0 else "None"
                # Рахуємо реальний об'єм за останні угоди (приблизно за 30 днів)
                account_data["tradingVolume30d"] = sum(float(o.get("amount", 0)) for o in all_p2p)


            elif exchange_name.lower() == "bybit":

                client = BybitAccountClient(api_key, api_secret)

                balances = await client.get_balance()

                # Для Bybit залишаємо заглушки на відгуки/ордери, щоб не було 403

            elif exchange_name.lower() == "okx":
                client = OKXAccountClient(api_key, api_secret, passphrase)
                balances = await client.get_funding_balance()

                # Реальні дані OKX
                info = await client.get_account_info()
                p2p_orders = await client.get_my_p2p_orders(limit=50)

                # Парсимо рівень акаунта з відповіді OKX
                acct_data = info.get("data", [{}])[0]
                account_data["kycLevel"] = f"Level {acct_data.get('acctLv', '1')}"
                account_data["merchantStatus"] = "Active" if len(p2p_orders) > 0 else "None"
                account_data["tradingVolume30d"] = sum(float(o.get("amount", 0)) for o in p2p_orders)

            elif exchange_name.lower() == "mexc":
                client = MEXCAccountClient(api_key, api_secret)
                balances = await client.get_balance()
                info = await client.get_account_info()
                account_data["kycLevel"] = "Verified"  # MEXC вимагає KYC для API

            # Парсимо знайдений баланс
            for b in balances:
                if b["coin"] == "USDT":
                    account_data["balanceUSDT"] = b["total"]
                elif b["coin"] == "UAH":
                    account_data["balanceUAH"] = b["total"]

        except Exception as e:
            logging.getLogger("Main").error(f"Помилка даних акаунта {exchange_name}: {e}")
            account_data["kycLevel"] = "API Error"
            account_data["merchantStatus"] = "API Error"

        accounts.append(account_data)

    return dict_to_camel(accounts)


if __name__ == "__main__":
    uvicorn.run("main:app", host="0.0.0.0", port=8000, reload=False)