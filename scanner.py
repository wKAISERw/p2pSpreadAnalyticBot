import asyncio
import logging
import time

from config import settings
from infrastructure.http.bybit_p2p_client import BybitP2PClient
from infrastructure.http.okx_client import OkxClient
from infrastructure.http.wallet_client import WalletClient  # <--- ДОДАНО ІМПОРТ КЛІЄНТА WALLET
from exchanges.bybit import BybitExchange
from exchanges.okx import OkxExchange
from exchanges.wallet import WalletExchange  # <--- ДОДАНО ІМПОРТ БІРЖІ WALLET
from filters.merchant_filter import MerchantFilter
from notifications.telegram_notifier import TelegramNotifier, SpreadAlert
from exchanges.cryptobot_userbot import CryptoBotUserbot
from infrastructure.http.binance_client import BinanceClient
from exchanges.binance import BinanceExchange


from core.dedup_cache import TTLCache
from core.circuit_breaker import CircuitBreaker
from core.cross_matcher import CrossMatchingEngine

logger = logging.getLogger("Scanner")



async def _watchdog(last_cycle_time: list[float], interval: float = 30.0):
    """Перевіряє, чи не завис основний цикл сканування."""
    while True:
        await asyncio.sleep(interval)
        elapsed = time.monotonic() - last_cycle_time[0]
        if elapsed > interval * 2:
            logger.error("🚨 [WATCHDOG] Головний цикл не відповідає %.0fs!", elapsed)


