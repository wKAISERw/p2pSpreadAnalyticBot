# scanner.py
# =============================================================================
# БОЙОВА ВЕРСІЯ v2.0 (Рефакторинг: AlertDispatcher + ProviderFactory)
# =============================================================================
from __future__ import annotations

from bot.handlers.core import update_stats, bump_stat, is_muted
from state import state
import asyncio
import logging
import time
from typing import Optional

from config import settings
from config.banks import DEFAULT_BANK_CODES, BANK_NAMES
from config.runtime import runtime_config

from bot.notifier import TelegramNotifier, SpreadAlert
from core.engine.cross_matcher import CrossMatchingEngine
from core.engine.risk_engine import RiskEngine
from core.engine.stability import SpreadStabilityFilter
from core.engine.exchange_manager import exchange_manager
from core.engine.taker_scanner import TakerScanner
from core.engine.maker_ad_monitor import MakerAdMonitor
from core.engine.price_advisor import PriceAdvisor
from core.storage.merchant_db import MerchantDB
from core.utils.circuit_breaker import CircuitBreaker
from core.utils.dedup_cache import TTLCache
from core.utils.tasks import spawn
from core.workers.llm_worker import LLMWorkerPool
from core.workers.review_fetcher import ReviewFetcher
from exchanges.binance import BinanceExchange
from exchanges.bybit import BybitExchange
from exchanges.cryptobot_userbot import CryptoBotUserbot
from exchanges.cryptobot_web import CryptoBotWebExchange
from exchanges.mexc import MexcExchange
from exchanges.okx import OkxExchange
from exchanges.wallet import WalletExchange
from exchanges.bingx import BingxExchange
from filters.merchant_filter import MerchantFilter
from filters.limit_filter import set_max_capital
from infrastructure.http.binance_client import BinanceClient
from infrastructure.http.bybit_p2p_client import BybitP2PClient
from infrastructure.http.mexc_client import MexcClient
from infrastructure.http.okx_client import OkxClient
from infrastructure.http.wallet_client import WalletClient
from infrastructure.http.bingx_client import BingxClient
from infrastructure.http.cryptobot_client import CryptoBotWebClient
from core.workers.session_manager import SessionManager

# Extracted modules
from core.engine.alert_dispatcher import AlertDispatcher
from core.engine.credentials import AccountClients, load_credentials as _load_credentials, bind_http_credentials as _bind_http_credentials
from core.engine.scanner_helpers import calculate_search_amounts, process_taker_path, process_maker_path


logger = logging.getLogger("Scanner")


def safe_float(val) -> float:
    """Безпечне перетворення в float. Визначено на рівні модуля (не в циклі)."""
    try:
        return float(val)
    except (TypeError, ValueError):
        return 0.0


def _transfer_leg(opp: dict, buy_o, sell_o) -> dict | None:
    """
    Крок між ногами зв'язки: чим і за скільки везти USDT на другу біржу.

    На екрані дві ноги стояли поруч так, ніби куплені монети опиняються на
    біржі продажу самі собою. Насправді між ними або внутрішній переказ
    (та сама біржа — безкоштовно), або мережевий переказ із власною
    комісією й часом. Комісія й раніше сиділа в `net_spread`, але побачити
    сам крок було нізвідки — а він і є те, що людина мусить зробити руками
    під таймер угоди.

    None означає «однакова біржа»: везти нема куди.
    """
    buy_ex = getattr(buy_o, "exchange", "")
    sell_ex = getattr(sell_o, "exchange", "")
    if not buy_ex or not sell_ex or buy_ex == sell_ex:
        return None

    try:
        from core.engine.network_fee_engine import NetworkFeeEngine

        network, fee_usdt = NetworkFeeEngine.get_optimal_network(buy_ex, sell_ex)
        options = NetworkFeeEngine.get_all_options(buy_ex, sell_ex)
    except Exception as e:
        logger.debug("transfer leg %s→%s: %s", buy_ex, sell_ex, e)
        return None

    price = safe_float(getattr(buy_o, "price", 0))
    # UNKNOWN — спільної мережі немає, і маршрут насправді неможливий.
    # Показати його як звичайний означало б відправити людину переказувати
    # те, що не переказується.
    unroutable = network == "UNKNOWN"

    return {
        "fromExchange": buy_ex,
        "toExchange": sell_ex,
        "network": network,
        "feeUsdt": 0.0 if unroutable else safe_float(fee_usdt),
        "feeUah": 0.0 if unroutable else safe_float(fee_usdt) * price,
        "unroutable": unroutable,
        # Усі спільні мережі, не лише найдешевша. Дешевша не завжди
        # означає бажана: людина може роками ходити через TRC20 і не
        # хотіти заводити гаманець у мережі, якою користується раз.
        "options": [
            {"network": n, "feeUsdt": safe_float(f), "feeUah": safe_float(f) * price}
            for n, f in options
            if n != "UNKNOWN"
        ],
    }


# Extracted classes and helpers have been moved to:
# - core/engine/alert_dispatcher.py (AlertDispatcher)
# - core/engine/credentials.py (AccountClients, load_credentials, bind_http_credentials)
# - core/engine/scanner_helpers.py (calculate_search_amounts, process_taker_path, process_maker_path)


# ═══════════════════════════════════════════════════════════════════════════════
# Watchdog і DB Maintenance (без змін — вже чисто)
# ═══════════════════════════════════════════════════════════════════════════════

_internet_connected = True
_internet_lost_at = 0.0

