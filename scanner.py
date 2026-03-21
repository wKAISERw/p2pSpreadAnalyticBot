# scanner.py
# =============================================================================
# БОЙОВА ВЕРСІЯ v1.1 (Крок 4: Чистий сканер без хардкоду)
# =============================================================================
import asyncio
import logging
import time

from config import settings
from infrastructure.http.bybit_p2p_client import BybitP2PClient
from infrastructure.http.okx_client import OkxClient
from infrastructure.http.wallet_client import WalletClient
from exchanges.bybit import BybitExchange
from exchanges.okx import OkxExchange
from exchanges.wallet import WalletExchange
from filters.merchant_filter import MerchantFilter
from bot.notifier import TelegramNotifier, SpreadAlert
from exchanges.cryptobot_userbot import CryptoBotUserbot
from infrastructure.http.binance_client import BinanceClient
from exchanges.binance import BinanceExchange
from infrastructure.http.mexc_client import MexcClient
from exchanges.mexc import MexcExchange
from core.engine.stability import SpreadStabilityFilter
from config.runtime import runtime_config
from core.utils.dedup_cache import TTLCache
from core.utils.circuit_breaker import CircuitBreaker
from core.engine.cross_matcher import CrossMatchingEngine
from core.engine.risk_engine import RiskEngine
from core.storage.merchant_db import MerchantDB
from core.workers.llm_worker import LLMWorkerPool
from core.workers.review_fetcher import ReviewFetcher
from infrastructure.api.bybit_account import BybitAccountClient
from infrastructure.api.binance_account import BinanceAccountClient
from infrastructure.api.okx_account import OKXAccountClient
from infrastructure.api.mexc_account import MEXCAccountClient
from config.banks import BankRegistry, DEFAULT_BANK_CODES, BANK_NAMES

logger = logging.getLogger("Scanner")


async def _watchdog(last_cycle_time: list[float], interval: float = getattr(settings, "watchdog_interval", 30.0)):
    while True:
        await asyncio.sleep(interval)
        elapsed = time.monotonic() - last_cycle_time[0]
        if elapsed > interval * 2:
            logger.error("🚨 [WATCHDOG] Головний цикл не відповідає %.0fs!", elapsed)


async def _db_maintenance_loop(db: MerchantDB, interval_hours: float = getattr(settings, "db_maint_interval_h", 1.0)):
    """Фонова задача для регулярного очищення старих снапшотів з БД."""
    max_age = getattr(settings, "db_snapshot_max_age_h", 168)
    while True:
        await asyncio.sleep(interval_hours * 3600)
        try:
            deleted = await db.prune_snapshots(max_age_hours=max_age)
            if deleted > 0:
                logger.info("🧹 DB Maintenance: видалено %d старих снапшотів", deleted)
        except asyncio.CancelledError:
            break
        except Exception as e:
            logger.error("Помилка під час DB Maintenance: %s", e)