async def run_scanner(notifier: TelegramNotifier, stop_event: asyncio.Event):
    merchant_filter = MerchantFilter()

    dedup_cache = TTLCache(
        ttl_seconds=settings.dedup_ttl_seconds,
        max_size=getattr(settings, "dedup_max_size", 1000)
    )

    # Окремі запобіжники для кожної біржі, щоб падіння однієї не зупиняло іншу
    cb_bybit = CircuitBreaker(failure_threshold=3, recovery_timeout=60.0)
    cb_okx = CircuitBreaker(failure_threshold=3, recovery_timeout=60.0)
    cb_wallet = CircuitBreaker(failure_threshold=3, recovery_timeout=60.0)  # <--- ДОДАНО ЗАПОБІЖНИК ДЛЯ WALLET
    cb_binance = CircuitBreaker(failure_threshold=3, recovery_timeout=60.0)

    logger.info("🚀 Запуск Cross-Exchange Сканера (Bybit + OKX + Wallet + Binance)...")
    logger.info("💼 Капітал: %s ₴ | Поріг: %s%% (+%s%% буфер)",
                settings.working_capital_uah, settings.min_spread_pct, getattr(settings, "safety_buffer_pct", 0.0))

    last_cycle_time = [time.monotonic()]
    watchdog_task = asyncio.create_task(_watchdog(last_cycle_time))

    # Ініціалізуємо рушій (він вже вміє рахувати міжбіржові комісії)
    matcher = CrossMatchingEngine(
        max_capital_uah=settings.working_capital_uah,
        min_trade_uah=getattr(settings, "search_amount_uah", 1000.0),
        min_spread_pct=settings.min_spread_pct,
        safety_buffer_pct=getattr(settings, "safety_buffer_pct", 0.3)
    )

    target_banks = {"43": "Monobank", "14": "PrivatBank", "64": "PUMB"}

    try:
        # Відкриваємо ТРИ HTTP клієнти паралельно (ДОДАНО WALLET)
        async with BybitP2PClient() as b_client, OkxClient() as o_client, WalletClient() as w_client, BinanceClient() as bn_client:
            bybit_ex = BybitExchange(b_client)
            okx_ex = OkxExchange(o_client)
            wallet_ex = WalletExchange(w_client)
            binance_ex = BinanceExchange(bn_client)

            cb_userbot = CryptoBotUserbot(
                api_id=settings.telegram_api_id,
                api_hash=settings.telegram_api_hash,
                session_name="cryptobot_session",  # Файл сесії з'явиться в корені
                update_interval=45.0,  # Безпечний інтервал для твінка
                banks=list(target_banks.keys())
            )
            await cb_userbot.start()  # Запускаємо фоновий воркер

            # ДОДАНО WALLET У СПИСОК КОНФІГІВ
            ex_configs = [
                {"name": "Bybit", "instance": bybit_ex, "cb": cb_bybit},
                {"name": "OKX", "instance": okx_ex, "cb": cb_okx},
                {"name": "Wallet", "instance": wallet_ex, "cb": cb_wallet},
                {"name": "Binance", "instance": binance_ex, "cb": cb_binance}
            ]

            while not stop_event.is_set():
                try:
                    start_time = time.monotonic()
                    search_amounts = getattr(settings, "search_amounts_uah", [1000.0, 2500.0, 5100.0])

                    tasks = []
                    for cfg in ex_configs:
                        tasks.append(cfg["cb"].call(
                            cfg["instance"].fetch_both_multi(amounts=search_amounts, banks=list(target_banks.keys()))
                        ))

                    results = await asyncio.gather(*tasks, return_exceptions=True)

                    # --- НОВЕ: ДОДАЄМО ДАНІ З CRYPTOBOT В ЗАГАЛЬНИЙ ПУЛ ---
                    cb_buy, cb_sell = cb_userbot.get_orders()
                    if cb_buy or cb_sell:
                        results.append((cb_buy, cb_sell))  # Агрегатор нижче сам їх підхопить!
                    # ------------------------------------------------------

                    last_cycle_time[0] = time.monotonic()

                    # 2. Агрегація даних у спільні групи для CrossMatchingEngine
                    buy_grouped = {b: [] for b in target_banks}
                    sell_grouped = {b: [] for b in target_banks}

                    # 2. Агрегація даних (Тимчасово без фільтра merchant_filter.passed)
                    for i, res in enumerate(results):
                        if isinstance(res, Exception): continue

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

                    # 3. Крос-біржове та крос-банківське зіставлення
                    opportunities = matcher.match(buy_grouped, sell_grouped)
                    latency = time.monotonic() - start_time

                    logger.info("🔄 Цикл: %.2fs | Бірж: %d | Маршрутів: %d | Знайдено: %d",
                                latency, len(ex_configs), len(ex_configs) * len(target_banks), len(opportunities))

                    # 4. Обробка та відправка результатів
                    for opp in opportunities:
                        buy_o = opp["buy_order"]
                        sell_o = opp["sell_order"]

                        # Створюємо унікальний ключ дедуплікації, що включає назви бірж
                        key = f"spread:{buy_o.exchange}_{buy_o.merchant_id}:{sell_o.exchange}_{sell_o.merchant_id}:{round(buy_o.price, 2)}"

                        if not dedup_cache.seen(key):
                            dedup_cache.mark(key)

                            # Формуємо назву маршруту: "Bybit(Mono) ➔ OKX(Privat)"
                            route_name = f"{buy_o.exchange}({target_banks[opp['buy_bank']]}) ➔ {sell_o.exchange}({target_banks[opp['sell_bank']]})"

                            logger.warning(
                                "🚨 СПРЕД! %s | Net: %.2f%% | Профіт: %.2f ₴",
                                route_name, opp["net_spread_pct"], opp["net_profit"]
                            )

                            # Відправляємо сповіщення в Telegram
                            alert = SpreadAlert(buy_o, sell_o, opp["net_spread_pct"], opp["net_profit"])
                            await notifier.push(alert)
                            break  # Відправляємо лише 1 найкращий варіант за цикл

                    await asyncio.sleep(settings.scan_interval_seconds)

                except Exception as e:
                    logger.error("❌ Помилка в циклі сканування: %s", e, exc_info=True)
                    await asyncio.sleep(10)
    finally:
        watchdog_task.cancel()
        if 'cb_userbot' in locals():
            await cb_userbot.stop()  # Вимикаємо сесію безпечно
        logger.info("Сканер завершив роботу.")