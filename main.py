import asyncio
import logging
import signal
import sys
import os
from logging.handlers import RotatingFileHandler

# Імпортуємо компоненти нашої системи
from bot.notifier import TelegramNotifier
from scanner import run_scanner


def setup_logging():
    """Налаштовує 3 канали логування: консоль, debug.log, error.log"""
    # Створюємо папку для логів, якщо її немає
    os.makedirs("logs", exist_ok=True)

    logger = logging.getLogger()
    logger.setLevel(logging.DEBUG)  # Глобальний рівень

    formatter = logging.Formatter("%(asctime)s | %(levelname)s | %(name)s | %(message)s")

    # 1. Console (INFO) - виводимо в термінал тільки важливе
    console_handler = logging.StreamHandler(sys.stdout)
    console_handler.setLevel(logging.INFO)
    console_handler.setFormatter(formatter)

    # 2. Debug Log (DEBUG) - пишемо все підряд (з ротацією по 5 МБ, зберігаємо 2 бекапи)
    debug_handler = RotatingFileHandler(
        "logs/debug.log", maxBytes=5 * 1024 * 1024, backupCount=2, encoding="utf-8"
    )
    debug_handler.setLevel(logging.DEBUG)
    debug_handler.setFormatter(formatter)

    # 3. Error Log (ERROR) - окремий файл тільки для помилок
    error_handler = RotatingFileHandler(
        "logs/error.log", maxBytes=5 * 1024 * 1024, backupCount=2, encoding="utf-8"
    )
    error_handler.setLevel(logging.ERROR)
    error_handler.setFormatter(formatter)

    logger.addHandler(console_handler)
    logger.addHandler(debug_handler)
    logger.addHandler(error_handler)


async def main():
    setup_logging()
    logger = logging.getLogger("Main")
    logger.info("🚀 Ініціалізація P2P Сканера (Production Mode)...")

    stop_event = asyncio.Event()
    notifier = TelegramNotifier()

    # Функція для перехоплення сигналів ОС (SIGINT, SIGTERM)
    def handle_signal(sig):
        logger.warning("🛑 Отримано сигнал %s. Ініціалізація graceful shutdown...", sig.name)
        stop_event.set()

    # Реєструємо обробники сигналів (тільки для Unix-подібних систем, якими є всі VPS)
    loop = asyncio.get_running_loop()
    if sys.platform != "win32":
        for sig in (signal.SIGINT, signal.SIGTERM):
            loop.add_signal_handler(sig, handle_signal, sig)

    await notifier.start()

    # Безпечна обгортка, яка не дасть помилкам зникнути мовчки
    async def run_scanner_safe():
        try:
            await run_scanner(notifier, stop_event)
        except Exception as e:
            logger.critical("🔥 КРИТИЧНА ПОМИЛКА СКАНЕРА: %s", e, exc_info=True)
            stop_event.set()  # Зупиняємо всю програму, якщо сканер впав

    # Запускаємо через нашу обгортку
    scanner_task = asyncio.create_task(run_scanner_safe())

    try:
        # Блокуємо виконання, поки не спрацює stop_event (через сигнал або помилку)
        await stop_event.wait()
    except KeyboardInterrupt:
        logger.warning("🛑 Отримано KeyboardInterrupt. Зупинка...")
        stop_event.set()
    finally:
        logger.info("🧹 Початок завершення процесів...")

        # Скасовуємо таску сканера, якщо вона ще працює
        scanner_task.cancel()
        try:
            # Даємо сканеру час на коректне завершення
            await asyncio.wait_for(scanner_task, timeout=5.0)
        except asyncio.CancelledError:
            pass
        except asyncio.TimeoutError:
            logger.error("⚠️ Сканер не зупинився вчасно (Timeout).")
        except Exception as e:
            logger.error("❌ Помилка під час зупинки сканера: %s", e)

        try:
            # Graceful shutdown нотифікатора (чекаємо відправки останніх повідомлень)
            await asyncio.wait_for(notifier.stop(), timeout=10.0)
            logger.info("✅ Telegram Notifier успішно зупинено.")
        except asyncio.TimeoutError:
            logger.error("⚠️ Timeout при зупинці нотифікатора. Можлива втрата повідомлень з черги.")

        logger.info("🏁 Систему повністю зупинено. До зустрічі!")


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        # Pass, оскільки ми вже обробили це всередині main()
        pass