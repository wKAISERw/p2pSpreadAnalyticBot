import asyncio
import logging
from pathlib import Path
from playwright.async_api import async_playwright, Request
from playwright_stealth import Stealth  # без нього рядок apply_stealth_async падає з NameError
from core.storage.merchant_db import MerchantDB

logging.basicConfig(level=logging.INFO, format="%(asctime)s | %(levelname)s | %(message)s")
logger = logging.getLogger("Interceptor")

# Таргети для перехоплення.
# Ми використовуємо реальні лінки на профілі мерчантів, щоб змусити фронтенд біржі завантажити відгуки.
TARGETS = {
    "Bybit": {
        "url": "https://www.bybit.com/en/p2p/profile/s9260bda0f121429184f1a258ee726a9f/USDT/UAH/item",
        "api_pattern": "appraiseList",  # 🚀 ВИПРАВЛЕНО ТУТ
    },
    "Binance": {
        "url": "https://c2c.binance.com/uk-UA/advertiserDetail?advertiserNo=s95b25fd3a5113bb0a054393e4289a471",
        "api_pattern": "review/list-by-page", # 🚀 Твій точний ендпоінт
    },
    "OKX": {
        "url": "https://www.okx.com/ru/p2p/ads-merchant?publicUserId=0e37a42aca",
        "api_pattern": "review/history",
    }
}


async def run_interceptor(exchange: str):
    target = TARGETS.get(exchange)
    if not target:
        logger.error(f"Невідома біржа {exchange}")
        return

    db = MerchantDB()
    await db.start()

    # Створюємо папку для збереження профілю браузера (щоб не логінитись щоразу)
    user_data_dir = Path(f"data/browser_profiles/{exchange.lower()}")
    user_data_dir.mkdir(parents=True, exist_ok=True)

    captured_event = asyncio.Event()

    async with async_playwright() as p:
        logger.info(f"🚀 Запуск браузера для {exchange}...")

        # Запускаємо Persistent Context.
        # headless=False робить браузер видимим, щоб ти міг пройти логін.
        # Шукай цей рядок (~120)
        context = await p.chromium.launch_persistent_context(
            user_data_dir=str(user_data_dir),
            headless=False,  # 🚀 ЗМІНИ НА False (вікно буде з'являтися на 10-15 сек)
            args=[
                "--disable-blink-features=AutomationControlled",
                "--disable-http2",
                "--window-size=1280,720",
            ],
        )
        page = await context.new_page()

        # 🚀 ДОДАЙ ЦЕ: Блокуємо важкі ресурси для швидкості
        await page.route("**/*.{png,jpg,jpeg,svg,woff2,css}", lambda route: route.abort())

        # Далі йде твій stealth...
        stealth_plugin = Stealth()
        await stealth_plugin.apply_stealth_async(context)

        async def handle_request(request: Request):
            # Якщо запит йде до нашого P2P-ендпоінту з відгуками
            if target["api_pattern"] in request.url:
                logger.info(f"🎯 ПЕРЕХОПЛЕНО ЗАПИТ: {request.url}")

                # Забираємо всі секретні заголовки (Risktoken, X-Client-Signature, Csrftoken)
                headers = request.headers

                # Забираємо всі кукіси з сесії браузера
                cookies_list = await context.cookies()
                cookies_dict = {c["name"]: c["value"] for c in cookies_list}

                # Зберігаємо у твою нову таблицю auth_sessions
                success = await db.save_auth_session(exchange, headers, cookies_dict)
                if success:
                    logger.info(f"✅ Сесію {exchange} успішно збережено в БД!")
                else:
                    logger.error("❌ Помилка збереження в БД.")

                # Даємо сигнал, що місію виконано
                captured_event.set()

        # Підключаємо нашого "шпигуна" до всіх мережевих запитів сторінки
        page.on("request", handle_request)

        logger.info(f"🌐 Перехід на профіль мерчанта {exchange}...")
        await page.goto(target["url"], wait_until="domcontentloaded")

        logger.info("⏳ Очікування завантаження JS та відправки запиту до відгуків...")

        try:
            # Чекаємо 60 секунд. Якщо ти залогінений — запит полетить миттєво.
            await asyncio.wait_for(captured_event.wait(), timeout=60.0)
        except asyncio.TimeoutError:
            logger.warning("⚠️ Таймаут 60с. Можливо, потрібно залогінитись вручну або пройти капчу.")
            logger.warning(
                "Браузер залишається відкритим. Авторизуйся, клікни на вкладку 'Відгуки', і скрипт зловить запит.")
            # Чекаємо нескінченно, поки ти не пройдеш перевірку ручками
            await captured_event.wait()

        logger.info("🛑 Перехоплення успішне. Закриваємо браузер за 3 секунди...")
        await asyncio.sleep(3)
        await context.close()

    await db.stop()


if __name__ == "__main__":
    # Почнемо з Bybit як з найоптимальнішого кандидата
    asyncio.run(run_interceptor("Binance"))
    asyncio.run(run_interceptor("Bybit"))