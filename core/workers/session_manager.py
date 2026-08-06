# core/workers/session_manager.py
import asyncio
from contextlib import suppress
import logging
import os
import time
from pathlib import Path
from typing import Optional, Callable, Awaitable
from playwright.async_api import async_playwright, Request
from playwright_stealth import Stealth  # 🚀 ДОДАНО ДЛЯ МАСКУВАННЯ
from aiogram.types import FSInputFile, InlineKeyboardMarkup, InlineKeyboardButton, InputMediaPhoto, Message
from aiogram.fsm.context import FSMContext
from bot.handlers.core import QRStates

from core.storage.merchant_db import MerchantDB
from core.utils.tasks import spawn

logger = logging.getLogger("SessionManager")

DEFAULT_USER_AGENT = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"

# Таргети для перехоплення з індивідуальними таймерами "протухання"
# ttl = через скільки секунд ПІСЛЯ оновлення запускати Playwright-перехоплення.
# Значення мають бути МЕНШЕ SESSION_TTL але достатньо великі, щоб не спамити.
TARGETS = {
    "Bybit": {
        # P2P market URL — гарантовано тригерить appraiseList (на відміну від профілю мерчанта
        # який може рендеритись пустим після QR redirect через SPA lazy-loading)
        "url": "https://www.bybit.com/en/p2p/trade/buy/USDT/?paymentMethod=&fiatCurrency=UAH",
        # Fallback: профіль мерчанта якщо market не спрацював
        "fallback_url": "https://www.bybit.com/en/p2p/profile/s9260bda0f121429184f1a258ee726a9f/USDT/UAH/item",
        "api_pattern": "appraiseList",
        "ttl": 48 * 3600   # 48 годин — перехоплення лише якщо сесія стара >48г
    },
    "Binance": {
        "url": "https://c2c.binance.com/uk-UA/advertiserDetail?advertiserNo=s95b25fd3a5113bb0a054393e4289a471",
        "api_pattern": "review/list-by-page",
        "ttl": 72 * 3600  # 72 години — JWT живе 5 днів, оновлюємо за 2 дні до кінця
    },
    "OKX": {
        "url": "https://www.okx.com/ru/p2p/ads-merchant?publicUserId=0e37a42aca",
        "api_pattern": "review/history",
        "ttl": 72 * 3600  # 72 години — JWT живе 14 днів, Playwright тільки якщо справді треба
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
        self._locks = {}
        self._active_qr_sessions = {}

    def _get_lock(self, exchange: str) -> asyncio.Lock:
        if exchange not in self._locks:
            self._locks[exchange] = asyncio.Lock()
        return self._locks[exchange]

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

    async def trigger_headed_capture(self, exchange: str) -> None:
        """Запускає оновлення сесії з видимим вікном (headless=False) для ручної авторизації."""
        target = TARGETS.get(exchange)
        if not target:
            logger.error(f"Невідома біржа для ручного захоплення: {exchange}")
            return
        logger.info(f"🖥 Запуск видимого браузера (headed) для сесії {exchange}...")
        spawn(
            self._capture_session(exchange, target, headless=False),
            f"session-capture-headed-{exchange}",
            logger_=logger,
        )

    async def _worker_loop(self):
        # Даємо сканеру 10 секунд на старт
        await asyncio.sleep(10)

        while True:
            try:
                from config.runtime import runtime_config
                require_sessions = runtime_config.get("require_sessions", "true") == "true"
                if require_sessions:
                    # Не запускаємо фоновий Playwright якщо активна QR-сесія
                    # (паралельні Playwright-контексти заважають один одному)
                    if self._active_qr_sessions:
                        logger.debug(
                            "SessionManager: є активна QR-сесія (%s), пропускаємо фонове оновлення",
                            list(self._active_qr_sessions.keys())
                        )
                    else:
                        now = time.time()
                        for exchange, target in TARGETS.items():
                            _, _, updated_at = await self._db.get_auth_session(exchange)

                            if updated_at > 0 and now - updated_at > target["ttl"]:
                                # Спочатку пробуємо легкий API-піng без браузера
                                refreshed = await self._try_lightweight_refresh(exchange)
                                if refreshed:
                                    logger.info(
                                        "🔄 SessionManager: Сесія %s продовжена API-пінгом (без браузера)",
                                        exchange
                                    )
                                    continue

                                # Піng не вдався — сесія справді протухла, запускаємо Playwright
                                logger.info(
                                    "🔄 SessionManager: Оновлення сесії %s через Playwright...",
                                    exchange
                                )
                                await self._capture_session(exchange, target)
                                # Робимо паузу 5 секунд між біржами
                                await asyncio.sleep(5)

            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"❌ SessionManager помилка в циклі: {e}", exc_info=True)

            await asyncio.sleep(30)

    async def _capture_session(self, exchange: str, target: dict, headless: Optional[bool] = None):
        user_data_dir = Path(f"data/browser_profiles/{exchange.lower()}")
        user_data_dir.mkdir(parents=True, exist_ok=True)

        # 🧹 Прибираємо старі залишкові файли блокування Chromium (SingletonLock)
        for lock_name in ["SingletonLock", "SingletonSocket", "SingletonCookie"]:
            lock_file = user_data_dir / lock_name
            if lock_file.exists() or lock_file.is_symlink():
                try:
                    lock_file.unlink(missing_ok=True)
                except Exception:
                    pass

        captured_event = asyncio.Event()

        lock = self._get_lock(exchange)
        if lock.locked():
            logger.warning(f"Session capture for {exchange} is already running. Skipping.")
            return

        async with lock:
            if headless is None:
                headless = True

            try:
                async with async_playwright() as p:
                    try:
                        context = await p.chromium.launch_persistent_context(
                            user_data_dir=str(user_data_dir),
                            headless=headless,
                            channel="chrome",
                            user_agent=DEFAULT_USER_AGENT,
                            ignore_default_args=["--enable-automation"],
                            args=[
                                "--disable-blink-features=AutomationControlled",
                                "--window-size=1280,720",
                            ],
                        )
                    except Exception as chrome_err:
                        logger.info(f"Failed to launch Chrome channel: {chrome_err}. Falling back to default Chromium.")
                        try:
                            context = await p.chromium.launch_persistent_context(
                                user_data_dir=str(user_data_dir),
                                headless=headless,
                                user_agent=DEFAULT_USER_AGENT,
                                ignore_default_args=["--enable-automation"],
                                args=[
                                    "--disable-blink-features=AutomationControlled",
                                    "--window-size=1280,720",
                                ],
                            )
                        except Exception as launch_err:
                            if not headless:
                                logger.warning(f"Failed to launch headed browser ({launch_err}). Falling back to headless mode on VPS...")
                                context = await p.chromium.launch_persistent_context(
                                    user_data_dir=str(user_data_dir),
                                    headless=True,
                                    user_agent=DEFAULT_USER_AGENT,
                                    ignore_default_args=["--enable-automation"],
                                    args=[
                                        "--disable-blink-features=AutomationControlled",
                                        "--window-size=1280,720",
                                    ],
                                )
                            else:
                                raise launch_err

                    stealth_plugin = Stealth()
                    await stealth_plugin.apply_stealth_async(context)

                    # ФІКС 1: Беремо вже існуючу першу вкладку замість створення нової
                    page = context.pages[0] if context.pages else await context.new_page()

                    # ФІКС 2: Прибрали .css з блокування (щоб JS-фреймворки не крашились)
                    await page.route("**/*.{png,jpg,jpeg,svg,woff2}", lambda route: route.abort())

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

                    # Bybit: P2P SPA не рендерується в Playwright — використовуємо browser fetch()
                    # щоб тригернути API запит з авторизованого браузера
                    if exchange == "Bybit":
                        try:
                            # Спочатку переходимо на bybit.com щоб були правильні cookies для cross-origin fetch
                            await page.goto("https://www.bybit.com/en/dashboard", wait_until="commit", timeout=30000)
                            await page.wait_for_timeout(3000)

                            logger.info("Bybit _capture_session: тригеримо API через browser fetch()...")
                            await page.evaluate("""
                                () => {
                                    fetch('https://api2.bybit.com/fiat/otc/item/list', {
                                        method: 'POST',
                                        credentials: 'include',
                                        headers: {
                                            'Content-Type': 'application/json;charset=UTF-8',
                                            'Accept': 'application/json'
                                        },
                                        body: JSON.stringify({
                                            tokenId: 'USDT',
                                            currencyId: 'UAH',
                                            payment: [],
                                            side: '1',
                                            size: '5',
                                            page: '1',
                                            amount: ''
                                        })
                                    }).catch(() => {});
                                    fetch('https://api2.bybit.com/fiat/otc/user/feedback/appraiseList', {
                                        method: 'POST',
                                        credentials: 'include',
                                        headers: {
                                            'Content-Type': 'application/json;charset=UTF-8',
                                            'Accept': 'application/json'
                                        },
                                        body: JSON.stringify({
                                            memberId: 's9260bda0f121429184f1a258ee726a9f',
                                            evaluateType: 1,
                                            page: 1,
                                            size: 5
                                        })
                                    }).catch(() => {});
                                }
                            """)
                            await page.wait_for_timeout(4000)

                            if not captured_event.is_set():
                                logger.warning("Bybit _capture_session: fetch() не перехоплено, зберігаємо cookies напряму...")
                                cookies_list = await context.cookies()
                                cookies_dict_fb = {c["name"]: c["value"] for c in cookies_list}
                                fallback_headers = {"content-type": "application/json;charset=UTF-8"}
                                success = await self._db.save_auth_session(exchange, fallback_headers, cookies_dict_fb)
                                if success:
                                    logger.info("✅ SessionManager: Bybit сесія збережена через прямий cookie capture!")
                                captured_event.set()
                        except Exception as bybit_err:
                            logger.error(f"Bybit _capture_session fetch error: {bybit_err}")
                            try:
                                cookies_list = await context.cookies()
                                cookies_dict_fb = {c["name"]: c["value"] for c in cookies_list}
                                await self._db.save_auth_session(exchange, {}, cookies_dict_fb)
                                captured_event.set()
                                logger.info("Bybit: ultimate fallback cookies збережено")
                            except Exception:
                                pass
                    else:
                        # Для Binance та OKX: звичайна навігація на сторінку мерчанта
                        try:
                            await page.goto(target["url"], wait_until="commit", timeout=60000)

                            # 🚀 ДОДАНО: Даємо сторінці (SPA) час на стабілізацію та рендер JS
                            if exchange == "Binance":
                                await page.wait_for_timeout(7000)
                                # 🚀 ДОДАНО: Примусово тригеримо fetch() у контексті сторінки для миттєвого перехоплення
                                await page.evaluate('''() => {
                                    fetch('https://c2c.binance.com/bapi/c2c/v1/friendly/c2c/review/list-by-page', {
                                        method: 'POST',
                                        headers: { 'Content-Type': 'application/json' },
                                        body: JSON.stringify({ userNo: 's95b25fd3a5113bb0a054393e4289a471', rating: 3, page: 1, rows: 10 })
                                    }).catch(() => {});
                                }''')
                            else:
                                await page.wait_for_timeout(6000)  # Даємо 6 секунд на рендер JS (до 4 сек на OKX)
                                if exchange == "OKX":
                                    # 🚀 ДОДАНО: Примусово тригеримо fetch() у контексті сторінки для миттєвого перехоплення
                                    await page.evaluate('''() => {
                                        fetch('https://www.okx.com/v3/c2c/review/history', {
                                            method: 'POST',
                                            headers: { 'Content-Type': 'application/json' },
                                            body: JSON.stringify({ currentPage: 1, hasComment: false, pageSize: 10, reviewFromBuyer: true, reviewScoreType: "", pubUserId: "0e37a42aca" })
                                        }).catch(() => {});
                                    }''')

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

                            # Чекаємо поки перехопиться API запит (розширено до 35 сек)
                            try:
                                await asyncio.wait_for(captured_event.wait(), timeout=35.0)
                            except (asyncio.TimeoutError, asyncio.exceptions.TimeoutError):
                                # Якщо є fallback URL — пробуємо його
                                fallback_url = target.get("fallback_url")
                                if fallback_url and not captured_event.is_set():
                                    logger.warning(
                                        "⚠️ SessionManager: Таймаут на основному URL %s, пробуємо fallback: %s",
                                        exchange, fallback_url
                                    )
                                    await page.goto(fallback_url, wait_until="commit", timeout=30000)
                                    await page.wait_for_timeout(6000)
                                    await page.evaluate('''() => {
                                        const keywords = ["відгуки", "отзывы", "review", "feedback"];
                                        const els = Array.from(document.querySelectorAll('div, span, a, button, li'));
                                        for (let el of els) {
                                            if (el.innerText && keywords.some(k => el.innerText.toLowerCase().includes(k))) {
                                                if (el.offsetWidth > 0 && el.offsetHeight > 0 && el.offsetWidth < 500) {
                                                    el.click();
                                                    break;
                                                }
                                            }
                                        }
                                    }''')
                                    await asyncio.wait_for(captured_event.wait(), timeout=20.0)
                                else:
                                    raise asyncio.TimeoutError(f"{exchange}: no API request captured in time")

                        except Exception as e:
                            logger.error(f"❌ SessionManager помилка завантаження {exchange}: {e}")
                            raise e

                    await asyncio.sleep(1)
                    await context.close()

            except (asyncio.TimeoutError, asyncio.exceptions.TimeoutError) as te:
                msg = f"⚠️ <b>Помилка фонового оновлення сесії {exchange}!</b> (Таймаут).\nМожливо, ви розлогінились або вилізла капча."
                logger.warning(f"⚠️ SessionManager: Таймаут {exchange}: {te}")
                if headless:
                    await self._send_notification(msg, exchange=exchange)
                else:
                    raise te
            except Exception as e:
                msg = f"❌ <b>Помилка фонового оновлення сесії {exchange}!</b>\nДеталі: {str(e)}"
                logger.error(f"❌ SessionManager помилка для {exchange}: {e}")
                if headless:
                    await self._send_notification(msg, exchange=exchange)
                else:
                    raise e

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
        from config.runtime import runtime_config
        require_sessions = runtime_config.get("require_sessions", "true") == "true"
        if not require_sessions:
            return

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

    async def are_all_sessions_valid(self, exchanges: list[str]) -> bool:
        """Перевіряє, чи є валідні сесії для всіх вказаних бірж."""
        invalid = await self.get_invalid_sessions(exchanges)
        return len(invalid) == 0

    async def get_invalid_sessions(self, exchanges: list[str]) -> list[str]:
        """Повертає список бірж, для яких сесії відсутні або протухли."""
        invalid_exchanges = []
        sessions = await self._db.get_all_auth_sessions()
        
        # Створюємо мапу сесій для швидкого пошуку
        # Якщо в БД кілька сесій (для різних user_id), достатньо хоча б однієї живої
        active_map = {}
        for session in sessions:
            ex = session.get("exchange", "")
            updated_at = float(session.get("updated_at", 0))
            is_active = session.get("is_active", 1)
            
            if not is_active or not ex:
                continue
                
            ttl = SESSION_TTL.get(ex, 96 * 3600)
            age = time.time() - updated_at
            
            if age < ttl:
                active_map[ex] = True
        
        for ex in exchanges:
            # Для деяких бірж (наприклад MEXC або WhiteBIT) сесії не використовуються
            if ex in ["Binance", "Bybit", "OKX"]:
                if ex not in active_map:
                    invalid_exchanges.append(ex)
                    
        return invalid_exchanges

    async def _try_lightweight_refresh(self, exchange: str, user_id: int = 0) -> bool:
        """
        Легка перевірка сесії без Playwright — просто API-піng.
        Якщо сесія жива, оновлює updated_at у БД.
        Повертає True якщо сесія валідна і updated_at оновлено.
        """
        if exchange == "OKX":
            return await self._try_okx_refresh(user_id)
        elif exchange == "Bybit":
            return await self._try_bybit_refresh(user_id)
        elif exchange == "Binance":
            return await self._try_binance_refresh(user_id)
        return False

    async def _try_okx_refresh(self, user_id: int = 0) -> bool:
        """
        OKX auto-refresh: POST /v3/c2c/review/history з браузерними cookies.
        JWT токен живе ~14 днів — просто перевіряємо що він ще валідний.
        """
        try:
            import time as time_mod
            from curl_cffi.requests import AsyncSession as CurlSession

            headers_dict, cookies_dict, _ = await self._db.get_auth_session("OKX", user_id)
            if not headers_dict or not cookies_dict:
                return False

            headers_dict = {k.lower(): v for k, v in headers_dict.items()}

            auth = headers_dict.get("authorization", "")
            if not auth:
                return False

            req_headers = {
                "accept": "application/json",
                "content-type": "application/json",
                "app-type": "web",
                "x-locale": "ru_RU",
                "authorization": auth,
            }
            for k in ("devid", "x-id-group", "x-site-info", "user-agent"):
                v = headers_dict.get(k)
                if v:
                    req_headers[k] = v

            ts = int(time_mod.time() * 1000)
            url = f"https://www.okx.com/v3/c2c/review/history?t={ts}"
            payload = {
                "currentPage": 1, "hasComment": False, "pageSize": 1,
                "reviewFromBuyer": True, "reviewScoreType": "",
                "pubUserId": "0e37a42aca",  # тестовий публічний мерчант
            }

            from config import settings
            proxies = {"http": settings.proxy_url, "https": settings.proxy_url} if settings.proxy_url else None

            async with CurlSession(impersonate="chrome124", proxies=proxies) as session:
                resp = await session.post(url, json=payload,
                                         headers=req_headers, cookies=cookies_dict,
                                         timeout=8)

            if resp.status_code == 200:
                data = resp.json()
                if data.get("code") == 0:
                    # Сесія жива — оновлюємо updated_at
                    await self._db.save_auth_session("OKX", headers_dict, cookies_dict, user_id)
                    logger.info("🔄 OKX сесія авто-оновлена через API ping (без браузера)")
                    return True

            logger.debug("OKX refresh ping: code=%s status=%d",
                        resp.json().get("code") if resp.status_code == 200 else "?",
                        resp.status_code)
            return False

        except Exception as e:
            logger.debug("OKX auto-refresh failed: %s", e)
            return False

    async def _try_binance_refresh(self, user_id: int = 0) -> bool:
        """
        Binance auto-refresh: легкий GET запит до публічного ендпоінту з сесійними cookies.
        Перевіряємо що cookies ще валідні (HTTP 200 + не редірект на логін).
        """
        try:
            from curl_cffi.requests import AsyncSession as CurlSession

            headers_dict, cookies_dict, _ = await self._db.get_auth_session("Binance", user_id)
            if not headers_dict or not cookies_dict:
                return False

            headers_dict = {k.lower(): v for k, v in headers_dict.items()}

            # Перевіряємо через публічний endpoint — якщо cookies протухли, отримаємо redirect
            req_headers = {
                "accept": "application/json",
                "user-agent": headers_dict.get("user-agent",
                    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"),
                "referer": "https://c2c.binance.com/",
            }
            # Копіюємо Csrftoken та інші важливі заголовки
            for k in ("csrftoken", "bnc-uuid", "fvideo-id", "fvideo-token"):
                v = headers_dict.get(k)
                if v:
                    req_headers[k] = v

            url = "https://c2c.binance.com/bapi/c2c/v1/friendly/c2c/user/get-profile"
            from config import settings
            proxies = {"http": settings.proxy_url, "https": settings.proxy_url} if settings.proxy_url else None

            async with CurlSession(impersonate="chrome124", proxies=proxies) as session:
                resp = await session.get(url, headers=req_headers,
                                         cookies=cookies_dict, timeout=8)

            if resp.status_code == 200:
                data = resp.json()
                # Binance повертає {"code": "000000"} якщо авторизований
                if data.get("code") == "000000":
                    await self._db.save_auth_session("Binance", headers_dict, cookies_dict, user_id)
                    logger.info("🔄 Binance сесія авто-оновлена через API ping (без браузера)")
                    return True

            logger.debug("Binance refresh ping: status=%d", resp.status_code)
            return False

        except Exception as e:
            logger.debug("Binance auto-refresh failed: %s", e)
            return False

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

    async def trigger_qr_capture(self, exchange: str, user_id: int, message: Message, state: Optional[FSMContext] = None) -> bool:
        """
        Запускає фоновий процес входу через QR-код для Binance або OKX.
        message — це об'єкт повідомлення користувача (щоб надсилати скріншот).
        """
        import os
        import time
        
        session_key = f"{exchange}:{user_id}"
        
        # 1. Якщо сесія вже активна, закриваємо її
        if session_key in self._active_qr_sessions:
            existing = self._active_qr_sessions[session_key]
            created_at = existing.get("created_at", 0.0)
            if time.time() - created_at < 20:
                logger.warning(f"⚠️ Повторний запит QR-входу менш ніж через 20 секунд для {session_key}. Ігноруємо.")
                return False
            await self.cancel_qr_session(exchange, user_id)
            
        logger.info(f"🔑 Запуск QR-авторизації для {exchange} (user_id={user_id})")
        
        # Створюємо папку для QR-кодів
        qr_dir = Path("data/qr_codes")
        qr_dir.mkdir(parents=True, exist_ok=True)
        qr_path = qr_dir / f"{exchange.lower()}_{user_id}.png"
        
        # Створюємо подію для скасування
        cancel_event = asyncio.Event()
        
        # Ініціалізуємо стан у словнику
        self._active_qr_sessions[session_key] = {
            "cancel_event": cancel_event,
            "browser_context": None,
            "qr_msg": None,
            "code_queue": asyncio.Queue(),
            "created_at": time.time()
        }
        
        # Оголосимо внутрішню функцію для виконання всього потоку
        async def run_flow():
            browser_context = None
            qr_msg = None
            try:
                # Повідомляємо про старт
                status_msg = await message.answer(f"⏳ Ініціалізація браузера для {exchange}...")
                
                # На Windows за замовчуванням запускаємо headed режим (headless=False) для обходу Cloudflare/Bybit.
                # Якщо немає дисплея (наприклад, на VPS) - автоматично перемикаємось на headless.
                headless = True
                if os.name == 'nt':
                    headless = False
                    
                async with async_playwright() as p:
                    user_data_dir = Path(f"data/browser_profiles/qr_{exchange.lower()}_{user_id}")
                    user_data_dir.mkdir(parents=True, exist_ok=True)
                    
                    # 🧹 Прибираємо залишкові файли блокування Chromium (SingletonLock)
                    for lock_name in ["SingletonLock", "SingletonSocket", "SingletonCookie"]:
                        lock_file = user_data_dir / lock_name
                        if lock_file.exists() or lock_file.is_symlink():
                            try:
                                lock_file.unlink(missing_ok=True)
                            except Exception:
                                pass
                    
                    # Bybit потребує --disable-http2 (їхній сервер повертає ERR_HTTP2_PROTOCOL_ERROR)
                    browser_args = [
                        "--disable-blink-features=AutomationControlled",
                        "--window-size=1280,800",
                    ]
                    if exchange == "Bybit":
                        browser_args.append("--disable-http2")
                    
                    try:
                        browser_context = await p.chromium.launch_persistent_context(
                            user_data_dir=str(user_data_dir),
                            headless=headless,
                            channel="chrome",
                            user_agent=DEFAULT_USER_AGENT,
                            ignore_default_args=["--enable-automation"],
                            args=browser_args
                        )
                    except Exception as launch_err:
                        if not headless:
                            logger.info(f"Failed to launch headed context with Chrome: {launch_err}. Retrying with Chromium...")
                            try:
                                browser_context = await p.chromium.launch_persistent_context(
                                    user_data_dir=str(user_data_dir),
                                    headless=headless,
                                    ignore_default_args=["--enable-automation"],
                                    args=browser_args
                                )
                            except Exception as fallback_err:
                                logger.info(f"Failed to launch Chromium headed: {fallback_err}. Retrying in headless mode...")
                                headless = True
                                browser_context = await p.chromium.launch_persistent_context(
                                    user_data_dir=str(user_data_dir),
                                    headless=headless,
                                    ignore_default_args=["--enable-automation"],
                                    args=browser_args
                                )
                        else:
                            logger.info(f"Failed headless Chrome launch: {launch_err}. Retrying headless Chromium...")
                            browser_context = await p.chromium.launch_persistent_context(
                                user_data_dir=str(user_data_dir),
                                headless=headless,
                                user_agent=DEFAULT_USER_AGENT,
                                ignore_default_args=["--enable-automation"],
                                args=browser_args
                            )
                    
                    # Записуємо контекст в сесію для можливості примусового закриття
                    if session_key in self._active_qr_sessions:
                        self._active_qr_sessions[session_key]["browser_context"] = browser_context
                    else:
                        await browser_context.close()
                        return
                    
                    stealth_plugin = Stealth()
                    await stealth_plugin.apply_stealth_async(browser_context)
                    
                    page = browser_context.pages[0] if browser_context.pages else await browser_context.new_page()
                    await page.set_viewport_size({"width": 1280, "height": 800})
                    
                    # Налаштовуємо перехоплення API запитів (cookies/headers)
                    captured_event = asyncio.Event()
                    
                    async def handle_request(req):
                        target = TARGETS.get(exchange)
                        if target and target["api_pattern"] in req.url:
                            logger.debug(f"🎯 QR ПЕРЕХОПЛЕНО {exchange}: {req.url}")
                            headers = req.headers
                            cookies_list = await browser_context.cookies()
                            cookies_dict = {c["name"]: c["value"] for c in cookies_list}
                            
                            await self._db.save_auth_session(exchange, headers, cookies_dict, user_id)
                            captured_event.set()
                    
                    page.on("request", handle_request)
                    
                    # Навігація до логін сторінки
                    login_urls = {
                        "Binance": "https://accounts.binance.com/en/login",
                        "OKX": "https://www.okx.com/account/login",
                        "Bybit": "https://www.bybit.com/en/login"
                    }
                    url = login_urls.get(exchange)
                    if not url:
                        await status_msg.edit_text(f"❌ Біржа {exchange} не підтримує QR-вхід.")
                        return
                        
                    wait_mode = "domcontentloaded"
                    await page.goto(url, wait_until=wait_mode, timeout=45000)
                    await page.wait_for_timeout(3000)
                    
                    # Закриваємо cookie popup (OKX/Bybit)
                    try:
                        for cookie_sel in [
                            "button:has-text('Reject All')",
                            "button:has-text('Accept All Cookies')",
                            "button:has-text('Accept')",
                            "button[class*='cookie'] >> text=OK",
                        ]:
                            cookie_btn = await page.query_selector(cookie_sel)
                            if cookie_btn and await cookie_btn.is_visible():
                                await cookie_btn.click()
                                logger.info(f"Dismissed cookie popup for {exchange}")
                                await page.wait_for_timeout(500)
                                break
                    except Exception as cookie_err:
                        logger.debug(f"Cookie popup dismiss failed: {cookie_err}")
                    
                    # Перевіряємо, чи ми вже авторизовані (наприклад, завдяки збереженій раніше сесії)
                    authenticated = False
                    
                    # Чекаємо додатково, якщо/поки триває редирект (наприклад, ми вже авторизовані і нас перенаправляє на головну сторінку)
                    is_already_logged_in = False
                    logger.info(f"Checking if already logged in to {exchange} (waiting up to 8 seconds)...")
                    for i in range(8):
                        current_url = page.url.lower()
                        # Якщо URL не містить login/verify/stay-signed-in, то ми залогінились
                        if "login" not in current_url and "signin" not in current_url and "login-verify" not in current_url and "stay-signed-in" not in current_url and "stay-logged-in" not in current_url and current_url not in ["", "about:blank"]:
                            is_already_logged_in = True
                            logger.info(f"Already logged in detected by URL redirect: {page.url}")
                            break
                        # Якщо ми бачимо елементи входу (канвас, кнопка перемикання або інпути паролю), то ми точно не авторизовані
                        try:
                            if await page.query_selector("canvas, .qr-login-icon, input[type='password']"):
                                logger.info("Login form or QR code detected. Proceeding to QR login flow.")
                                break
                        except Exception as query_err:
                            logger.debug(f"Query selector ignored during navigation/redirect: {query_err}")
                        await page.wait_for_timeout(1000)
                    
                    if is_already_logged_in:
                        logger.info(f"User is already logged in to {exchange}. Skipping QR capture, proceeding to P2P.")
                        authenticated = True
                    else:
                        # Перемикаємось на QR-код
                        if exchange == "Binance":
                            try:
                                canvas = await page.query_selector("canvas")
                                if canvas and await canvas.is_visible():
                                    logger.info("Binance QR code is already visible.")
                                else:
                                    logger.info("Binance QR code not visible. Clicking toggle...")
                                    await page.locator(".qr-login-icon").click(timeout=5000)
                                    await page.wait_for_selector("canvas", timeout=5000)
                            except Exception as toggle_err:
                                logger.debug(f"Failed to click Binance QR toggle via locator: {toggle_err}")
                                # Резервний спрощений клік через query_selector
                                try:
                                    toggle = await page.query_selector(".qr-login-icon")
                                    if toggle:
                                        await toggle.click()
                                        await page.wait_for_selector("canvas", timeout=5000)
                                except Exception as e2:
                                    logger.debug(f"Fallback Binance QR toggle failed: {e2}")
                        elif exchange == "OKX":
                            try:
                                canvas = await page.query_selector("canvas")
                                if canvas and await canvas.is_visible():
                                    logger.info("OKX QR code is already visible.")
                                else:
                                    logger.info("OKX QR code not visible. Clicking toggle...")
                                    await page.locator("text=QR code").click(timeout=5000)
                                    await page.wait_for_selector("canvas", timeout=5000)
                            except Exception as toggle_err:
                                logger.debug(f"Failed to click OKX QR toggle via locator: {toggle_err}")
                                try:
                                    toggle = await page.query_selector("text=QR code")
                                    if toggle:
                                        await toggle.click()
                                        await page.wait_for_selector("canvas", timeout=5000)
                                except Exception as e2:
                                    logger.debug(f"Fallback OKX QR toggle failed: {e2}")
                        elif exchange == "Bybit":
                            try:
                                canvas = await page.query_selector("canvas")
                                if canvas and await canvas.is_visible():
                                    logger.info("Bybit QR code is already visible.")
                                else:
                                    logger.info("Bybit QR code not visible. Clicking toggle...")
                                    await page.locator("text=QR Code").click(timeout=5000)
                                    await page.wait_for_selector("canvas", timeout=5000)
                            except Exception as toggle_err:
                                logger.debug(f"Failed to click Bybit QR toggle via locator: {toggle_err}")
                                try:
                                    toggle = await page.query_selector("text=QR Code")
                                    if toggle:
                                        await toggle.click()
                                        await page.wait_for_selector("canvas", timeout=5000)
                                except Exception as e2:
                                    logger.debug(f"Fallback Bybit QR toggle failed: {e2}")
                                
                    if not authenticated:
                        # Беремо перший скріншот QR
                        qr_selector = "canvas"
                        qr_el = await page.query_selector(qr_selector)
                        if not qr_el:
                            await status_msg.edit_text(f"❌ Не вдалося знайти QR-код на сторінці {exchange}.")
                            return
                            
                        await qr_el.screenshot(path=str(qr_path))
                        
                        # Видаляємо тимчасовий статус-меседж
                        with suppress(Exception):
                            await status_msg.delete()
                        
                        # Надсилаємо QR-код з кнопкою скасування
                        caption = (
                            f"🔐 <b>Вхід через QR-код для {exchange}</b>\n\n"
                            f"1️⃣ Відкрий офіційний додаток {exchange} на своєму телефоні.\n"
                            f"2️⃣ Знайди сканер QR-кодів і відскануй цей код.\n"
                            f"3️⃣ Підтверди вхід у додатку.\n\n"
                            f"<i>🔄 Бот автоматично оновлює цей QR-код кожні 25 секунд.</i>\n"
                            f"<i>⏳ Залишилось часу: 90 сек.</i>"
                        )
                        kb = InlineKeyboardMarkup(inline_keyboard=[
                            [InlineKeyboardButton(text="❌ Скасувати", callback_data=f"session:qr_cancel:{exchange}")]
                        ])
                        
                        qr_msg = await message.answer_photo(
                            photo=FSInputFile(str(qr_path)),
                            caption=caption,
                            reply_markup=kb
                        )
                        
                        if session_key in self._active_qr_sessions:
                            self._active_qr_sessions[session_key]["qr_msg"] = qr_msg
                    else:
                        # Видаляємо тимчасовий статус-меседж, якщо ми вже авторизовані
                        await status_msg.delete()
                        
                    # Запускаємо цикл опитування (polling) успіху логіну
                    start_time = time.time()
                    last_refresh_time = time.time()
                    last_fv_update_time = 0.0
                    authenticated = False
                    max_duration = 180  # 🚀 Збільшено таймаут до 180 секунд для спокійної верифікації з телефона
                    
                    while time.time() - start_time < max_duration:
                        # Перевіряємо подію скасування
                        if cancel_event.is_set():
                            logger.info(f"QR Login {exchange} canceled by user.")
                            break
                            
                        # Детекція facial verification (OKX / Binance / Bybit)
                        is_fv_active = bool(self._active_qr_sessions[session_key].get("notified_facial"))
                        try:
                            page_text_fv = await page.evaluate("document.body ? document.body.innerText : ''")
                            page_text_fv_lower = page_text_fv.lower()
                            fv_keywords = [
                                "facial verification", "face verification", "scan your face",
                                "switch to phone", "verify your identity", "use phone camera",
                                "get ready for facial verification", "верификация лица", "верифікація обличчя"
                            ]
                            if any(k in page_text_fv_lower for k in fv_keywords):
                                is_fv_active = True

                            if is_fv_active:
                                logger.info(f"Facial verification modal active for {exchange}!")
                                
                                # 1. Закриваємо Cookie Banner якщо він заважає
                                try:
                                    cookie_btn = page.locator("button:has-text('Accept All Cookies'), button#onetrust-accept-btn-handler").first
                                    if await cookie_btn.count() > 0 and await cookie_btn.is_visible():
                                        await cookie_btn.click(timeout=1000)
                                        logger.info("Dismissed OKX cookie banner")
                                except Exception:
                                    pass

                                # 2. ПРІОРИТЕТ 1: Натискаємо кнопку "Switch to phone" напряму через DOM & Event Dispatch
                                switched = False
                                try:
                                    switched = await page.evaluate("""() => {
                                        const els = Array.from(document.querySelectorAll('button, a, [role="button"], div, span'));
                                        const target = els.reverse().find(el => {
                                            const t = (el.innerText || el.textContent || '').trim();
                                            return t === 'Switch to phone' || t === 'Перейти на телефон';
                                        });
                                        if (target) {
                                            target.click();
                                            target.dispatchEvent(new MouseEvent('click', { bubbles: true, cancelable: true, view: window }));
                                            return true;
                                        }
                                        return false;
                                    }""")
                                    if switched:
                                        logger.info("✅ Dispatched click to 'Switch to phone' button successfully!")
                                        await page.wait_for_timeout(1000)
                                except Exception as switch_err:
                                    logger.debug(f"Failed JS click Switch to phone: {switch_err}")

                                # 3. ПРІОРИТЕТ 2: Якщо "Switch to phone" відсутня, натискаємо "Get started" (Крок 1/2)
                                if not switched:
                                    try:
                                        get_started_done = await page.evaluate("""() => {
                                            const els = Array.from(document.querySelectorAll('button, a, [role="button"], div, span'));
                                            const target = els.reverse().find(el => {
                                                const t = (el.innerText || el.textContent || '').trim();
                                                return t === 'Get started' || t === 'Start' || t === 'Розпочати' || t === 'Начать';
                                            });
                                            if (target) {
                                                target.click();
                                                target.dispatchEvent(new MouseEvent('click', { bubbles: true, cancelable: true, view: window }));
                                                return true;
                                            }
                                            return false;
                                        }""")
                                        if get_started_done:
                                            logger.info("Clicked 'Get started' via JS for facial verification")
                                            await page.wait_for_timeout(1000)
                                    except Exception as fv_click_err:
                                        logger.debug(f"Failed to click Get started: {fv_click_err}")

                                # Оновлюємо статус у Telegram (перший раз або кожні 10 сек)
                                now_ts = time.time()
                                if not self._active_qr_sessions[session_key].get("notified_facial") or (now_ts - last_fv_update_time > 10):
                                    self._active_qr_sessions[session_key]["notified_facial"] = True
                                    last_fv_update_time = now_ts
                                    seconds_left = max(0, int(max_duration - (now_ts - start_time)))
                                    await page.screenshot(path=str(qr_path))
                                    caption_fv = (
                                        f"🔐 <b>Вхід через QR-код для {exchange}</b>\n\n"
                                        f"🪪 <b>Потрібна верифікація обличчя (Face Verification)!</b>\n\n"
                                        f"👉 <b>Бот автоматично натиснув «Get started» та «Switch to phone».</b>\n"
                                        f"Будь ласка, відкрийте додаток {exchange} на своєму телефоні та завершіть перевірку обличчя там!\n\n"
                                        f"<i>⏳ Залишилось часу: {seconds_left} сек. Бот очікує...</i>"
                                    )
                                    qr_msg = self._active_qr_sessions[session_key].get("qr_msg")
                                    if qr_msg:
                                        try:
                                            await qr_msg.edit_media(
                                                media=InputMediaPhoto(
                                                    media=FSInputFile(str(qr_path)),
                                                    caption=caption_fv
                                                ),
                                                reply_markup=kb
                                            )
                                        except Exception as fv_edit_err:
                                            logger.debug(f"Failed to edit media for facial verification: {fv_edit_err}")
                        except Exception as fv_err:
                            logger.debug(f"Facial verification check error: {fv_err}")
                        
                        # Детекція 2FA для будь-якої біржі (лише якщо немає активної верифікації обличчя)
                        try:
                            has_2fa = False
                            text_desc = ""
                            
                            if not is_fv_active:
                                page_text = await page.evaluate("document.body ? document.body.innerText : ''")
                                page_text_lower = page_text.lower()
                            
                            if exchange == "OKX":
                                okx_indicators = [
                                    "enter code", "look out for a text", "verification code", 
                                    "google authenticator", "authenticator code", "sms verification",
                                    "введите код", "код подтверждения", "двухфакторная", "аутентификатор", "sms-код",
                                    "введіть код", "код підтвердження", "двофакторна", "автентифікатор", "sms-код",
                                    "telegram", "телеграм"
                                ]
                                if any(ind in page_text_lower for ind in okx_indicators):
                                    has_2fa = True
                                    text_desc = "OKX Verification Needed"
                                    # Намагаємось знайти більш конкретний опис на сторінці
                                    lines = page_text.split("\n")
                                    for line in lines:
                                        line_lower = line.lower()
                                        if any(k in line_lower for k in ["sent to", "look out", "отправлен", "надіслано", "verification code", "код", "telegram", "телеграм"]):
                                            if any(skip in line_lower for skip in ["join", "group", "channel", "community", "chat", "новости", "новини"]):
                                                continue
                                            if len(line.strip()) > 5 and len(line.strip()) < 150:
                                                text_desc = line.strip()
                                                break
                                                
                            elif exchange == "Binance":
                                binance_indicators = [
                                    "security verification", "verification code", "authenticator code",
                                    "enter 6-digit code", "email verification", "phone verification",
                                    "код подтверждения", "код верификации", "безопасность", "аутентификатор",
                                    "код підтвердження", "код верифікації", "безпека", "автентифікатор"
                                ]
                                if any(ind in page_text_lower for ind in binance_indicators):
                                    has_2fa = True
                                    text_desc = "Binance Security Verification"
                                    lines = page_text.split("\n")
                                    for line in lines:
                                        line_lower = line.lower()
                                        if any(k in line_lower for k in ["verification code", "код", "security", "безопасность", "безпека"]):
                                            if any(skip in line_lower for skip in ["join", "group", "channel", "community", "chat"]):
                                                continue
                                            if len(line.strip()) > 5 and len(line.strip()) < 150:
                                                text_desc = line.strip()
                                                break
                                                
                            elif exchange == "Bybit":
                                bybit_indicators = [
                                    "security verification", "verification code", "authenticator code",
                                    "enter verification code", "google authenticator", "email verification", "sms verification",
                                    "код подтверждения", "код верификации", "безопасность", "аутентификатор",
                                    "код підтвердження", "код верифікації", "безпека", "автентифікатор"
                                ]
                                if any(ind in page_text_lower for ind in bybit_indicators):
                                    has_2fa = True
                                    text_desc = "Bybit Security Verification"
                                    lines = page_text.split("\n")
                                    for line in lines:
                                        line_lower = line.lower()
                                        if any(k in line_lower for k in ["verification code", "код", "security", "безопасность", "безпека"]):
                                            if any(skip in line_lower for skip in ["join", "group", "channel", "community", "chat"]):
                                                continue
                                            if len(line.strip()) > 5 and len(line.strip()) < 150:
                                                text_desc = line.strip()
                                                break
                                                
                            if has_2fa:
                                self._active_qr_sessions[session_key]["text_desc"] = text_desc
                                if not self._active_qr_sessions[session_key].get("notified_2fa"):
                                    self._active_qr_sessions[session_key]["notified_2fa"] = True
                                    seconds_left = int(90 - (time.time() - start_time))
                                    qr_msg = self._active_qr_sessions[session_key].get("qr_msg")
                                    
                                    caption_2fa = (
                                        f"🔐 <b>Вхід через QR-код для {exchange}</b>\n\n"
                                        f"⚠️ <b>Потрібен 2FA-код підтвердження!</b>\n"
                                        f"Опис: <i>{text_desc}</i>\n\n"
                                        f"👉 <b>Будь ласка, введіть цей код безпосередньо у цей чат:</b>\n"
                                        f"<i>(Бот автоматично підставить його на сторінці)</i>\n\n"
                                        f"<i>⏳ Залишилось часу: {seconds_left} сек.</i>"
                                    )
                                    
                                    # Робимо скріншот всієї сторінки, щоб користувач бачив форму 2FA
                                    await page.screenshot(path=str(qr_path))
                                    
                                    if qr_msg:
                                        try:
                                            await qr_msg.edit_media(
                                                media=InputMediaPhoto(
                                                    media=FSInputFile(str(qr_path)),
                                                    caption=caption_2fa
                                                ),
                                                reply_markup=kb
                                            )
                                        except Exception as edit_err:
                                            logger.error(f"Failed to edit QR media to 2FA screenshot: {edit_err}")
                                            try:
                                                await qr_msg.edit_caption(caption=caption_2fa, reply_markup=kb)
                                            except Exception:
                                                pass
                                    else:
                                        # Надсилаємо нове повідомлення із скріншотом поточного стану
                                        qr_msg = await message.answer_photo(
                                            photo=FSInputFile(str(qr_path)),
                                            caption=caption_2fa,
                                            reply_markup=kb
                                        )
                                        self._active_qr_sessions[session_key]["qr_msg"] = qr_msg
                                        
                                    if state:
                                        await state.set_state(QRStates.waiting_for_code)
                                        await state.update_data(session_key=session_key, exchange=exchange)
                        except Exception as e2fa:
                            logger.debug(f"Error checking 2FA state: {e2fa}")
                            
                        # Перевіряємо чи є код у черзі для введення в браузері
                        code_queue = self._active_qr_sessions[session_key].get("code_queue")
                        if code_queue and not code_queue.empty():
                            code = await code_queue.get()
                            logger.info(f"Received 2FA code from user for {exchange}: {code}")
                            
                            # UX feedback: оновлюємо статус в Telegram
                            seconds_left = int(90 - (time.time() - start_time))
                            caption_entering = (
                                f"🔐 <b>Вхід через QR-код для {exchange}</b>\n\n"
                                f"⏳ <b>Вводжу отриманий 2FA-код ({code[:2]}***{code[-1:] if len(code) > 3 else ''}) у браузер...</b>\n\n"
                                f"<i>⏳ Залишилось часу: {seconds_left} сек.</i>"
                            )
                            qr_msg = self._active_qr_sessions[session_key].get("qr_msg")
                            if qr_msg:
                                try:
                                    await qr_msg.edit_caption(caption=caption_entering, reply_markup=kb)
                                except Exception:
                                    pass
                                    
                            try:
                                inputs = await page.query_selector_all("input[type='text'], input[type='number'], input[type='tel'], input[placeholder*='code'], input[placeholder*='Code']")
                                if not inputs:
                                    inputs = await page.query_selector_all("input")
                                    
                                # Відбираємо лише видимі та редаговані поля
                                editable_inputs = []
                                for inp in inputs:
                                    try:
                                        if await inp.is_visible() and await inp.is_editable():
                                            editable_inputs.append(inp)
                                    except Exception:
                                        pass
                                        
                                # Шукаємо конкретно поля OTP/2FA (зазвичай мають maxlength="1")
                                otp_cells = []
                                for inp in editable_inputs:
                                    try:
                                        max_len = await inp.get_attribute("maxlength")
                                        if max_len == "1":
                                            otp_cells.append(inp)
                                    except Exception:
                                        pass
                                        
                                # Якщо знайшли саме 6 окремих комірок (або відповідно до довжини коду)
                                if len(otp_cells) in [4, 6, 8] or (len(otp_cells) > 0 and len(otp_cells) == len(code)):
                                    visible_inputs = otp_cells
                                else:
                                    visible_inputs = editable_inputs
                                    
                                if len(visible_inputs) == 6:
                                    # Спробуємо ввести весь код в першу комірку
                                    try:
                                        await visible_inputs[0].click()
                                        await page.keyboard.type(code, delay=100)
                                        await page.wait_for_timeout(1000)
                                    except Exception as type_err:
                                        logger.debug(f"Failed typing entire code into first cell: {type_err}")
                                    
                                    # Перевіримо, чи заповнилась остання комірка
                                    last_val = ""
                                    try:
                                        last_val = await visible_inputs[-1].input_value()
                                    except Exception:
                                        pass
                                        
                                    if not last_val:
                                        logger.info("First cell type didn't fill all cells, filling individually...")
                                        for idx, char in enumerate(code[:6]):
                                            try:
                                                await visible_inputs[idx].click()
                                                await visible_inputs[idx].fill(char)
                                                await page.wait_for_timeout(100)
                                            except Exception as fill_char_err:
                                                logger.debug(f"Failed to fill cell {idx}: {fill_char_err}")
                                elif len(visible_inputs) >= 1:
                                    # Одне суцільне поле для введення
                                    await visible_inputs[0].click()
                                    await visible_inputs[0].fill("")
                                    await page.keyboard.type(code)
                                    
                                # Натискаємо кнопку підтвердження
                                confirm_btn = await page.query_selector(
                                    "button:has-text('Confirm'), button:has-text('Submit'), button:has-text('Verify'), "
                                    "button:has-text('Next'), button:has-text('Log in'), button:has-text('Login'), "
                                    "button:has-text('Подтвердить'), button:has-text('Далее'), button:has-text('Войти'), "
                                    "button:has-text('Підтвердити'), button:has-text('Далі'), button:has-text('Увійти'), "
                                    "button[type='submit']"
                                )
                                if confirm_btn and await confirm_btn.is_visible() and await confirm_btn.is_enabled():
                                    await confirm_btn.click()
                                else:
                                    if visible_inputs:
                                        await visible_inputs[-1].press("Enter")
                                        
                                await page.wait_for_timeout(3000)
                            except Exception as fill_err:
                                current_url = page.url.lower()
                                if "login" not in current_url and "login-verify" not in current_url:
                                    logger.debug(f"2FA input interrupted by successful redirect/login: {fill_err}")
                                else:
                                    logger.error(f"Failed to fill 2FA code: {fill_err}")
                                
                        # Якщо це сторінка Stay Signed In для Binance - авто-клікаємо Yes
                        if exchange == "Binance" and ("stay-signed-in" in page.url or "stay-logged-in" in page.url):
                            logger.info("Stay Signed In page detected for Binance. Clicking Yes automatically...")
                            try:
                                for selector in ["button:has-text('Yes')", "text=Yes", "button.cht-register-login-button"]:
                                    yes_btn = await page.query_selector(selector)
                                    if yes_btn:
                                        await yes_btn.click()
                                        logger.info("Clicked Yes on Stay Signed In page.")
                                        await page.wait_for_timeout(2000)
                                        break
                            except Exception as btn_err:
                                logger.debug(f"Failed to click Yes: {btn_err}")
                            authenticated = True
                            break

                        # Перевіряємо чи змінився URL (авторизація)
                        is_stay_signed_in = "stay-signed-in" in page.url or "stay-logged-in" in page.url
                        is_logged_in_url = "login" not in page.url and "login-verify" not in page.url
                        
                        if is_logged_in_url or is_stay_signed_in:
                            authenticated = True
                            break
                            
                        # Автоматичне оновлення QR-коду / 2FA скріншоту кожні 25 секунд
                        now_time = time.time()
                        if now_time - last_refresh_time >= 25.0:
                            last_refresh_time = now_time
                            
                            # Якщо ми вже в режимі 2FA, то оновлюємо скріншот всієї сторінки
                            if self._active_qr_sessions[session_key].get("notified_2fa"):
                                logger.info(f"🔄 Оновлення 2FA-скріншоту для {exchange}...")
                                seconds_left = int(90 - (time.time() - start_time))
                                text_desc_cached = self._active_qr_sessions[session_key].get("text_desc", "Security Verification")
                                
                                await page.screenshot(path=str(qr_path))
                                caption_2fa = (
                                    f"🔐 <b>Вхід через QR-код для {exchange}</b>\n\n"
                                    f"⚠️ <b>Потрібен 2FA-код підтвердження!</b>\n"
                                    f"Опис: <i>{text_desc_cached}</i>\n\n"
                                    f"👉 <b>Будь ласка, введіть цей код безпосередньо у цей чат:</b>\n"
                                    f"<i>(Бот автоматично підставить його на сторінці)</i>\n\n"
                                    f"<i>⏳ Залишилось часу: {seconds_left} сек.</i>"
                                )
                                try:
                                    await qr_msg.edit_media(
                                        media=InputMediaPhoto(
                                            media=FSInputFile(str(qr_path)),
                                            caption=caption_2fa
                                        ),
                                        reply_markup=kb
                                    )
                                except Exception as edit_err:
                                    logger.error(f"Failed to edit 2FA media: {edit_err}")
                            else:
                                logger.info(f"🔄 Авто-оновлення QR-скріншоту для {exchange}...")
                                qr_el = await page.query_selector(qr_selector)
                                if qr_el:
                                    if qr_path.exists():
                                        try:
                                            os.remove(qr_path)
                                        except Exception:
                                            pass
                                    await qr_el.screenshot(path=str(qr_path))
                                    
                                    seconds_left = int(90 - (time.time() - start_time))
                                    new_caption = (
                                        f"🔐 <b>Вхід через QR-код для {exchange}</b>\n\n"
                                        f"1️⃣ Відкрий офіційний додаток {exchange} на своєму телефоні.\n"
                                        f"2️⃣ Знайди сканер QR-кодів і відскануй цей код.\n"
                                        f"3️⃣ Підтверди вхід у додатку.\n\n"
                                        f"<i>🔄 QR-код автоматично оновлено!</i>\n"
                                        f"<i>⏳ Залишилось часу: {seconds_left} сек.</i>"
                                    )
                                    try:
                                        await qr_msg.edit_media(
                                            media=InputMediaPhoto(
                                                media=FSInputFile(str(qr_path)),
                                                caption=new_caption
                                            ),
                                            reply_markup=kb
                                        )
                                    except Exception as edit_err:
                                        logger.error(f"Failed to edit QR media: {edit_err}")
                                
                        await asyncio.sleep(1.0)
                        
                    if authenticated:
                        logger.info(f"🎉 QR Login {exchange} authenticated! Redirecting to P2P URL...")
                        # Оновлюємо повідомлення
                        if qr_msg:
                            try:
                                if qr_msg.photo:
                                    await qr_msg.edit_caption(
                                        caption=f"⏳ <b>Авторизація пройшла успішно!</b>\nПерехоплюю сесію {exchange}...",
                                        reply_markup=None
                                    )
                                else:
                                    await qr_msg.edit_text(
                                        text=f"⏳ <b>Авторизація пройшла успішно!</b>\nПерехоплюю сесію {exchange}...",
                                        reply_markup=None
                                    )
                            except Exception as edit_err:
                                logger.debug(f"Failed to edit qr_msg caption/text: {edit_err}")
                        else:
                            try:
                                qr_msg = await message.answer(
                                    f"⏳ <b>Авторизація пройшла успішно (вже залогінені)!</b>\nПерехоплюю сесію {exchange}..."
                                )
                                self._active_qr_sessions[session_key]["qr_msg"] = qr_msg
                            except Exception as send_err:
                                logger.debug(f"Failed to send success session capture notice: {send_err}")
                        
                        # Навігація до P2P profile
                        p2p_url = TARGETS[exchange]["url"]

                        if exchange == "OKX":
                            try:
                                import aiohttp
                                api_url = "https://www.okx.com/v3/c2c/tradingOrders/getMarketplaceAdsPrelogin"
                                params = {
                                    "fiatCurrency": "UAH",
                                    "cryptoCurrency": "USDT",
                                    "paymentMethod": "all",
                                    "side": "sell",
                                    "userType": "all",
                                    "sortType": "price_asc",
                                    "numberPerPage": "5",
                                    "t": str(int(time.time() * 1000)),
                                }
                                logger.info("Fetching active OKX merchant ID dynamically...")
                                async with aiohttp.ClientSession() as session:
                                    async with session.get(api_url, params=params, timeout=10) as resp:
                                        if resp.status == 200:
                                            res_data = await resp.json()
                                            if res_data.get("code") == 0:
                                                items = res_data.get("data", {}).get("sell", [])
                                                if items:
                                                    active_id = items[0].get("publicUserId")
                                                    if active_id:
                                                        p2p_url = f"https://www.okx.com/ua/p2p/ads-merchant?publicUserId={active_id}&fiatCurrency=UAH&fiat=UAH&currency=UAH&cryptoCurrency=USDT&crypto=USDT&token=USDT&ccy=USDT"
                                                        logger.info(f"Dynamically resolved active OKX merchant profile URL: {p2p_url}")
                                                    else:
                                                        logger.warning("No publicUserId found in first OKX sell ad.")
                                                else:
                                                    logger.warning("OKX sell marketplace ads list is empty.")
                                            else:
                                                logger.warning(f"OKX prelogin API returned non-zero code: {res_data.get('code')}")
                                        else:
                                            logger.warning(f"OKX prelogin API status code: {resp.status}")
                            except Exception as fetch_err:
                                logger.error(f"Failed to fetch active OKX merchant dynamically (using fallback): {fetch_err}")

                        # ── Bybit: P2P SPA не рендерується в Playwright (завжди пуста сторінка)
                        # Замість навігації на P2P — виконуємо fetch() прямо в браузері,
                        # який вже авторизований і автоматично додає cookies.
                        # handle_request перехопить цей запит і збереже сесію.
                        if exchange == "Bybit":
                            logger.info("Bybit: тригеримо API через browser fetch() (P2P SPA не рендерується)...")
                            try:
                                await page.evaluate("""
                                    () => {
                                        // Робимо запит до Bybit P2P ads — браузер автоматично додасть всі cookies
                                        fetch('https://api2.bybit.com/fiat/otc/item/list', {
                                            method: 'POST',
                                            credentials: 'include',
                                            headers: {
                                                'Content-Type': 'application/json;charset=UTF-8',
                                                'Accept': 'application/json'
                                            },
                                            body: JSON.stringify({
                                                tokenId: 'USDT',
                                                currencyId: 'UAH',
                                                payment: [],
                                                side: '1',
                                                size: '5',
                                                page: '1',
                                                amount: ''
                                            })
                                        }).catch(() => {});
                                        // Запасний: також запит до feedback/appraise (оригінальний api_pattern)
                                        fetch('https://api2.bybit.com/fiat/otc/user/feedback/appraiseList', {
                                            method: 'POST',
                                            credentials: 'include',
                                            headers: {
                                                'Content-Type': 'application/json;charset=UTF-8',
                                                'Accept': 'application/json'
                                            },
                                            body: JSON.stringify({
                                                memberId: 's9260bda0f121429184f1a258ee726a9f',
                                                evaluateType: 1,
                                                page: 1,
                                                size: 5
                                            })
                                        }).catch(() => {});
                                    }
                                """)
                                # Чекаємо на перехоплення (запити асинхронні)
                                await page.wait_for_timeout(4000)

                                cookies_list = await browser_context.cookies()
                                cookies_dict_direct = {c["name"]: c["value"] for c in cookies_list}
                                captured_hdr = self._active_qr_sessions.get(session_key, {}).get("headers") or {
                                    "content-type": "application/json;charset=UTF-8",
                                    "accept": "application/json",
                                    "User-Agent": DEFAULT_USER_AGENT
                                }
                                await self._db.save_auth_session(exchange, captured_hdr, cookies_dict_direct, user_id)
                                captured_event.set()
                                logger.info(f"Bybit: сесія збережена у БД ({len(cookies_dict_direct)} cookies)")
                            except Exception as bybit_fetch_err:
                                logger.error(f"Bybit browser fetch error: {bybit_fetch_err}")
                                try:
                                    cookies_list = await browser_context.cookies()
                                    cookies_dict_direct = {c["name"]: c["value"] for c in cookies_list}
                                    await self._db.save_auth_session(exchange, {}, cookies_dict_direct, user_id)
                                    captured_event.set()
                                    logger.info("Bybit: fallback cookies збережено")
                                except Exception:
                                    pass
                        else:
                            p2p_wait_mode = "domcontentloaded"
                            await page.goto(p2p_url, wait_until=p2p_wait_mode, timeout=30000)

                        # Даємо сторінці час на стабілізацію та рендер JS (не для Bybit — там вже чекали вище)
                        if exchange == "Binance":
                            await page.wait_for_timeout(7000)
                            # 🚀 ДОДАНО: Примусово тригеримо fetch() у контексті сторінки для миттєвого перехоплення
                            await page.evaluate('''() => {
                                fetch('https://c2c.binance.com/bapi/c2c/v1/friendly/c2c/review/list-by-page', {
                                    method: 'POST',
                                    headers: { 'Content-Type': 'application/json' },
                                    body: JSON.stringify({ userNo: 's95b25fd3a5113bb0a054393e4289a471', rating: 3, page: 1, rows: 10 })
                                }).catch(() => {});
                            }''')
                        elif exchange != "Bybit":
                            await page.wait_for_timeout(6000)
                            if exchange == "OKX":
                                # 🚀 ДОДАНО: Примусово тригеримо fetch() у контексті сторінки для миттєвого перехоплення
                                await page.evaluate('''() => {
                                    fetch('https://www.okx.com/v3/c2c/review/history', {
                                        method: 'POST',
                                        headers: { 'Content-Type': 'application/json' },
                                        body: JSON.stringify({ currentPage: 1, hasComment: false, pageSize: 10, reviewFromBuyer: true, reviewScoreType: "", pubUserId: "0e37a42aca" })
                                    }).catch(() => {});
                                }''')
                            
                        # Очікуємо перехоплення API-запиту (до 25 сек) з періодичним кліком по відгуках та перевіркою редиректів
                        logger.info(f"Waiting for request capture on P2P profile for {exchange}...")
                        capture_timeout = 25.0
                        poll_start = time.time()
                        
                        while time.time() - poll_start < capture_timeout:
                            if captured_event.is_set():
                                break
                                
                            # Якщо раптом нас перенаправило на сторінку підтримки/фідбеку, повертаємось назад
                            current_url = page.url.lower()
                            if "user-support" in current_url or "feedback/entry" in current_url:
                                logger.warning(f"Detected support redirect on {exchange}, navigating back...")
                                try:
                                    await page.go_back(wait_until="domcontentloaded")
                                    await page.wait_for_timeout(2000)
                                except Exception as back_err:
                                    logger.debug(f"Failed to navigate back from support page: {back_err}")
                            
                            # Знаходимо і клікаємо елементи відгуків за межами навігаційних панелей та шапок/підвалів
                            clicked = await page.evaluate('''() => {
                                const keywords = ["відгуки", "отзывы", "review"];
                                const els = Array.from(document.querySelectorAll('div, span, a, button, li'));
                                let clicked = false;
                                
                                for (let el of els) {
                                    if (el.closest('header') || el.closest('nav') || el.closest('footer')) continue;
                                    
                                    const href = el.getAttribute('href') || '';
                                    if (href.includes('feedback') || href.includes('support') || href.includes('user-support')) continue;
                                    
                                    if (el.innerText && keywords.some(k => el.innerText.toLowerCase().trim() === k)) {
                                        if (el.offsetWidth > 0 && el.offsetHeight > 0 && el.innerText.length < 25) {
                                            el.click();
                                            clicked = true;
                                            break;
                                        }
                                    }
                                }
                                
                                if (!clicked) {
                                    for (let el of els) {
                                        if (el.closest('header') || el.closest('nav') || el.closest('footer')) continue;
                                        
                                        const href = el.getAttribute('href') || '';
                                        if (href.includes('feedback') || href.includes('support') || href.includes('user-support')) continue;
                                        
                                        if (el.innerText && keywords.some(k => el.innerText.toLowerCase().includes(k))) {
                                            if (el.offsetWidth > 0 && el.offsetHeight > 0 && el.innerText.length < 25) {
                                                el.click();
                                                clicked = true;
                                                break;
                                            }
                                        }
                                    }
                                }
                                return clicked;
                            }''')
                            
                            if clicked:
                                logger.debug(f"Successfully triggered click on reviews tab for {exchange}")
                            
                            await asyncio.sleep(4.0)
                            
                        try:
                            await asyncio.wait_for(captured_event.wait(), timeout=1.0)
                            if qr_msg:
                                await qr_msg.answer(f"✅ <b>Сесію {exchange} успішно оновлено через QR-код!</b>")
                                try:
                                    await qr_msg.delete()
                                except Exception:
                                    pass
                        except asyncio.TimeoutError:
                            logger.error(f"Timeout waiting for request capture on P2P page for {exchange}")
                            if qr_msg:
                                try:
                                    if qr_msg.photo:
                                        await qr_msg.edit_caption(
                                            caption=f"❌ <b>Помилка:</b> не вдалося перехопити API-запити на сторінці P2P {exchange}. Спробуйте ручну авторизацію.",
                                            reply_markup=None
                                        )
                                    else:
                                        await qr_msg.edit_text(
                                            text=f"❌ <b>Помилка:</b> не вдалося перехопити API-запити на сторінці P2P {exchange}. Спробуйте ручну авторизацію.",
                                            reply_markup=None
                                        )
                                except Exception:
                                    pass
                    else:
                        if not cancel_event.is_set():
                            if qr_msg:
                                try:
                                    if qr_msg.photo:
                                        await qr_msg.edit_caption(
                                            caption=f"⚠️ <b>Час очікування сканування QR-коду {exchange} вичерпано.</b> Спробуйте ще раз.",
                                            reply_markup=None
                                        )
                                    else:
                                        await qr_msg.edit_text(
                                            text=f"⚠️ <b>Час очікування сканування QR-коду {exchange} вичерпано.</b> Спробуйте ще раз.",
                                            reply_markup=None
                                        )
                                except Exception:
                                    pass
                            
            except Exception as e:
                logger.error(f"Error in QR capture flow for {exchange}: {e}", exc_info=True)
                if qr_msg:
                    try:
                        if qr_msg.photo:
                            await qr_msg.edit_caption(
                                caption=f"❌ <b>Помилка під час QR-входу:</b> {str(e)}",
                                reply_markup=None
                            )
                        else:
                            await qr_msg.edit_text(
                                text=f"❌ <b>Помилка під час QR-входу:</b> {str(e)}",
                                reply_markup=None
                            )
                    except Exception:
                        pass
                else:
                    try:
                        await message.answer(f"❌ <b>Помилка QR-входу для {exchange}:</b> {str(e)}")
                    except Exception:
                        pass
            finally:
                # Очищаємо FSM стан
                if state:
                    try:
                        await state.clear()
                    except Exception:
                        pass
                # Закриваємо браузер
                if browser_context:
                    try:
                        await browser_context.close()
                    except Exception as close_err:
                        logger.debug(f"Failed to close browser context (might be already closed): {close_err}")
                # Видаляємо файл QR коду
                if qr_path.exists():
                    try:
                        os.remove(qr_path)
                    except Exception:
                        pass
                # Видаляємо із сесій
                self._active_qr_sessions.pop(session_key, None)
                
        # Запускаємо асинхронно
        asyncio.create_task(run_flow(), name=f"qr-login-flow-{session_key}")

    async def cancel_qr_session(self, exchange: str, user_id: int) -> None:
        """Скасовує активний процес QR-входу для вказаного користувача/біржі."""
        session_key = f"{exchange}:{user_id}"
        session = self._active_qr_sessions.get(session_key)
        if session:
            logger.info(f"Canceling QR session for {session_key}")
            # Встановлюємо подію скасування
            session["cancel_event"].set()
            # Видаляємо кнопку та пишемо про скасування в Telegram
            qr_msg = session.get("qr_msg")
            if qr_msg:
                try:
                    if qr_msg.photo:
                        await qr_msg.edit_caption(
                            caption=f"❌ <b>Авторизацію {exchange} скасовано користувачем.</b>",
                            reply_markup=None
                        )
                    else:
                        await qr_msg.edit_text(
                            text=f"❌ <b>Авторизацію {exchange} скасовано користувачем.</b>",
                            reply_markup=None
                        )
                except Exception:
                    pass

