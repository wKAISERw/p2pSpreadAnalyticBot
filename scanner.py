# scanner.py
# =============================================================================
# БОЙОВА ВЕРСІЯ v1.0  (Крок 1: канонічна версія)
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
from notifications.telegram_notifier import TelegramNotifier, SpreadAlert
from exchanges.cryptobot_userbot import CryptoBotUserbot
from infrastructure.http.binance_client import BinanceClient
from exchanges.binance import BinanceExchange
from infrastructure.http.mexc_client import MexcClient
from exchanges.mexc import MexcExchange
from core.stability import SpreadStabilityFilter

from core.dedup_cache import TTLCache
from core.circuit_breaker import CircuitBreaker
from core.cross_matcher import CrossMatchingEngine
from core.risk_engine import RiskEngine
from core.merchant_db import MerchantDB
from core.llm_worker import LLMWorkerPool
from core.review_fetcher import ReviewFetcher

logger = logging.getLogger("Scanner")


async def _watchdog(last_cycle_time: list[float], interval: float = 30.0):
    while True:
        await asyncio.sleep(interval)
        elapsed = time.monotonic() - last_cycle_time[0]
        if elapsed > interval * 2:
            logger.error("🚨 [WATCHDOG] Головний цикл не відповідає %.0fs!", elapsed)


async def _db_maintenance_loop(db: MerchantDB, interval_hours: float = 1.0):
    """Фонова задача для регулярного очищення старих снапшотів з БД (раз на годину)."""
    while True:
        await asyncio.sleep(interval_hours * 3600)
        try:
            # Зберігаємо дані за 7 днів (168 годин) для long-term патернів
            deleted = await db.prune_snapshots(max_age_hours=168)
            if deleted > 0:
                logger.info("🧹 DB Maintenance: видалено %d старих снапшотів", deleted)
        except asyncio.CancelledError:
            break
        except Exception as e:
            logger.error("Помилка під час DB Maintenance: %s", e)


def _order_fingerprint(order, bank_code: str) -> str:
    return (
        f"{order.exchange}|{order.merchant_id}|{bank_code}|"
        f"{order.price}|{order.min_limit}|{order.max_limit}|{order.link}"
    )


def _route_fingerprint(opp: dict) -> str:
    buy_o = opp["buy_order"]
    sell_o = opp["sell_order"]
    return (
        f"{_order_fingerprint(buy_o, opp['buy_bank'])}"
        f" -> "
        f"{_order_fingerprint(sell_o, opp['sell_bank'])}"
    )


def _banks_sorted(codes: list[str] | None, target_banks: dict[str, str]) -> list[str]:
    uniq = {c for c in (codes or []) if c}
    return sorted(uniq, key=lambda c: target_banks.get(c, c))


def _merge_opp_key(opp: dict) -> str:
    buy_o = opp["buy_order"]
    sell_o = opp["sell_order"]
    return (
        f"{opp.get('route_type', 'UNKNOWN')}|"
        f"{buy_o.exchange}|{buy_o.merchant_id}|{buy_o.price}|{buy_o.min_limit}|{buy_o.max_limit}|{buy_o.link}|"
        f"{sell_o.exchange}|{sell_o.merchant_id}|{sell_o.price}|{sell_o.min_limit}|{sell_o.max_limit}|{sell_o.link}|"
        f"{round(float(opp['actual_entry_uah']), 2)}"
    )


def _group_opportunities(opportunities: list[dict], target_banks: dict[str, str]) -> list[dict]:
    grouped: dict[str, dict] = {}

    for opp in opportunities:
        key = _merge_opp_key(opp)
        item = grouped.setdefault(
            key,
            {
                "base": opp.copy(),
                "route_pairs": set(),
            },
        )

        item["route_pairs"].add((opp["buy_bank"], opp["sell_bank"]))

        if float(opp["net_profit"]) > float(item["base"]["net_profit"]):
            item["base"] = opp.copy()

    merged: list[dict] = []

    for item in grouped.values():
        base = item["base"]
        buy_o = base["buy_order"]
        sell_o = base["sell_order"]

        buy_all = _banks_sorted(getattr(buy_o, "bank_codes", []), target_banks)
        sell_all = _banks_sorted(getattr(sell_o, "bank_codes", []), target_banks)

        buy_fit = [b for b in buy_all if b in target_banks]
        sell_fit = [b for b in sell_all if b in target_banks]

        route_pairs = sorted(
            item["route_pairs"],
            key=lambda p: (target_banks.get(p[0], p[0]), target_banks.get(p[1], p[1])),
        )

        base["buy_banks_all"] = buy_all
        base["sell_banks_all"] = sell_all
        base["buy_banks_fit"] = buy_fit
        base["sell_banks_fit"] = sell_fit
        base["route_variants"] = [
            f"{target_banks.get(b, b)} → {target_banks.get(s, s)}"
            for b, s in route_pairs
        ]

        merged.append(base)

    merged.sort(
        key=lambda x: (float(x["net_profit"]), float(x["net_spread_pct"])),
        reverse=True,
    )
    return merged