async def check_internet_connection(timeout: float = 3.0) -> bool:
    """Перевірка наявності інтернет-зв'язку."""
    hosts = [
        ("1.1.1.1", 80),            # Cloudflare HTTP
        ("8.8.8.8", 443),           # Google DNS over HTTPS
        ("api.telegram.org", 443),  # Telegram API HTTPS
    ]
    for host, port in hosts:
        try:
            reader, writer = await asyncio.wait_for(
                asyncio.open_connection(host, port),
                timeout=timeout
            )
            writer.close()
            try:
                await writer.wait_closed()
            except Exception:
                pass
            return True
        except Exception:
            continue
    return False

async def _internet_watchdog(
    notifier: TelegramNotifier,
    on_restored_callback = None,
    check_interval: float = 5.0
) -> None:
    global _internet_connected, _internet_lost_at
    while True:
        try:
            ok = await check_internet_connection()
            if ok:
                if not _internet_connected:
                    duration = time.monotonic() - _internet_lost_at
                    logger.info("🌐 [Internet Connection] Інтернет-зв'язок відновлено! Відсутній був %.1fs.", duration)
                    _internet_connected = True
                    _internet_lost_at = 0.0
                    
                    if on_restored_callback:
                        try:
                            if asyncio.iscoroutinefunction(on_restored_callback):
                                await on_restored_callback()
                            else:
                                on_restored_callback()
                        except Exception as cb_err:
                            logger.error("Error executing on_restored_callback: %s", cb_err)

                    msg = (
                        f"🌐 <b>Інтернет-зв'язок відновлено!</b>\n\n"
                        f"Зв'язок був відсутній протягом <code>{duration:.0f}s</code>.\n"
                        f"Сканер автоматично продовжує роботу."
                    )
                    spawn(notifier._send_with_retry(msg), "internet-restored-notify", logger_=logger)
            else:
                if _internet_connected:
                    _internet_connected = False
                    _internet_lost_at = time.monotonic()
                    logger.error("🌐 [Internet Connection] Інтернет-зв'язок втрачено! Сканування тимчасово призупинено.")
            
            # Оновлюємо стан для UI та API
            state.stats["internet_connected"] = ok
            update_stats(internet_connected=ok)
            
        except asyncio.CancelledError:
            break
        except Exception as e:
            logger.error("Error in internet watchdog loop: %s", e)
            
        await asyncio.sleep(check_interval)

async def _watchdog(
        last_cycle_time: list[float],
        interval: float = getattr(settings, "watchdog_interval", 30.0),
) -> None:
    while True:
        await asyncio.sleep(interval)
        elapsed = time.monotonic() - last_cycle_time[0]
        if elapsed > interval * 2:
            logger.error("🚨 [WATCHDOG] Головний цикл не відповідає %.0fs!", elapsed)


# Обслуговування БД (prune снапшотів/пропозицій + GC + VACUUM) живе в
# core/workers/db_maintenance.DBMaintenanceTask і стартує з main.py.
# Тут раніше був другий, незалежний цикл на ту саму базу.


# ═══════════════════════════════════════════════════════════════════════════════
# run_scanner — тільки оркестрація
# ═══════════════════════════════════════════════════════════════════════════════

