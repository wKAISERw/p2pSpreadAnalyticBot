# core/workers/session_manager.py
import asyncio
import logging
import time
from pathlib import Path
from typing import Optional, Callable, Awaitable
from playwright.async_api import async_playwright, Request
from playwright_stealth import Stealth  # 🚀 ДОДАНО ДЛЯ МАСКУВАННЯ

from core.storage.merchant_db import MerchantDB

logger = logging.getLogger("SessionManager")

# Таргети для перехоплення з індивідуальними таймерами "протухання"
TARGETS = {
    "Bybit": {
        "url": "https://www.bybit.com/en/p2p/profile/s9260bda0f121429184f1a258ee726a9f/USDT/UAH/item",
        "api_pattern": "appraiseList",
        "ttl": 3600  # 1 година
    },
    "Binance": {
        "url": "https://c2c.binance.com/uk-UA/advertiserDetail?advertiserNo=s95b25fd3a5113bb0a054393e4289a471",
        "api_pattern": "review/list-by-page",
        "ttl": 14400  # 4 години (було 15 хв). Тепер ми покладаємось на AuthError!
    },
    "OKX": {
        "url": "https://www.okx.com/ru/p2p/ads-merchant?publicUserId=0e37a42aca",
        "api_pattern": "review/history",
        "ttl": 3600  # Поки заглушка
    }
}

# ─── Блок C: TTL сесій (секунди) ──────────────────────────────────────────
SESSION_TTL = {
    "Bybit":   72 * 3600,    # ~3 дні
    "Binance": 120 * 3600,   # ~5 днів
    "OKX":     96 * 3600,    # ~4 дні
}

# За скільки секунд до закінчення TTL надсилати попередження
SESSION_WARNING_BEFORE = 2 * 3600  # 2 години

# Інтервал перевірки health (секунди)
HEALTH_CHECK_INTERVAL = 30 * 60  # 30 хв


