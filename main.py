2# main.py
import asyncio
import logging
import sys
import os
from logging.handlers import RotatingFileHandler
from contextlib import asynccontextmanager

from fastapi import FastAPI, Response
from fastapi.middleware.cors import CORSMiddleware
import uvicorn
from prometheus_client import generate_latest, CONTENT_TYPE_LATEST

from api.security import warn_if_unprotected
from bot.handlers.core import setup as bot_setup
from bot.handlers import get_router as get_bot_router
from bot.notifier import TelegramNotifier
from config import settings
from core.storage.merchant_db import MerchantDB
from core.workers.db_maintenance import DBMaintenanceTask
from core.workers.card_sync import CardBalanceSyncTask
from scanner import run_scanner

# Імпортуємо чисті модульні роутери нашого власного API сервера
from api.routers import (
    dashboard_router, webhooks_router, control_router, auth_router, personal_router,
)

db = MerchantDB()   


class StateLogHandler(logging.Handler):
    """
    Складає останні записи логу в state.logs — саме звідти читає /api/v1/logs.
    Раніше цей буфер не наповнювався ніде, тож ендпоінт завжди віддавав [].
    """

    def emit(self, record: logging.LogRecord) -> None:
        try:
            from state import state
            state.logs.append({
                "timestamp": int(record.created * 1000),
                "level": record.levelname,
                "source": record.name,
                "message": record.getMessage()[:1000],
            })
        except Exception:  # логер не має права ламати застосунок
            pass


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

    state_handler = StateLogHandler()
    state_handler.setLevel(logging.INFO)

    logger.addHandler(console_handler)
    logger.addHandler(debug_handler)
    logger.addHandler(error_handler)
    logger.addHandler(state_handler)


# Глобальні змінні оркестрації
scanner_task = None
notifier = None
stop_event = asyncio.Event()
db_maintainer = None
card_sync_task = None


@asynccontextmanager
async def lifespan(app: FastAPI):
    global scanner_task, notifier, stop_event, db_maintainer, card_sync_task

    setup_logging()
    logger = logging.getLogger("Main")
    logger.info("🚀 Ініціалізація P2P Сканера + API (Production Mode)...")

    # Гучно кажемо, якщо HTTP API лишився без ключа.
    warn_if_unprotected()

    # 1. Запуск БД та підключення до синглтону хендлерів боту
    await db.start()
    logger.info("✅ База даних успішно підключена для API")

    # Краще не стартувати, ніж стартувати з нечитабельними креденшлами:
    # мовчазна деградація тут призводить до торгівлі з чужого акаунта.
    try:
        await db.verify_encryption_key()
    except Exception as key_err:
        logger.critical("🔑 %s", key_err)
        raise SystemExit(1) from key_err

    # 2. Ініціалізація обслуговування бази
    db_maintainer = DBMaintenanceTask(db)
    db_maintainer.start()

    # 2.1. Ініціалізація фонового оновлення балансів карток
    card_sync_task = CardBalanceSyncTask(db)
    card_sync_task.start()

    # 3. Ініціалізація та зв'язування компонентів Telegram
    notifier = TelegramNotifier()
    notifier.bind_db(db)

    # Реєструємо залежності в ядрі хендлерів боту
    bot_setup(db=db, account_clients={}, notifier=notifier, bot=notifier._bot)

    # Інтегруємо наш новий модульний роутер меню в єдиний Dispatcher
    notifier._dp.include_router(get_bot_router())

    await notifier.start()

    async def run_scanner_safe():
        try:
            await run_scanner(notifier, stop_event, shared_db=db)
        except Exception as e:
            logger.critical("🔥 КРИТИЧНА ПОМИЛКА СКАНЕРА: %s", e, exc_info=True)
            stop_event.set()

    scanner_task = asyncio.create_task(run_scanner_safe())

    yield

    # --- GRACEFUL SHUTDOWN (БЕЗПЕЧНА ЗУПИНКА) ---
    logger.info("🧹 Початок завершення процесів...")
    stop_event.set()

    if scanner_task:
        scanner_task.cancel()
        try:
            await asyncio.wait_for(scanner_task, timeout=5.0)
        except (asyncio.CancelledError, asyncio.TimeoutError):
            pass

    if card_sync_task:
        await card_sync_task.stop()

    if db_maintainer:
        await db_maintainer.stop()

    if notifier:
        try:
            await asyncio.wait_for(notifier.stop(), timeout=10.0)
            logger.info("✅ Telegram Notifier успішно зупинено.")
        except asyncio.TimeoutError:
            logger.error("⚠️ Timeout при зупинці нотифікатора.")

    await db.stop()
    logger.info("🏁 Систему повністю зупинено. До зустрічі!")


app = FastAPI(title="Arbix Quantum API", lifespan=lifespan)

# CORS MIDDLEWARE
_cors_origins = list(getattr(settings, "cors_origins", ["*"]) or ["*"])
app.add_middleware(
    CORSMiddleware,
    allow_origins=_cors_origins,
    # Wildcard сумісний тільки з credentials=False за специфікацією CORS.
    # Автентифікація йде заголовком X-API-Key, кукі нам не потрібні.
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)

# 📈 Prometheus /metrics endpoint
@app.get("/metrics", include_in_schema=False)
async def prometheus_metrics():
    """Expose Prometheus metrics for scraping."""
    return Response(
        content=generate_latest(),
        media_type=CONTENT_TYPE_LATEST,
    )

# 🚀 ПІД КАТАЛОГ ОДНОЧАСНО ПІДКЛЮЧАЄМО ВСІ НАШІ БОЙОВІ РОУТЕРИ
app.include_router(dashboard_router)
app.include_router(webhooks_router)
app.include_router(control_router)
app.include_router(auth_router)
app.include_router(personal_router)

if __name__ == "__main__":
    uvicorn.run("main:app", host="0.0.0.0", port=8000, reload=False)