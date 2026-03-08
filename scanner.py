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

    merchant_db = MerchantDB()
    await merchant_db.start()
    await merchant_db.load_blacklist_from_file()

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

                    for res in results:
                        if isinstance(res, Exception):
                            continue

                        b_orders, s_orders = res
                        await risk_engine.analyze_batch_async(b_orders)
                        await risk_engine.analyze_batch_async(s_orders)

                        for o in b_orders:
                            if o.merchant_id:
                                review_fetcher.schedule(o.exchange, o.merchant_id)

                        for o in s_orders:
                            if o.merchant_id:
                                review_fetcher.schedule(o.exchange, o.merchant_id)

                    last_cycle_time[0] = time.monotonic()

                    buy_grouped = {b: [] for b in target_banks}
                    sell_grouped = {b: [] for b in target_banks}

                    for res in results:
                        if isinstance(res, Exception):
                            continue

                        b_orders, s_orders = res

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

                    opportunities = matcher.match(buy_grouped, sell_grouped)
                    latency = time.monotonic() - start_time

                    logger.info(
                        "🔄 Цикл: %.2fs | Бірж: %d | Маршрутів: %d | Знайдено: %d",
                        latency,
                        len(ex_configs),
                        len(ex_configs) * len(target_banks),
                        len(opportunities),
                    )

                    sent_count = 0
                    used_route_keys: set[str] = set()

                    for opp in opportunities:
                        if sent_count >= max_alerts_per_cycle:
                            break

                        buy_o = opp["buy_order"]
                        sell_o = opp["sell_order"]

                        route_key = _route_fingerprint(opp)
                        if route_key in used_route_keys:
                            continue

                        dedup_key = (
                            f"spread:{buy_o.exchange}_{buy_o.merchant_id}:"
                            f"{sell_o.exchange}_{sell_o.merchant_id}:"
                            f"{opp['buy_bank']}:{opp['sell_bank']}:"
                            f"{round(float(buy_o.price), 2)}:{round(float(sell_o.price), 2)}:"
                            f"{round(float(opp['actual_entry_uah']), 2)}"
                        )

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
                        used_route_keys.add(route_key)
                        sent_count += 1

                        route_name = (
                            f"{opp.get('route_type', 'UNKNOWN')} | "
                            f"{buy_o.exchange}({target_banks[opp['buy_bank']]}) "
                            f"➔ {sell_o.exchange}({target_banks[opp['sell_bank']]})"
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
                            buy_bank=opp["buy_bank"],
                            sell_bank=opp["sell_bank"],
                        )
                        setattr(alert, "route_type", opp.get("route_type", "UNKNOWN"))

                        await notifier.push(alert)

                    await asyncio.sleep(2.0)

                except Exception as e:
                    logger.error("❌ Помилка в циклі сканування: %s", e, exc_info=True)
                    await asyncio.sleep(10)

    finally:
        watchdog_task.cancel()

        if "cb_userbot" in locals():
            await cb_userbot.stop()

        await review_fetcher.stop()
        await llm_pool.stop()
        await merchant_db.stop()

        logger.info("Сканер завершив роботу.")