class SessionManager:
    def __init__(self, db: MerchantDB):
        self._db = db
        self._worker_task: Optional[asyncio.Task] = None
        self._health_task: Optional[asyncio.Task] = None
        # Callback для сповіщень (підключається з notifier)
        self._notify_callback: Optional[Callable[[str, int, str], Awaitable[None]]] = None
        # Трекінг вже надісланих попереджень (щоб не спамити)
        self._warned_sessions: set[str] = set()
        self._expired_sessions: set[str] = set()

    def set_notify_callback(self, callback: Callable[[str, int, str], Awaitable[None]]) -> None:
        """Встановлює callback для TG-сповіщень."""
        self._notify_callback = callback

    async def start(self) -> None:
        if self._worker_task and not self._worker_task.done():
            logger.debug("SessionManager вже запущено.")
            return
        self._worker_task = asyncio.create_task(self._worker_loop(), name="session-manager")
        self._health_task = asyncio.create_task(self._health_check_loop(), name="session-health")
        logger.info("🤖 SessionManager запущено (Фоновий збір браузерних сесій з маскуванням)")
        logger.info("🩺 Session Health Monitor запущено (інтервал=%dхв)", HEALTH_CHECK_INTERVAL // 60)

    async def stop(self) -> None:
        for task in [self._worker_task, self._health_task]:
            if task:
                task.cancel()
                try:
                    await task
                except asyncio.CancelledError:
                    pass
        self._worker_task = None
        self._health_task = None
        logger.info("SessionManager зупинено")

    async def _worker_loop(self):
        # Даємо сканеру 10 секунд на старт
        await asyncio.sleep(10)

        while True:
            try:
                now = time.time()
                for exchange, target in TARGETS.items():
                    _, _, updated_at = await self._db.get_auth_session(exchange)

                    if now - updated_at > target["ttl"]:
                        logger.info(f"🔄 SessionManager: Оновлення сесії {exchange} у фоні...")
                        await self._capture_session(exchange, target)
                        # Робимо паузу 5 секунд між біржами
                        await asyncio.sleep(5)

            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"❌ SessionManager помилка в циклі: {e}", exc_info=True)

            await asyncio.sleep(30)

    async def _capture_session(self, exchange: str, target: dict):
        user_data_dir = Path(f"data/browser_profiles/{exchange.lower()}")
        user_data_dir.mkdir(parents=True, exist_ok=True)
        captured_event = asyncio.Event()

        try:
            async with async_playwright() as p:
                # 🚀 ДОДАНО АНТИ-ДЕТЕКТ АРГУМЕНТИ
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

                # 🚀 НОВИЙ СИНТАКСИС ДЛЯ PLAYWRIGHT-STEALTH 2.0.2+
                stealth_plugin = Stealth()
                await stealth_plugin.apply_stealth_async(context)

                page = await context.new_page()

                async def handle_request(request: Request):
                    if target["api_pattern"] in request.url:
                        logger.debug(f"🎯 ПЕРЕХОПЛЕНО {exchange}: {request.url}")

                        headers = request.headers
                        cookies_list = await context.cookies()
                        cookies_dict = {c["name"]: c["value"] for c in cookies_list}

                        success = await self._db.save_auth_session(exchange, headers, cookies_dict)
                        if success:
                            logger.info(f"✅ SessionManager: Сесію {exchange} успішно подовжено!")
                        captured_event.set()

                page.on("request", handle_request)

                # Даємо браузеру цілих 60 секунд на завантаження важкої сторінки Bybit
                # Шукай в кінці методу _capture_session (~145)
                # ТЕПЕР ЦЕЙ БЛОК ВСЕРЕДИНІ 'async with'
                try:
                    await page.goto(target["url"], wait_until="commit", timeout=60000)
                    
                    # 🚀 ДОДАНО: Даємо сторінці (SPA) час на стабілізацію та рендер JS
                    if exchange == "Binance":
                        await page.wait_for_timeout(7000)
                    else:
                        await page.wait_for_timeout(6000)  # Даємо 6 секунд на рендер JS (до 4 сек на OKX)
                    
                    # Щоб уникнути кліків по хлібних крихтах чи неробочих 'Span' – 
                    # інжектимо JS, який знаходить УСІ елементи зі словом Відгуки/Отзывы і клікає їх
                    success = await page.evaluate('''() => {
                        const keywords = ["відгуки", "отзывы", "review", "feedback"];
                        const els = Array.from(document.querySelectorAll('div, span, a, button, li'));
                        let clicked = false;
                        for (let el of els) {
                            if (el.innerText && keywords.some(k => el.innerText.toLowerCase().includes(k))) {
                                // Фільтруємо за наявністю розміру екрану і чи не є він занадто великим (щоб не клікати весь body/header)
                                if (el.offsetWidth > 0 && el.offsetHeight > 0 && el.offsetWidth < 500) {
                                    el.click();
                                    clicked = true;
                                }
                            }
                        }
                        return clicked;
                    }''')
                    
                    if success:
                        logger.info(f"👉 SessionManager: Автоматично натиснуто вкладку відгуків для {exchange} (через JS-масив)")
                    else:
                        logger.error(f"❌ SessionManager: Жодного елемента 'Відгуки' не знайдено на екрані {exchange}!")

                    # Чекаємо поки перехопиться API запит
                    await asyncio.wait_for(captured_event.wait(), timeout=30.0)
                except Exception as e:
                    logger.error(f"❌ SessionManager помилка завантаження {exchange}: {e}")

                await asyncio.sleep(1)
                await context.close()

        except asyncio.TimeoutError:
            logger.warning(f"⚠️ SessionManager: Таймаут {exchange}. Можливо, розлогінило або вилізла капча.")
            logger.warning(
                f"👉 Запусти вручну 'python scripts/session_interceptor.py' для відновлення логіну {exchange}.")
        except Exception as e:
            logger.error(f"❌ SessionManager помилка для {exchange}: {e}")

    # ═══════════════════════════════════════════════════════════════════════
    # Блок C: Session Health Monitor
    # ═══════════════════════════════════════════════════════════════════════

    async def _health_check_loop(self):
        """
        Фоновий цикл перевірки живості сесій:
        - Перевіряє вік кожної сесії vs SESSION_TTL
        - За 2 години до закінчення → TG-попередження
        - Після закінчення → TG-повідомлення "протухла"
        - Bybit auto-refresh: легкий API запит для продовження
        """
        await asyncio.sleep(30)  # Даємо системі прогрітися

        while True:
            try:
                await self._check_all_sessions()
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"❌ Session Health Check помилка: {e}", exc_info=True)

            await asyncio.sleep(HEALTH_CHECK_INTERVAL)

    async def _check_all_sessions(self):
        """Перевіряє всі активні сесії та їхні TTL."""
        sessions = await self._db.get_all_auth_sessions()

        for session in sessions:
            exchange = session.get("exchange", "")
            user_id = session.get("user_id", 0)
            updated_at = float(session.get("updated_at", 0))
            is_active = session.get("is_active", 1)

            if not is_active or not exchange:
                continue

            ttl = SESSION_TTL.get(exchange, 96 * 3600)  # fallback 4 дні
            age = time.time() - updated_at
            remaining = ttl - age
            session_key = f"{user_id}:{exchange}"

            # Bybit auto-refresh: спробувати продовжити сесію легким запитом
            if exchange == "Bybit" and 0 < remaining < SESSION_WARNING_BEFORE:
                refreshed = await self._try_bybit_refresh(user_id)
                if refreshed:
                    # Скидаємо попередження
                    self._warned_sessions.discard(session_key)
                    self._expired_sessions.discard(session_key)
                    continue

            # Сесія протухла
            if remaining <= 0:
                if session_key not in self._expired_sessions:
                    self._expired_sessions.add(session_key)
                    msg = (
                        f"❌ <b>Сесія {exchange} протухла!</b>\n"
                        f"Бот більше не може перевіряти твої угоди чи відгуки.\n\n"
                        f"Вік: {age / 3600:.1f}г (TTL: {ttl / 3600:.0f}г)"
                    )
                    logger.warning(msg)
                    await self._send_notification(msg, user_id, exchange)

                    # Інвалідуємо сесію в БД
                    await self._db.invalidate_auth_session(exchange, user_id)

            # Попередження за 2 години до закінчення
            elif remaining < SESSION_WARNING_BEFORE:
                if session_key not in self._warned_sessions:
                    self._warned_sessions.add(session_key)
                    hours_left = remaining / 3600
                    msg = (
                        f"⚠️ <b>Сесія {exchange} спливає через {hours_left:.1f} години!</b>\n"
                        f"Вік: {age / 3600:.1f}г з {ttl / 3600:.0f}г"
                    )
                    logger.warning(msg)
                    await self._send_notification(msg, user_id, exchange)

            else:
                # Сесія OK — скидаємо трекери
                self._warned_sessions.discard(session_key)
                self._expired_sessions.discard(session_key)

    async def _try_bybit_refresh(self, user_id: int = 0) -> bool:
        """
        Bybit auto-refresh: робимо легкий API запит (get_pending_orders).
        Якщо OK — оновлюємо updated_at в auth_sessions.
        """
        try:
            from infrastructure.http.bybit_p2p_client import BybitP2PClient

            creds = await self._db.get_credentials("Bybit", user_id)
            if not creds:
                return False

            client = BybitP2PClient()
            client.set_credentials(creds.get("api_key", ""), creds.get("api_secret", ""))

            # Легкий запит — якщо проходить, сесія жива
            orders = await client.get_pending_orders()
            # orders може бути [] — це нормально (немає активних ордерів)

            # Оновлюємо updated_at — сесія продовжена
            headers, cookies, _ = await self._db.get_auth_session("Bybit", user_id)
            if headers or cookies:
                await self._db.save_auth_session("Bybit", headers, cookies, user_id)
                logger.info(f"🔄 Bybit сесія авто-оновлена через API ping (user_id={user_id})")
                return True

            return False
        except Exception as e:
            logger.debug(f"Bybit auto-refresh failed: {e}")
            return False

    async def _send_notification(self, message: str, user_id: int = 0, exchange: str = "") -> None:
        """Відправляє сповіщення через callback (якщо встановлено)."""
        if self._notify_callback:
            try:
                await self._notify_callback(message, user_id, exchange)
            except Exception as e:
                logger.error(f"Session health notification error: {e}")