async def run_scanner(notifier: TelegramNotifier, stop_event: asyncio.Event):
    risk_mode = getattr(settings, "risk_mode", "WARNING")
    merchant_filter = MerchantFilter(risk_mode=risk_mode)

    dedup_cache = TTLCache(
        ttl_seconds=getattr(settings, "dedup_ttl_seconds", 60.0),
        max_size=getattr(settings, "dedup_max_size", 1000),
    )

    # Виносимо Circuit Breaker параметри в settings
    cb_fails = getattr(settings, "cb_failure_threshold", 3)
    cb_timeout = getattr(settings, "cb_recovery_timeout", 60.0)

    cb_bybit = CircuitBreaker(failure_threshold=cb_fails, recovery_timeout=cb_timeout)
    cb_okx = CircuitBreaker(failure_threshold=cb_fails, recovery_timeout=cb_timeout)
    cb_wallet = CircuitBreaker(failure_threshold=cb_fails, recovery_timeout=cb_timeout)
    cb_binance = CircuitBreaker(failure_threshold=cb_fails, recovery_timeout=cb_timeout)
    cb_mexc = CircuitBreaker(failure_threshold=cb_fails, recovery_timeout=cb_timeout)

    logger.info("🚀 Запуск Cross-Exchange Сканера (Bybit + OKX + Wallet + Binance + MEXC)...")
    logger.info(
        "💼 Капітал: %s ₴ | Поріг: %s%% (+%s%% буфер)",
        settings.working_capital_uah,
        settings.min_spread_pct,
        getattr(settings, "safety_buffer_pct", 0.0),
    )

    last_cycle_time = [time.monotonic()]
    watchdog_task = asyncio.create_task(_watchdog(last_cycle_time))

    matcher = CrossMatchingEngine(
        max_capital_uah=settings.working_capital_uah,
        min_trade_uah=getattr(settings, "search_amount_uah", 1000.0),
        min_spread_pct=settings.min_spread_pct,
        safety_buffer_pct=getattr(settings, "safety_buffer_pct", 0.3),
    )

    merchant_db = MerchantDB()
    await merchant_db.start()
    await merchant_db.load_blacklist_from_file()

    runtime_config._db = merchant_db
    await runtime_config.load()

    # ── Завантажуємо API credentials з БД ────────────────────────────────
    # Credentials зберігаються зашифровано через /connect команду бота (майбутнє)
    # При старті читаємо і ініціалізуємо account клієнтів
    all_creds = await merchant_db.get_all_credentials()

    bybit_account   = BybitAccountClient()
    binance_account = BinanceAccountClient()
    okx_account     = OKXAccountClient()
    mexc_account    = MEXCAccountClient()

    if "Bybit" in all_creds:
        bybit_account.set_credentials(all_creds["Bybit"]["api_key"], all_creds["Bybit"]["api_secret"])
        logger.info("✅ Bybit API credentials завантажено")
    if "Binance" in all_creds:
        binance_account.set_credentials(all_creds["Binance"]["api_key"], all_creds["Binance"]["api_secret"])
        logger.info("✅ Binance API credentials завантажено")
    if "OKX" in all_creds:
        okx_account.set_credentials(
            all_creds["OKX"]["api_key"], all_creds["OKX"]["api_secret"],
            all_creds["OKX"].get("passphrase", ""),
        )
        logger.info("✅ OKX API credentials завантажено")
    if "MEXC" in all_creds:
        mexc_account.set_credentials(all_creds["MEXC"]["api_key"], all_creds["MEXC"]["api_secret"])
        logger.info("✅ MEXC API credentials завантажено")

    maintenance_task = asyncio.create_task(_db_maintenance_loop(merchant_db))

    notifier.bind_db(merchant_db)
    notifier.bind_commands(merchant_db, {
        "Bybit":   bybit_account,
        "Binance": binance_account,
        "OKX":     okx_account,
        "MEXC":    mexc_account,
    })
    llm_pool = LLMWorkerPool(merchant_db)
    await llm_pool.start()

    review_fetcher = ReviewFetcher(
        merchant_db,
        review_ttl_hours=getattr(settings, "review_ttl_hours", 24.0),
    )
    await review_fetcher.start()
    # Клієнти будуть прив'язані після їх ініціалізації в async with блоці нижче

    risk_engine = RiskEngine(db=merchant_db, llm_pool=llm_pool)

    # Виносимо Stability Filter параметри
    stability_filter = SpreadStabilityFilter(
        required_hits=getattr(settings, "stability_hits", 2),
        ttl_seconds=getattr(settings, "stability_ttl", 15.0)
    )

    target_banks = {code: BANK_NAMES[code] for code in DEFAULT_BANK_CODES if code in BANK_NAMES}

    # Таймінги циклу
    cycle_min_sleep = getattr(settings, "cycle_min_sleep", 0.5)
    cycle_max_sleep = getattr(settings, "cycle_max_sleep", 3.0)
    cycle_error_sleep = getattr(settings, "cycle_error_sleep", 10.0)

    try:
        async with (
            BybitP2PClient() as b_client,
            OkxClient() as o_client,
            WalletClient() as w_client,
            BinanceClient() as bn_client,
            MexcClient() as m_client,
        ):
            bybit_ex = BybitExchange(b_client)
            okx_ex = OkxExchange(o_client)
            wallet_ex = WalletExchange(w_client)
            binance_ex = BinanceExchange(bn_client)
            mexc_ex = MexcExchange(m_client)

            # ── Прив'язуємо API credentials до HTTP клієнтів ─────────────
            # HTTP клієнти використовують ті самі ключі для автентифікованих
            # запитів до P2P профілів мерчантів (review_fetcher)
            if "Bybit" in all_creds:
                b_client.set_credentials(
                    all_creds["Bybit"]["api_key"], all_creds["Bybit"]["api_secret"]
                )
            if "Binance" in all_creds:
                bn_client.set_credentials(
                    all_creds["Binance"]["api_key"], all_creds["Binance"]["api_secret"]
                )
            if "OKX" in all_creds:
                o_client.set_credentials(
                    all_creds["OKX"]["api_key"], all_creds["OKX"]["api_secret"],
                    all_creds["OKX"].get("passphrase", ""),
                )

            # ── Підключаємо HTTP клієнти до ReviewFetcher ────────────────
            review_fetcher.bind_clients(
                binance=bn_client,
                bybit=b_client,
                okx=o_client,
            )

            cb_userbot = CryptoBotUserbot(
                api_id=settings.telegram_api_id,
                api_hash=settings.telegram_api_hash,
                session_name="cryptobot_session",
                update_interval=getattr(settings, "cb_userbot_interval", 45.0),
                banks=list(target_banks.keys()),
            )
            await cb_userbot.start()

            ex_configs = [
                {"name": "Bybit", "instance": bybit_ex, "cb": cb_bybit},
                {"name": "OKX", "instance": okx_ex, "cb": cb_okx},
                {"name": "Wallet", "instance": wallet_ex, "cb": cb_wallet},
                {"name": "Binance", "instance": binance_ex, "cb": cb_binance},
                {"name": "MEXC", "instance": mexc_ex, "cb": cb_mexc},
            ]

            async def safe_fetch_exchange(cfg, amounts, banks):
                """Обгортка для кожної біржі з гільйотиною по часу і логуванням."""
                try:
                    # 🚀 ГІЛЬЙОТИНА: Жодна біржа не має права затримувати цикл довше ніж на 8 секунд
                    # Wallet має менший timeout — він часто 429-ить
                    # і краще нехай CB трипнеться швидко ніж блокувати цикл
                    per_exchange_timeout = 3.0 if cfg["name"] == "Wallet" else 7.0
                    return await asyncio.wait_for(
                        cfg["cb"].call(
                            cfg["instance"].fetch_both_multi(amounts=amounts, banks=banks)
                        ),
                        timeout=per_exchange_timeout
                    )
                except asyncio.TimeoutError:
                    logger.warning("🐌 %s занадто довго відповідає! Відрізаємо від поточного циклу.", cfg["name"])
                    # Примусово реєструємо помилку в CircuitBreaker
                    cfg["cb"].record_failure()
                    raise
                except Exception as e:
                    # Якщо CircuitBreaker відкритий (біржа впала 3 рази), він викидає свою помилку
                    if type(e).__name__ == "CircuitBreakerOpenException":
                        logger.warning("📉 Degraded Mode: %s тимчасово ВІДКЛЮЧЕНА (Запобіжник відкритий)", cfg["name"])
                    raise
            _runtime_last_load: float = 0.0  # timestamp останнього runtime_config.load()
            _cycle_counter: int = 0
            while not stop_event.is_set():
                try:
                    start_time = time.monotonic()

                    # RuntimeConfig: перезавантажуємо не частіше ніж раз на 10с
                    # (не на кожному циклі — зайвий SELECT блокує WAL при конкуренції)
                    if time.monotonic() - _runtime_last_load >= 10.0:
                        await runtime_config.load()
                        _runtime_last_load = time.monotonic()
                    current_capital = float(runtime_config.get("working_capital_uah", settings.working_capital_uah))
                    current_spread = float(runtime_config.get("min_spread_pct", settings.min_spread_pct))
                    current_max_alerts = int(
                        runtime_config.get("max_alerts_per_cycle", getattr(settings, "max_alerts_per_cycle", 4)))

                    matcher.max_capital_uah = current_capital
                    matcher.min_spread_pct = current_spread

                    search_amounts = getattr(settings, "search_amounts_uah", [1000.0, 2500.0, 5100.0])

                    # 🚀 СТАЛО:
                    tasks = [
                        safe_fetch_exchange(cfg, search_amounts, list(target_banks.keys()))
                        for cfg in ex_configs
                    ]

                    # gather зловить помилки відключених бірж (return_exceptions=True),
                    # а успішні результати підуть далі в обробку!
                    results = await asyncio.gather(*tasks, return_exceptions=True)

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

                        all_cycle_orders.extend(b_orders)
                        all_cycle_orders.extend(s_orders)

                        # 🚀 ОПТИМІЗАЦІЯ: Перевіряємо відгуки ТІЛЬКИ для тих, хто пройшов наші фільтри!
                        for o in b_orders:
                            if merchant_filter.passed(o):
                                if o.merchant_id:
                                    review_fetcher.schedule(o.exchange, o.merchant_id)
                                for bank_code in o.bank_codes:
                                    if bank_code in buy_grouped:
                                        buy_grouped[bank_code].append(o)

                        for o in s_orders:
                            if merchant_filter.passed(o):
                                if o.merchant_id:
                                    review_fetcher.schedule(o.exchange, o.merchant_id)
                                for bank_code in o.bank_codes:
                                    if bank_code in sell_grouped:
                                        sell_grouped[bank_code].append(o)

                    if all_cycle_orders:
                        asyncio.ensure_future(
                            merchant_db.add_snapshots_batch(all_cycle_orders)
                        )

                    last_cycle_time[0] = time.monotonic()

                    raw_opportunities = matcher.match(buy_grouped, sell_grouped)
                    opportunities = matcher.group(raw_opportunities, BANK_NAMES)
                    latency = time.monotonic() - start_time

                    # Оновлюємо статистику для /status команди
                    from bot.commands import update_stats
                    _cycle_counter += 1
                    update_stats(
                        cycles=_cycle_counter,
                        last_cycle_ms=latency * 1000,
                    )

                    logger.info(
                        "🔄 Цикл: %.2fs | Бірж: %d | Маршрутів: %d | Сирих: %d | Згруповано: %d",
                        latency,
                        len(ex_configs),
                        len(ex_configs) * len(target_banks),
                        len(raw_opportunities),
                        len(opportunities),
                    )

                    sent_count = 0

                    for opp in opportunities:
                        if sent_count >= current_max_alerts:
                            break

                        buy_o = opp["buy_order"]
                        sell_o = opp["sell_order"]

                        if not getattr(buy_o, "_risk_analyzed", False):
                            risk_engine.analyze(buy_o)
                            buy_o._risk_analyzed = True

                        if not getattr(sell_o, "_risk_analyzed", False):
                            risk_engine.analyze(sell_o)
                            sell_o._risk_analyzed = True

                        risk_buy = getattr(buy_o, "risk_flag", "") or ""
                        risk_sell = getattr(sell_o, "risk_flag", "") or ""
                        if "BLOCK" in risk_buy or "BLOCK" in risk_sell:
                            continue

                        dedup_key = f"spread:{matcher._merge_key(opp)}"
                        if dedup_cache.seen(dedup_key):
                            continue

                        if not stability_filter.check(
                                buy_o.exchange,
                                sell_o.exchange,
                                str(buy_o.price),
                                str(sell_o.price),
                                buy_o.merchant_name,
                                sell_o.merchant_name,
                        ):
                            continue

                        dedup_cache.mark(dedup_key)
                        sent_count += 1

                        route_name = (
                            f"{opp.get('route_type', 'UNKNOWN')} | "
                            f"{buy_o.exchange} ➔ {sell_o.exchange} | "
                            f"{', '.join(opp.get('route_variants', [])[:4])}"
                        )

                        logger.warning(
                            "🚨 СПРЕД! %s | Net: %.2f%% | Профіт: %.2f ₴",
                            route_name,
                            opp["net_spread_pct"],
                            opp["net_profit"],
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
                        )

                        # ── Multi-user dispatch ───────────────────────────
                        # Отримуємо активних підписників і фільтруємо
                        # спред під їхні персональні параметри.
                        # Якщо юзерів немає (перший запуск) — шлемо в default chat.
                        active_users = await merchant_db.get_active_users()

                        if active_users:
                            for user in active_users:
                                # Перевіряємо чи спред підходить під капітал юзера
                                user_capital = float(user["capital"])
                                user_spread  = float(user["min_spread"])
                                user_banks   = set(user["bank_codes"])

                                if float(opp["net_spread_pct"]) < user_spread:
                                    continue

                                # Перевіряємо чи банки перетинаються
                                opp_buy_banks  = set(opp.get("buy_banks_fit") or [])
                                opp_sell_banks = set(opp.get("sell_banks_fit") or [])
                                if not (opp_buy_banks & user_banks) or not (opp_sell_banks & user_banks):
                                    continue

                                # Перевіряємо чи угода влізає в капітал юзера
                                if float(opp["actual_entry_uah"]) > user_capital:
                                    continue

                                await notifier.send_to_user(user["chat_id"], alert)
                        else:
                            # Fallback: single-user режим (перший запуск або немає /start)
                            await notifier.push(alert)

                    cycle_elapsed = time.monotonic() - start_time
                    # Використовуємо динамічні таймінги з settings
                    adaptive_sleep = max(cycle_min_sleep, min(cycle_max_sleep, cycle_max_sleep - cycle_elapsed))
                    await asyncio.sleep(adaptive_sleep)

                except Exception as e:
                    logger.error("❌ Помилка в циклі сканування: %s", e, exc_info=True)
                    await asyncio.sleep(cycle_error_sleep)

    finally:
        watchdog_task.cancel()
        maintenance_task.cancel()

        if "cb_userbot" in locals():
            await cb_userbot.stop()

        await review_fetcher.stop()
        await llm_pool.stop()
        await merchant_db.stop()

        logger.info("Сканер завершив роботу.")