async def run_scanner(notifier: TelegramNotifier, stop_event: asyncio.Event, shared_db=None) -> None:
    # ── Ініціалізація ──────────────────────────────────────────────────────
    # Використовуємо shared_db з main.py якщо є — уникаємо дублювання екземплярів
    _owns_db = shared_db is None
    merchant_db = shared_db if shared_db is not None else MerchantDB()
    if _owns_db:
        await merchant_db.start()
        await merchant_db.load_blacklist_from_file()

    runtime_config._db = merchant_db
    await runtime_config.load()
    from core.engine.risk_engine import CompositeScorer
    await CompositeScorer.load_weights(merchant_db)

    # Завантажуємо credentials один раз через ProviderFactory
    all_creds = await merchant_db.get_all_credentials()
    account_clients = await _load_credentials(merchant_db)

    from core.engine.trade_worker import TradeWorker
    from core.engine.strategy_manager import StrategyManager

    # 🚀 Блок A: Single-Leg Executor
    from core.engine.single_leg_executor import SingleLegExecutor
    single_leg_executor = SingleLegExecutor(merchant_db, notifier=notifier)

    notifier.bind_db(merchant_db)
    # bind_commands відкладено до ініціалізації MakerAdMonitor (після risk_engine)

    llm_pool = LLMWorkerPool(merchant_db, notifier=notifier)
    await llm_pool.start()

    review_fetcher = ReviewFetcher(
        merchant_db,
        review_ttl_hours=getattr(settings, "review_ttl_hours", 24.0),
        notifier=notifier,
    )
    await review_fetcher.start()
    # 🚀 ДОДАНО: Запуск фонового менеджера сесій
    session_manager = SessionManager(merchant_db)

    # 🚀 Блок C: Підключаємо TG-сповіщення для session health
    async def _session_notify(msg: str, user_id: int = 0, exchange: str = "") -> None:
        try:
            from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton
            kb = None
            if exchange:
                kb = InlineKeyboardMarkup(inline_keyboard=[
                    [InlineKeyboardButton(text="🖥 Авторизуватись на ПК (видимо)", callback_data=f"session:login:{exchange}")],
                    [InlineKeyboardButton(text="📲 Скрипт-закладка (телефон)", callback_data=f"intercept:{exchange}")],
                    [InlineKeyboardButton(text="🏃‍♂️ Продовжити без сесій (Ігнор)", callback_data="boot_ignore_sessions")]
                ])

            # Відправляємо конкретному юзеру або всім адмінам
            target_chat = user_id if user_id else None
            await notifier._send_with_retry(msg, keyboard=kb, chat_id=target_chat)
        except Exception as e:
            logger.error("Session notify error: %s", e)

    session_manager.set_notify_callback(_session_notify)
    await session_manager.start()

    # 🚀 ExchangeManager — управління доступністю бірж
    await exchange_manager.load_from_config(runtime_config)

    async def _exchange_down_notify(exchange_name: str) -> None:
        """Сповіщення юзеру коли біржа впала (CircuitBreaker → OPEN)."""
        try:
            from bot.keyboards import exchange_down_kb
            text = (
                f"⚠️ <b>{exchange_name} API не відповідає!</b>\n\n"
                f"CircuitBreaker спрацював — біржа тимчасово недоступна.\n"
                f"Поки вона в пошуку — сканер гальмує на таймаутах.\n\n"
                f"💡 <b>Рекомендація:</b> вимкнути біржу з пошуку"
            )
            kb = exchange_down_kb(exchange_name)
            await notifier._send_with_retry(text, keyboard=kb)
        except Exception as e:
            logger.error("Exchange notify error: %s", e)

    exchange_manager.set_notify_callback(_exchange_down_notify)

    risk_engine = RiskEngine(db=merchant_db, llm_pool=llm_pool, review_fetcher=review_fetcher)
    merchant_filter = MerchantFilter(risk_mode=getattr(settings, "risk_mode", "WARNING"))

    # 🚀 MakerAdMonitor — моніторинг вхідних ордерів на мейкер-оголошення
    maker_monitor = MakerAdMonitor(db=merchant_db, risk_engine=risk_engine)

    async def _maker_notify_cb(chat_id: int, order_info: dict, counterparty_order, exchange: str):
        """Callback для TG-нотифікації про вхідний maker-ордер."""
        try:
            await notifier.send_maker_order_alert(chat_id, order_info, counterparty_order, exchange)
        except Exception as e:
            logger.error("Maker notify callback error: %s", e)

    maker_monitor.set_notify_callback(_maker_notify_cb)
    await maker_monitor.seed_seen_orders()

    # Створюємо TradeWorker першим (бо в нього всередині створюється RouteExecutor)
    trade_worker = TradeWorker(merchant_db)

    # Створюємо StrategyManager і передаємо йому RouteExecutor
    strategy_manager = StrategyManager(merchant_db, maker_monitor, trade_worker._executor)

    # Інжектимо зв'язок назад у TradeWorker
    trade_worker._strategy_manager = strategy_manager

    # Передаємо maker_monitor в bot_commands через notifier.bind_commands
    notifier.bind_commands(
        merchant_db, account_clients.as_dict(), trade_worker,
        single_leg_executor=single_leg_executor,
        maker_monitor=maker_monitor,
        session_manager=session_manager,
    )
    # Імена мають збігатися з полями Settings. Тут стояли stability_hits і
    # stability_ttl, яких у Settings немає, — getattr мовчки повертав дефолт,
    # тож STABILITY_REQUIRED_HITS і STABILITY_TTL_SECONDS з .env не діяли
    # взагалі й фільтр стабільності завжди працював на 2/15.
    stability_filter = SpreadStabilityFilter(
        required_hits=settings.stability_required_hits,
        ttl_seconds=settings.stability_ttl_seconds,
    )
    dedup_cache = TTLCache(
        ttl_seconds=getattr(settings, "dedup_ttl_seconds", 60.0),
        max_size=getattr(settings, "dedup_max_size", 1000),
    )
    matcher = CrossMatchingEngine(
        max_capital_uah=settings.working_capital_uah,
        min_trade_uah=getattr(settings, "search_amount_uah", 1000.0),
        min_spread_pct=settings.min_spread_pct,
        safety_buffer_pct=getattr(settings, "safety_buffer_pct", 0.3),
    )
    target_banks = {code: BANK_NAMES[code] for code in DEFAULT_BANK_CODES if code in BANK_NAMES}
    dispatcher = AlertDispatcher(merchant_db, notifier)
    taker_scanner = TakerScanner(merchant_db)
    taker_dedup = TTLCache(
        ttl_seconds=getattr(settings, "taker_dedup_ttl", 43200.0),  # 12 hours default to prevent flood
        max_size=1000,
    )
    maker_dedup = TTLCache(
        ttl_seconds=getattr(settings, "maker_dedup_ttl", 300.0),  # 5 хвилин
        max_size=100,
    )

    # Таймінги
    cb_fails = getattr(settings, "cb_failure_threshold", 3)
    cb_timeout = getattr(settings, "cb_recovery_timeout", 60.0)
    cycle_min_sleep = getattr(settings, "cycle_min_sleep", 0.5)
    cycle_max_sleep = getattr(settings, "cycle_max_sleep", 3.0)
    cycle_error_sleep = getattr(settings, "cycle_error_sleep", 10.0)

    last_cycle_time = [time.monotonic()]
    watchdog_task = asyncio.create_task(_watchdog(last_cycle_time))

    logger.info("🚀 Запуск Cross-Exchange Сканера (Bybit + OKX + Wallet + Binance + MEXC)...")
    logger.info(
        "💼 Дефолт капітал: %s ₴ | Поріг: %s%% | (Персональні налаштування завантажуються з БД)",
        settings.working_capital_uah,
        settings.min_spread_pct,
    )

    try:
        async with (
            BybitP2PClient() as b_client,
            OkxClient() as o_client,
            WalletClient() as w_client,
            BinanceClient() as bn_client,
            MexcClient() as m_client,
            BingxClient() as bx_client,
            CryptoBotWebClient() as cb_client,
        ):
            # Прив'язуємо credentials до HTTP клієнтів
            _bind_http_credentials(all_creds, b_client, bn_client, o_client, w_client)

            cb_bybit = CircuitBreaker(failure_threshold=cb_fails, recovery_timeout=cb_timeout)
            cb_okx = CircuitBreaker(failure_threshold=cb_fails, recovery_timeout=cb_timeout)
            cb_wallet = CircuitBreaker(failure_threshold=cb_fails, recovery_timeout=cb_timeout)
            cb_binance = CircuitBreaker(failure_threshold=cb_fails, recovery_timeout=cb_timeout)
            cb_mexc = CircuitBreaker(failure_threshold=cb_fails, recovery_timeout=cb_timeout)
            cb_bingx = CircuitBreaker(failure_threshold=cb_fails, recovery_timeout=cb_timeout)
            cb_cryptobot = CircuitBreaker(failure_threshold=cb_fails, recovery_timeout=cb_timeout)

            ex_configs = [
                {"name": "Bybit", "instance": BybitExchange(b_client, merchant_db), "cb": cb_bybit},
                {"name": "OKX", "instance": OkxExchange(o_client, merchant_db), "cb": cb_okx},
                {"name": "Wallet", "instance": WalletExchange(w_client), "cb": cb_wallet},
                {"name": "Binance", "instance": BinanceExchange(bn_client, merchant_db), "cb": cb_binance},
                {"name": "MEXC", "instance": MexcExchange(m_client), "cb": cb_mexc},
                {"name": "BingX", "instance": BingxExchange(bx_client), "cb": cb_bingx},
            ]

            if not settings.use_cryptobot_userbot_scraper:
                ex_configs.append(
                    {"name": "CryptoBot", "instance": CryptoBotWebExchange(cb_client), "cb": cb_cryptobot}
                )

            cb_userbot = CryptoBotUserbot(
                api_id=settings.telegram_api_id,
                api_hash=settings.telegram_api_hash,
                session_name="data/cryptobot_session",
                update_interval=getattr(settings, "cb_userbot_interval", 45.0),
                banks=list(target_banks.keys()),
                use_scraper=settings.use_cryptobot_userbot_scraper,
            )
            await cb_userbot.start()
            
            # Налаштовуємо залежності юзербота
            w_client.set_userbot(cb_userbot)
            cb_client.set_userbot(cb_userbot)
            
            # Прив'язуємо клієнти до review_fetcher
            review_fetcher.bind_clients(
                binance=bn_client,
                bybit=b_client,
                okx=o_client,
                mexc=m_client,
                cryptobot=cb_client
            )

            async def safe_fetch(cfg: dict, amounts: list, banks: list):
                name = cfg["name"]

                # 🚀 Пропускаємо вимкнені біржі — нульовий overhead
                if not exchange_manager.is_enabled(name):
                    return ([], [])

                timeout = 3.0 if name == "Wallet" else 7.0
                f_start = time.monotonic()
                try:
                    result = await asyncio.wait_for(
                        cfg["cb"].call(cfg["instance"].fetch_both_multi(amounts=amounts, banks=banks)),
                        timeout=timeout,
                    )
                    f_duration = time.monotonic() - f_start
                    if f_duration > 1.0:
                        logger.warning("🐌 %s фетч зайняв %.2fs!", name, f_duration)
                    exchange_manager.reset_failures(name)
                    return result
                except asyncio.TimeoutError:
                    f_duration = time.monotonic() - f_start
                    logger.warning("🐌 %s занадто довго відповідає! (Timeout після %.2fs)", name, f_duration)
                    cfg["cb"].record_failure()
                    raise
                except Exception as e:
                    if "Circuit is OPEN" in str(e):
                        logger.warning("📉 Degraded Mode: %s ВІДКЛЮЧЕНА", name)
                        # Сповіщуємо юзера з пропозицією вимкнути
                        spawn(
                            exchange_manager.on_circuit_open(name, runtime_config),
                            f"circuit-open-notify-{name}",
                            logger_=logger,
                        )
                    raise

            # Health-check функція для ExchangeManager
            async def _health_check(exchange_name: str) -> bool:
                """Пробний запит до біржі — повертає True якщо відповідає."""
                cfg_map = {c["name"]: c for c in ex_configs}
                cfg = cfg_map.get(exchange_name)
                if not cfg:
                    return False
                try:
                    result = await asyncio.wait_for(
                        cfg["instance"].fetch_both_multi(
                            amounts=[1000.0], banks=list(target_banks.keys())
                        ),
                        timeout=5.0,
                    )
                    return bool(result and (result[0] or result[1]))
                except Exception:
                    return False

            exchange_manager.set_health_check(_health_check)

            async def on_internet_restored():
                for cfg in ex_configs:
                    cfg["cb"].reset()
                    exchange_manager.reset_failures(cfg["name"])
                logger.info("⚡ [Internet Watchdog] Circuit Breakers & failures reset for all exchanges.")

            internet_watchdog_task = asyncio.create_task(
                _internet_watchdog(notifier, on_restored_callback=on_internet_restored)
            )

            # ── Safe Boot Flow ──────────────────────────────────────────────
            await runtime_config.load()
            await CompositeScorer.load_weights(merchant_db)

            # Перевірялка "здоров'я" при старті
            require_sessions = runtime_config.get("require_sessions", "true") == "true"
            if require_sessions:
                invalid_exchanges = await session_manager.get_invalid_sessions(["Binance", "Bybit", "OKX"])
                if invalid_exchanges:
                    # Ставимо сканер на паузу
                    await runtime_config.set("is_scanner_active", "false")

                    # Сповіщаємо адмінів
                    ex_list = ", ".join(invalid_exchanges)
                    msg = (
                        f"🛑 <b>Safe Boot: Сканер на паузі</b>\n\n"
                        f"У тебе протухли сесії для: <b>{ex_list}</b>.\n"
                        f"Без них RiskEngine матиме менше даних для аналізу мерчантів.\n\n"
                        f"👉 Онови сесії, після чого запусти сканер знову."
                    )
                    from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton
                    kb = InlineKeyboardMarkup(inline_keyboard=[
                        [InlineKeyboardButton(text="🏃‍♂️ Поїхали без сесій (Ігнор)", callback_data="boot_ignore_sessions")],
                        [InlineKeyboardButton(text="📲 Оновити через скрипт-закладки", callback_data="menu:sessions")]
                    ])
                    await notifier._send_with_retry(msg, keyboard=kb)
                    logger.warning(f"Safe Boot: відкладено старт через недійсні сесії ({ex_list}).")
                else:
                    # Сесії валідні або відключена перевірка — піднімаємо покинуті ордери (Регідратація)
                    await strategy_manager.hydrate_active_trades()
            else:
                await strategy_manager.hydrate_active_trades()

            # ── Головний цикл ──────────────────────────────────────────────
            _runtime_last_load: float = time.monotonic()
            _cycle_counter: int = 0

            while not stop_event.is_set():
                try:
                    start_time = time.monotonic()

                    # Runtime config — не частіше ніж раз на 10s
                    if time.monotonic() - _runtime_last_load >= 10.0:
                        await runtime_config.load()
                        await CompositeScorer.load_weights(merchant_db)
                        _runtime_last_load = time.monotonic()

                    # 🚀 ФІКС: Перевірка чи сканер на паузі
                    is_active = runtime_config.get("is_scanner_active", "false") == "true"
                    # Прокидаємо стан у спільну статистику — /api/v1/stats читає
                    # саме звідти і раніше завжди показував "зупинено".
                    if state.stats.get("is_scanner_active") != is_active:
                        update_stats(is_scanner_active=is_active)
                    if not is_active:
                        last_cycle_time[0] = time.monotonic()
                        await asyncio.sleep(3.0)
                        continue

                    # Перевіряємо інтернет-зв'язок
                    if not _internet_connected:
                        logger.warning("🌐 [Scanner] Пропуск циклу сканування через відсутність інтернету...")
                        last_cycle_time[0] = time.monotonic()
                        await asyncio.sleep(5.0)
                        continue

                    current_max_alerts = int(
                        runtime_config.get("max_alerts_per_cycle", getattr(settings, "max_alerts_per_cycle", 4)))

                    active_users = await merchant_db.get_active_users()
                    if active_users:
                        # 🚀 Оновлюємо капітал для користувачів
                        for u in active_users:
                            u_mode = u.get("scanner_mode", "SPREAD")
                            is_sell_mode = u_mode in ("TAKER_SELL", "MAKER_SELL")

                            auto_cap = await merchant_db.get_user_auto_capital(u["user_id"])
                            card_settings = await merchant_db.get_user_card_settings(u["user_id"])
                            card_module_enabled = card_settings and card_settings.get("card_module_mode") != "off"

                            if not is_sell_mode:
                                if u.get("capital_mode") == "auto":
                                    u["capital"] = auto_cap
                                else:
                                    if card_module_enabled and auto_cap > 0:
                                        u["capital"] = min(float(u["capital"]), auto_cap)
                        
                        current_capital = max(float(u["capital"]) for u in active_users) if active_users else settings.working_capital_uah
                        current_spread  = min(float(u["min_spread"]) for u in active_users)
                        # Динамічні банки — union всіх активних юзерів (загальні + buy + sell)
                        import re
                        _all_banks: set[str] = set()
                        for _u in active_users:
                            for field in ("bank_codes", "buy_bank_codes", "sell_bank_codes"):
                                val = _u.get(field)
                                if val:
                                    if isinstance(val, str):
                                        codes = re.findall(r'\d+', val)
                                        _all_banks.update(codes)
                                    elif isinstance(val, (list, tuple, set)):
                                        _all_banks.update(str(x) for x in val)
                        target_banks = {c: BANK_NAMES[c] for c in _all_banks if c in BANK_NAMES}
                        if not target_banks:  # fallback якщо банки порожні
                            target_banks = {c: BANK_NAMES[c] for c in DEFAULT_BANK_CODES if c in BANK_NAMES}
                    else:
                        current_capital = settings.working_capital_uah
                        current_spread = settings.min_spread_pct

                    matcher.max_capital_uah = current_capital
                    matcher.min_spread_pct = current_spread
                    set_max_capital(current_capital)

                    search_amounts = calculate_search_amounts(
                        active_users,
                        getattr(settings, "search_amounts_uah", [1000.0, 2500.0, 5100.0])
                    )

                    results = await asyncio.gather(
                        *(safe_fetch(cfg, search_amounts, list(target_banks.keys())) for cfg in ex_configs),
                        return_exceptions=True,
                    )

                    cb_buy, cb_sell = cb_userbot.get_orders()
                    if cb_buy or cb_sell:
                        results.append((cb_buy, cb_sell))

                    all_cycle_orders = []
                    buy_grouped = {b: [] for b in target_banks}
                    sell_grouped = {b: [] for b in target_banks}

                    for res in results:
                        if isinstance(res, Exception):
                            continue
                        b_orders, s_orders = res

                        # Тегуємо side для снапшотів і PriceAdvisor
                        for o in b_orders:
                            o.side = "buy"
                        for o in s_orders:
                            o.side = "sell"

                        all_cycle_orders.extend(b_orders)
                        all_cycle_orders.extend(s_orders)

                        for o in b_orders:
                            if merchant_filter.passed(o):
                                # schedule() прибрано — відгуки тягнуться ТІЛЬКИ
                                # для мерчантів у реальному спреді (lazy в risk_engine)
                                for bank_code in o.bank_codes:
                                    if bank_code in buy_grouped:
                                        buy_grouped[bank_code].append(o)

                        for o in s_orders:
                            if merchant_filter.passed(o):
                                for bank_code in o.bank_codes:
                                    if bank_code in sell_grouped:
                                        sell_grouped[bank_code].append(o)

                    if all_cycle_orders:
                        spawn(
                            merchant_db.add_snapshots_batch(all_cycle_orders),
                            "add_snapshots_batch",
                            logger_=logger,
                        )

                    last_cycle_time[0] = time.monotonic()

                    raw_opportunities = matcher.match(buy_grouped, sell_grouped, experimental_mode=True)
                    opportunities = matcher.group(raw_opportunities, BANK_NAMES)
                    latency = time.monotonic() - start_time

                    _cycle_counter += 1
                    update_stats(
                        cycles=_cycle_counter,
                        last_cycle_ms=latency * 1000,
                        llm_queue=llm_pool._queue.qsize() if hasattr(llm_pool, "_queue") else 0,
                        review_queue=review_fetcher.in_flight,
                        cb_status={
                            cfg["name"]: "DISABLED" if not exchange_manager.is_enabled(cfg["name"])
                            else cfg["cb"].state.value
                            for cfg in ex_configs
                        },
                    )

                    # 📈 Prometheus metrics update
                    try:
                        from core.analytics.metrics import (
                            scanner_cycle_duration_seconds,
                            llm_queue_size, review_queue_size,
                        )
                        scanner_cycle_duration_seconds.observe(latency)
                        llm_queue_size.set(llm_pool._queue.qsize() if hasattr(llm_pool, "_queue") else 0)
                        review_queue_size.set(review_fetcher.in_flight)
                    except Exception:
                        pass

                    # --- ДОДАНО ДЛЯ ФРОНТЕНДУ ---
                    state.stats["totalScanned"] += len(all_cycle_orders)
                    state.stats["opportunitiesFound"] += len(opportunities)
                    state.stats["avgSpread"] = round(
                        sum(float(o["net_spread_pct"]) for o in opportunities) / len(opportunities)
                        if opportunities else 0.0,
                        2
                    )
                    if opportunities:
                        bump_stat("spreads_found_today", len(opportunities))

                    # 1. Створюємо новий пустий список для актуальних ордерів
                    current_frontend_opps = []
                    current_raw_opps = {}
                    current_cycle_alerts = []
                    # ----------------------------

                    active_ex_count = sum(
                        1 for cfg in ex_configs if exchange_manager.is_enabled(cfg["name"])
                    )
                    logger.info(
                        "🔄 Цикл: %.2fs | Бірж: %d | Маршрутів: %d | Сирих: %d | Згруповано: %d",
                        latency, active_ex_count,
                        active_ex_count * len(target_banks),
                        len(raw_opportunities), len(opportunities),
                    )

                    sent_count = 0
                    # ── Аналіз ризику для всіх ордерів у спреді ─────────────
                    # analyze_for_spread() ЧЕКАЄ завершення аналізу (await) —
                    # кешований LLM вердикт буде в алерті, а не тільки в наступному циклі.
                    _analyzed_ids: set[tuple[str, str]] = set()
                    _to_analyze: list = []
                    for opp in opportunities:
                        for order in [opp["buy_order"], opp["sell_order"]]:
                            key = (order.exchange, order.merchant_id)
                            if key not in _analyzed_ids:
                                _analyzed_ids.add(key)
                                _to_analyze.append(order)
                    if _to_analyze:
                        await risk_engine.analyze_for_spread(_to_analyze)

                    alerts_to_dispatch = []
                    for opp in opportunities:
                        buy_o = opp["buy_order"]
                        sell_o = opp["sell_order"]

                        b_rec, _, b_reason, _, _ = await merchant_db.get_trade_recommendation_full(buy_o.exchange, buy_o.merchant_id)
                        s_rec, _, s_reason, _, _ = await merchant_db.get_trade_recommendation_full(sell_o.exchange, sell_o.merchant_id)
                        # 2. Формуємо об'єкт для React ДО фільтрів дедуплікації і лімітів алертів.
                        # Це гарантує, що ордер буде на сайті рівно стільки, скільки він реально висить в стакані.
                        logger.debug(
                            "🚨 СПРЕД! %s | %s ➔ %s | %s | Net: %.2f%% | Профіт: %.2f ₴",
                            opp.get("route_type", "?"),
                            buy_o.exchange, sell_o.exchange,
                            ", ".join(opp.get("route_variants", [])[:4]),
                            opp["net_spread_pct"], opp["net_profit"],
                        )

                        alert = SpreadAlert(
                            buy_order=buy_o,
                            sell_order=sell_o,
                            spread_pct=opp["net_spread_pct"],
                            profit_uah=opp["net_profit"],
                            deal_amount_uah=opp["actual_entry_uah"],
                            buy_bank=opp.get("buy_bank", ""),
                            sell_bank=opp.get("sell_bank", ""),
                            buy_banks_all=opp.get("buy_banks_all"),
                            sell_banks_all=opp.get("sell_banks_all"),
                            buy_banks_fit=opp.get("buy_banks_fit"),
                            sell_banks_fit=opp.get("sell_banks_fit"),
                            route_variants=opp.get("route_variants"),
                            route_type=opp.get("route_type", "UNKNOWN"),
                            route_pairs=opp.get("route_pairs"),
                            buy_rec=b_rec,
                            sell_rec=s_rec,
                            buy_reason=b_reason,
                            sell_reason=s_reason,
                        )

                        # 🚀 ПРОКИДАЄМО ДАНІ ЕКСПЕРИМЕНТУ В КОРЕНЕВІ ПАРАМЕТРИ АЛЕРТА
                        alert.is_asymmetric = opp.get("is_asymmetric", False)
                        alert.asymmetric_details = opp.get("asymmetric_details")

                        # 🚀 Пропозиція зберігається ПІСЛЯ фільтрів (see below)

                        # --- ДОДАНО ДЛЯ ФРОНТЕНДУ ---
                        frontend_opp = {
                            "id": f"{getattr(buy_o, 'id', 'b')}-{getattr(sell_o, 'id', 's')}",  # ← ФІКС flickering
                            "timestamp": int(time.time() * 1000),
                            "buyOrder": {
                                "id": getattr(buy_o, "id", "b1"),
                                "price": safe_float(getattr(buy_o, "price", 0)),
                                "availableAmount": safe_float(getattr(buy_o, "available_amount", getattr(buy_o, "qty",
                                                                                                         getattr(buy_o,
                                                                                                                 "amount",
                                                                                                                 0)))),
                                "minLimit": safe_float(getattr(buy_o, "min_limit", getattr(buy_o, "min_amount",
                                                                                           getattr(buy_o,
                                                                                                   "min_order_amount",
                                                                                                   0)))),
                                "maxLimit": safe_float(getattr(buy_o, "max_limit", getattr(buy_o, "max_amount",
                                                                                           getattr(buy_o,
                                                                                                   "max_order_amount",
                                                                                                   0)))),
                                "merchantId": getattr(buy_o, "merchant_id", ""),
                                "merchantName": getattr(buy_o, "merchant_name", "Unknown"),
                                "orderCount": int(
                                    safe_float(getattr(buy_o, "order_count", getattr(buy_o, "month_order_count", 0)))),
                                "finishRate": safe_float(getattr(buy_o, "finish_rate", getattr(buy_o, "finish_rate_pct",
                                                                                               getattr(buy_o,
                                                                                                       "month_finish_rate",
                                                                                                       0)))),
                                "exchange": getattr(buy_o, "exchange", "Unknown"),
                                "link": getattr(buy_o, "link", "#"),
                                "bankCodes": getattr(buy_o, "bank_codes", []),
                                "riskScore": getattr(buy_o, "risk_score", 0),
                                "riskFlag": getattr(buy_o, "risk_flag", "OK"),
                                "isVerified": getattr(buy_o, "is_verified", False),
                                "lastOnlineMins": getattr(buy_o, "last_online_mins", None)
                            },
                            "sellOrder": {
                                "id": getattr(sell_o, "id", "s1"),
                                "price": safe_float(getattr(sell_o, "price", 0)),
                                "availableAmount": safe_float(getattr(sell_o, "available_amount", getattr(sell_o, "qty",
                                                                                                          getattr(
                                                                                                              sell_o,
                                                                                                              "amount",
                                                                                                              0)))),
                                "minLimit": safe_float(getattr(sell_o, "min_limit", getattr(sell_o, "min_amount",
                                                                                            getattr(sell_o,
                                                                                                    "min_order_amount",
                                                                                                    0)))),
                                "maxLimit": safe_float(getattr(sell_o, "max_limit", getattr(sell_o, "max_amount",
                                                                                            getattr(sell_o,
                                                                                                    "max_order_amount",
                                                                                                    0)))),
                                "merchantId": getattr(sell_o, "merchant_id", ""),
                                "merchantName": getattr(sell_o, "merchant_name", "Unknown"),
                                "orderCount": int(safe_float(
                                    getattr(sell_o, "order_count", getattr(sell_o, "month_order_count", 0)))),
                                "finishRate": safe_float(getattr(sell_o, "finish_rate",
                                                                 getattr(sell_o, "finish_rate_pct",
                                                                         getattr(sell_o, "month_finish_rate", 0)))),
                                "exchange": getattr(sell_o, "exchange", "Unknown"),
                                "link": getattr(sell_o, "link", "#"),
                                "bankCodes": getattr(sell_o, "bank_codes", []),
                                "riskScore": getattr(sell_o, "risk_score", 0),
                                "riskFlag": getattr(sell_o, "risk_flag", "OK"),
                                "isVerified": getattr(sell_o, "is_verified", False),
                                "lastOnlineMins": getattr(sell_o, "last_online_mins", None)
                            },
                            "netSpread": safe_float(opp.get("net_spread_pct", 0)),
                            "dealAmount": safe_float(
                                opp.get("actual_entry_uah", opp.get("deal_amount", opp.get("volume", 0)))),
                            "netProfit": safe_float(opp.get("net_profit", 0)),
                            "buyBank": opp.get("buy_bank", ""),
                            "sellBank": opp.get("sell_bank", ""),
                            "routeType": opp.get("route_type", "UNKNOWN"),
                            # Крок між ногами: на різних біржах монети треба
                            # ще перевезти. Комісія мережі вже сидить у
                            # net_spread, але сам крок ніде не було видно —
                            # на екрані дві ноги стояли так, ніби USDT
                            # опиняється на другій біржі сам собою.
                            "transfer": _transfer_leg(opp, buy_o, sell_o),
                            "fees": [
                                {"label": f.description, "amountUah": safe_float(f.amount)}
                                for f in (opp.get("fee_details") or [])
                            ],
                            "totalFeeUah": safe_float(opp.get("total_fee", 0)),
                        }
                        is_blocked = False
                        if "BLOCK" in (getattr(buy_o, "risk_flag", "") or ""):
                            logger.debug("⏭ Фронт-скіп: buy BLOCK [%s]", buy_o.merchant_name)
                            is_blocked = True
                        if "BLOCK" in (getattr(sell_o, "risk_flag", "") or ""):
                            logger.debug("⏭ Фронт-скіп: sell BLOCK [%s]", sell_o.merchant_name)
                            is_blocked = True

                        if not is_blocked:
                            current_frontend_opps.append(frontend_opp)
                            # Сирий opp поруч: за ним дашборд відсіє те, що
                            # не проходить фільтри конкретного користувача.
                            current_raw_opps[frontend_opp["id"]] = opp
                        current_cycle_alerts.append(alert)
                        if sent_count >= current_max_alerts:
                            logger.debug("⏭ Скіп: max_alerts (%d)", current_max_alerts)
                            continue

                        dedup_key = f"spread:{matcher._merge_key(opp)}"
                        if dedup_cache.seen(dedup_key):
                            logger.debug("⏭ Скіп: dedup [%s→%s]", buy_o.merchant_name, sell_o.merchant_name)
                            continue

                        if not stability_filter.check(
                                buy_o.exchange, sell_o.exchange,
                                str(buy_o.price), str(sell_o.price),
                                buy_o.merchant_name, sell_o.merchant_name,
                        ):
                            logger.debug("⏭ Скіп: stability [%s→%s]", buy_o.merchant_name, sell_o.merchant_name)
                            continue

                        dedup_cache.mark(dedup_key)
                        sent_count += 1

                        # Пропозиція зберігається персонально під кожного юзера
                        # в AlertDispatcher.dispatch_batch — після того, як
                        # алерт реально пішов у чат.
                        if not is_muted():
                            if runtime_config.get("show_spread_logs", "true") == "true":
                                logger.info("📤 Додано в dispatch-батч алерт: %s→%s %.2f%%", buy_o.merchant_name, sell_o.merchant_name, opp["net_spread_pct"])
                            else:
                                logger.debug("📤 Додано в dispatch-батч алерт: %s→%s %.2f%%", buy_o.merchant_name, sell_o.merchant_name, opp["net_spread_pct"])
                            alerts_to_dispatch.append((alert, opp))
                        else:
                            logger.debug("⏭ Скіп: muted")
                    
                    if alerts_to_dispatch:
                        spawn(
                            dispatcher.dispatch_batch(alerts_to_dispatch),
                            "dispatch_batch",
                            logger_=logger,
                        )
                    state.opportunities = current_frontend_opps[:50]
                    state.opportunities_raw = {
                        fo["id"]: current_raw_opps[fo["id"]]
                        for fo in state.opportunities
                        if fo["id"] in current_raw_opps
                    }
                    state.current_alerts = current_cycle_alerts[:50]
                    state.last_buy_grouped = buy_grouped
                    state.last_sell_grouped = sell_grouped

                    # ── Тейкер-шлях: алерти для TAKER_BUY / TAKER_SELL юзерів ──
                    await process_taker_path(
                        active_users,
                        buy_grouped,
                        sell_grouped,
                        taker_scanner,
                        taker_dedup,
                        notifier,
                        risk_engine=risk_engine,
                    )

                    # ── Мейкер-шлях: підказки для MAKER_BUY / MAKER_SELL юзерів ──
                    await process_maker_path(
                        active_users,
                        buy_grouped,
                        sell_grouped,
                        maker_dedup,
                        notifier,
                    )

                    cycle_elapsed = time.monotonic() - start_time
                    adaptive_sleep = max(cycle_min_sleep, min(cycle_max_sleep, cycle_max_sleep - cycle_elapsed))
                    await asyncio.sleep(adaptive_sleep)

                except Exception as e:
                    logger.error("❌ Помилка в циклі сканування: %s", e, exc_info=True)
                    await asyncio.sleep(cycle_error_sleep)

    finally:
        if "internet_watchdog_task" in locals():
            internet_watchdog_task.cancel()
        watchdog_task.cancel()
        if "cb_userbot" in locals():
            await cb_userbot.stop()
        maker_monitor.stop_all()
        await session_manager.stop()
        await review_fetcher.stop()
        await llm_pool.stop()
        if _owns_db:
            await merchant_db.stop()