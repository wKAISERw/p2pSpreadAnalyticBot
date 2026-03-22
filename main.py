import re
import asyncio
import logging
import sys
import os
from logging.handlers import RotatingFileHandler
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.encoders import jsonable_encoder  # <--- Додано для безпечної конвертації об'єктів
import uvicorn

# Імпортуємо компоненти нашої системи
from bot.notifier import TelegramNotifier
from scanner import run_scanner
from state import state  # Імпортуємо наш глобальний стан


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
    # Перетворює minCapital -> min_capital
    return re.sub(r'(?<!^)(?=[A-Z])', '_', camel_str).lower()


def dict_to_snake(obj):
    if isinstance(obj, list):
        return [dict_to_snake(item) for item in obj]
    elif isinstance(obj, dict):
        return {to_snake(k): dict_to_snake(v) for k, v in obj.items()}
    return obj


def setup_logging():
    """Налаштовує 3 канали логування: консоль, debug.log, error.log"""
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


# Глобальні змінні для фонових задач
scanner_task = None
notifier = None
stop_event = asyncio.Event()


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Керує життєвим циклом додатку (запуск та зупинка фонових задач)"""
    global scanner_task, notifier, stop_event

    setup_logging()
    logger = logging.getLogger("Main")
    logger.info("🚀 Ініціалізація P2P Сканера + API (Production Mode)...")

    notifier = TelegramNotifier()
    await notifier.start()

    async def run_scanner_safe():
        try:
            await run_scanner(notifier, stop_event)
        except Exception as e:
            logger.critical("🔥 КРИТИЧНА ПОМИЛКА СКАНЕРА: %s", e, exc_info=True)
            stop_event.set()

    # Запускаємо сканер у фоні
    scanner_task = asyncio.create_task(run_scanner_safe())

    yield  # ТУТ ПРАЦЮЄ FASTAPI ТА ОБРОБЛЯЄ ЗАПИТИ ВІД REACT

    # --- GRACEFUL SHUTDOWN ---
    logger.info("🧹 Початок завершення процесів...")
    stop_event.set()

    if scanner_task:
        scanner_task.cancel()
        try:
            await asyncio.wait_for(scanner_task, timeout=5.0)
        except (asyncio.CancelledError, asyncio.TimeoutError):
            pass

    if notifier:
        try:
            await asyncio.wait_for(notifier.stop(), timeout=10.0)
            logger.info("✅ Telegram Notifier успішно зупинено.")
        except asyncio.TimeoutError:
            logger.error("⚠️ Timeout при зупинці нотифікатора.")

    logger.info("🏁 Систему повністю зупинено. До зустрічі!")


# --- ІНІЦІАЛІЗАЦІЯ FASTAPI ---
app = FastAPI(title="Arbix Quantum API", lifespan=lifespan)

# Дозволяємо React-фронтенду робити запити до нашого API
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# --- GET ЕНДПОІНТИ (Віддаємо на фронтенд у camelCase) ---

@app.get("/api/v1/stats")
async def get_stats():
    # jsonable_encoder перетворює об'єкти/класи у словники, а dict_to_camel робить ключі зручними для React
    return dict_to_camel(jsonable_encoder(state.stats))


@app.get("/api/v1/opportunities")
async def get_opportunities():
    # Беремо спреди зі state замість неіснуючої змінної
    return dict_to_camel(jsonable_encoder(state.opportunities))


@app.get("/api/v1/logs")
async def get_logs():
    return dict_to_camel(jsonable_encoder(state.logs))


@app.get("/api/v1/blacklist")
async def get_blacklist():
    return dict_to_camel(jsonable_encoder(state.blacklist))


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


# --- POST ЕНДПОІНТИ (Отримуємо з фронтенда, перетворюємо у snake_case і зберігаємо) ---

@app.post("/api/v1/settings/global")
async def update_global_settings(settings: dict):
    # Фронт прислав camelCase, перекладаємо на python-стандарт
    snake_settings = dict_to_snake(settings)

    if hasattr(state, "global_settings"):
        if isinstance(state.global_settings, dict):
            state.global_settings.update(snake_settings)
        else:
            # Якщо це об'єкт класу
            for key, value in snake_settings.items():
                setattr(state.global_settings, key, value)

    return {"status": "success", "updated_settings": snake_settings}


@app.post("/api/v1/settings/user")
async def update_user_settings(settings: dict):
    snake_settings = dict_to_snake(settings)

    if hasattr(state, "user_settings"):
        if isinstance(state.user_settings, dict):
            state.user_settings.update(snake_settings)
        else:
            for key, value in snake_settings.items():
                setattr(state.user_settings, key, value)

    return {"status": "success", "updated_settings": snake_settings}


if __name__ == "__main__":
    uvicorn.run("main:app", host="0.0.0.0", port=8000, reload=False)