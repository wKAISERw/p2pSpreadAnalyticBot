import asyncio
import logging
import time

from config import settings
from infrastructure.http.bybit_p2p_client import BybitP2PClient
from exchanges.bybit import BybitExchange
from filters.merchant_filter import MerchantFilter
from notifications.telegram_notifier import TelegramNotifier, SpreadAlert

from core.dedup_cache import TTLCache
from core.circuit_breaker import CircuitBreaker
from core.cross_matcher import CrossMatchingEngine

logger = logging.getLogger("Scanner")


async def _watchdog(last_cycle_time: list[float], interval: float = 30.0):
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

    circuit_breaker = CircuitBreaker(failure_threshold=3, recovery_timeout=60.0)

    logger.info("🚀 Запуск P2P Сканера (Multi-Bank HFT Engine)...")
    logger.info("💼 Робочий капітал (Макс): %s ₴", settings.working_capital_uah)
    logger.info("🎯 Мінімальний спред (Net): %s%% (Safety Buffer: %s%%)",
                settings.min_spread_pct, getattr(settings, "safety_buffer_pct", 0.0))

    last_cycle_time = [time.monotonic()]
    watchdog_task = asyncio.create_task(_watchdog(last_cycle_time))

    cycle = 0

    try:
        async with BybitP2PClient() as client:
            exchange = BybitExchange(client)

            # Ініціалізуємо рушій один раз поза циклом
            matcher = CrossMatchingEngine(
                max_capital_uah=settings.working_capital_uah,
                min_trade_uah=getattr(settings, "search_amount_uah", 1000.0),
                min_spread_pct=settings.min_spread_pct,
                safety_buffer_pct=getattr(settings, "safety_buffer_pct", 0.3)
            )

            target_banks = {"43": "Monobank", "14": "PrivatBank", "64": "PUMB"}

            while not stop_event.is_set():
                cycle += 1
                if cycle % 100 == 0:
                    logger.info("[HEARTBEAT] Bot alive. Cycles: %d", cycle)

                try:
                    start_time = time.monotonic()
                    search_amounts = getattr(settings, "search_amounts_uah", [1000.0, 2000.0, 3100.0])

                    # 1. ОДИН мульти-запит до API для всіх банків відразу
                    b_orders, s_orders = await circuit_breaker.call(
                        exchange.fetch_both_multi(amounts=search_amounts, banks=list(target_banks.keys()))
                    )

                    last_cycle_time[0] = time.monotonic()

                    # 2. Оптимізоване локальне групування O(1)
                    buy_grouped = {b: [] for b in target_banks}
                    sell_grouped = {b: [] for b in target_banks}

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

                    # 3. Перехресне зіставлення (Cross-Matching)
                    opportunities = matcher.match(buy_grouped, sell_grouped)
                    latency = time.monotonic() - start_time

                    logger.info("🔄 Цикл: %.2fs | Маршрутів: 9 | Знайдено плюсових: %d", latency, len(opportunities))

                    # 4. Обробка результатів
                    for opp in opportunities:
                        buy_o = opp["buy_order"]
                        sell_o = opp["sell_order"]
                        net_spread = opp["net_spread_pct"]
                        net_profit = opp["net_profit"]

                        # Розумний ключ дедуплікації з округленням копійок
                        key = f"cross:{buy_o.merchant_id}:{sell_o.merchant_id}:{round(buy_o.price, 2)}:{round(sell_o.price, 2)}"

                        if not dedup_cache.seen(key):
                            dedup_cache.mark(key)

                            route_name = f"{target_banks[opp['buy_bank']]} ➔ {target_banks[opp['sell_bank']]}"

                            logger.warning(
                                "🚨 СПРЕД! %s | Gross: %.2f%% | Net: %.2f%% | Профіт: %.2f ₴",
                                route_name, opp["gross_spread_pct"], net_spread, net_profit
                            )

                            # Об'єкт SpreadAlert може потребувати оновлення у файлі telegram_notifier.py,
                            # щоб підтримувати нові поля (net_spread, net_profit замість старих)
                            alert = SpreadAlert(buy_o, sell_o, net_spread, net_profit)
                            await notifier.push(alert)
                            break  # Відправляємо тільки найкращий спред
                        else:
                            logger.debug("♻️ Спред %s ➔ %s вже в кеші.", buy_o.merchant_name, sell_o.merchant_name)

                    await asyncio.sleep(5)

                except RuntimeError as e:
                    logger.warning("🔴 %s", e)
                    await asyncio.sleep(5)
                except Exception as e:
                    logger.error("❌ Помилка в циклі сканування: %s", e, exc_info=True)
                    await asyncio.sleep(10)
    finally:
        watchdog_task.cancel()
        logger.info("Сканер завершив роботу.")