async def run_scanner(notifier: TelegramNotifier, stop_event: asyncio.Event):
    risk_mode = getattr(settings, "risk_mode", "WARNING")
    merchant_filter = MerchantFilter(risk_mode=risk_mode)

    dedup_cache = TTLCache(
        ttl_seconds=settings.dedup_ttl_seconds,
        max_size=getattr(settings, "dedup_max_size", 1000),
    )

    cb_bybit = CircuitBreaker(failure_threshold=3, recovery_timeout=60.0)
    cb_okx = CircuitBreaker(failure_threshold=3, recovery_timeout=60.0)
    cb_wallet = CircuitBreaker(failure_threshold=3, recovery_timeout=60.0)
    cb_binance = CircuitBreaker(failure_threshold=3, recovery_timeout=60.0)
    cb_mexc = CircuitBreaker(failure_threshold=3, recovery_timeout=60.0)

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

    # 1. Створюємо базу
    merchant_db = MerchantDB()
    await merchant_db.start()
    await merchant_db.load_blacklist_from_file()

    # 2. 🚀 ТЕПЕР ЗАПУСКАЄМО MAINTENANCE (база вже існує)
    maintenance_task = asyncio.create_task(_db_maintenance_loop(merchant_db))

    notifier.bind_db(merchant_db)
    llm_pool = LLMWorkerPool(merchant_db)
    await llm_pool.start()

    review_fetcher = ReviewFetcher(
        merchant_db,
        review_ttl_hours=getattr(settings, "review_ttl_hours", 24.0),
    )
    await review_fetcher.start()

    risk_engine = RiskEngine(db=merchant_db, llm_pool=llm_pool)
    stability_filter = SpreadStabilityFilter(required_hits=2, ttl_seconds=15.0)

    target_banks = {"43": "Monobank", "14": "PrivatBank", "64": "PUMB"}
    max_alerts_per_cycle = getattr(settings, "max_alerts_per_cycle", 4)

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

            cb_userbot = CryptoBotUserbot(
                api_id=settings.telegram_api_id,
                api_hash=settings.telegram_api_hash,
                session_name="cryptobot_session",
                update_interval=45.0,
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

            while not stop_event.is_set():
                try:
                    start_time = time.monotonic()
                    search_amounts = getattr(
                        settings, "search_amounts_uah", [1000.0, 2500.0, 5100.0]
                    )

                    tasks = []
                    for cfg in ex_configs:
                        tasks.append(
                            cfg["cb"].call(
                                cfg["instance"].fetch_both_multi(
                                    amounts=search_amounts,
                                    banks=list(target_banks.keys()),
                                )
                            )
                        )

                    results = await asyncio.gather(*tasks, return_exceptions=True)

                    cb_buy, cb_sell = cb_userbot.get_orders()
                    if cb_buy or cb_sell:
                        results.append((cb_buy, cb_sell))

                    # 🚀 ЗБИРАЄМО ДАНІ ДЛЯ ГРУПУВАННЯ + АНАЛІЗУ (один прохід)
                    all_cycle_orders = []
                    buy_grouped = {b: [] for b in target_banks}
                    sell_grouped = {b: [] for b in target_banks}

                    for res in results:
                        if isinstance(res, Exception):
                            continue

                        b_orders, s_orders = res

                        # Risk analysis (fire-and-forget via ensure_future в analyze())
                        await risk_engine.analyze_batch_async(b_orders)
                        await risk_engine.analyze_batch_async(s_orders)

                        all_cycle_orders.extend(b_orders)
                        all_cycle_orders.extend(s_orders)

                        # Review scheduling
                        for o in b_orders:
                            if o.merchant_id:
                                review_fetcher.schedule(o.exchange, o.merchant_id)
                        for o in s_orders:
                            if o.merchant_id:
                                review_fetcher.schedule(o.exchange, o.merchant_id)

                        # Spread grouping — одразу в цьому ж проході
                        for o in b_orders:
                            if merchant_filter.passed(o):
                                for bank_code in o.bank_codes:
                                    if bank_code in buy_grouped:
                                        buy_grouped[bank_code].append(o)
                        for o in s_orders:
                            if merchant_filter.passed(o):
                                for bank_code in o.bank_codes:
                                    if bank_code in sell_grouped:
                                        sell_grouped[bank_code].append(o)

                    # 🚀 SNAPSHOT INGESTION — fire-and-forget, не блокує цикл.
                    # Снапшоти потрібні для поведінкового аналізу який йде
                    # async у _async_analyze — вони не потрібні ДО spread matching.
                    if all_cycle_orders:
                        asyncio.ensure_future(
                            merchant_db.add_snapshots_batch(all_cycle_orders)
                        )

                    last_cycle_time[0] = time.monotonic()

                    raw_opportunities = matcher.match(buy_grouped, sell_grouped)
                    opportunities = _group_opportunities(raw_opportunities, target_banks)
                    latency = time.monotonic() - start_time

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
                        if sent_count >= max_alerts_per_cycle:
                            break

                        buy_o = opp["buy_order"]
                        sell_o = opp["sell_order"]

                        dedup_key = f"spread:{_merge_opp_key(opp)}"
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
                            logger.debug(
                                "⏳ Спред %s ➔ %s на перевірці стабільності...",
                                buy_o.exchange,
                                sell_o.exchange,
                            )
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

                        await notifier.push(alert)

                    # Адаптивний sleep: підтримуємо стабільний інтервал циклу ~3с
                    # незалежно від того скільки зайняв сам цикл.
                    # min=0.5с (не спамимо біржі), max=3.0с (не гальмуємо)
                    cycle_elapsed = time.monotonic() - start_time
                    adaptive_sleep = max(0.5, min(3.0, 3.0 - cycle_elapsed))
                    await asyncio.sleep(adaptive_sleep)

                except Exception as e:
                    logger.error("❌ Помилка в циклі сканування: %s", e, exc_info=True)
                    await asyncio.sleep(10)

    finally:
        watchdog_task.cancel()
        maintenance_task.cancel()  # 🚀 ЗУПИНЯЄМО MAINTENANCE

        if "cb_userbot" in locals():
            await cb_userbot.stop()

        await review_fetcher.stop()
        await llm_pool.stop()
        await merchant_db.stop()

        logger.info("Сканер завершив роботу.")