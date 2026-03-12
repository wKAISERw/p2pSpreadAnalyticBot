# exchanges/test_wallet.py
import asyncio
import logging

# Додаємо корінь проєкту до шляху, щоб імпорти працювали, якщо запускати з папки exchanges
import sys
import os

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from infrastructure.http.wallet_client import WalletClient
from exchanges.wallet import WalletExchange

# Налаштування логування для красивого виводу
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(message)s",
    datefmt="%H:%M:%S"
)
logger = logging.getLogger("WalletTest")


async def main():
    logger.info("🚀 Запуск тесту Wallet P2P...")

    # Ініціалізуємо клієнт (якщо юзаєш проксі, можна передати proxy="http://...")
    async with WalletClient() as client:
        exchange = WalletExchange(client)

        banks_to_search = ["monobank", "privatbank"]
        logger.info(f"🔍 Шукаємо ордери для банків: {banks_to_search}")

        # Викликаємо наш метод
        buy_orders, sell_orders = await exchange.fetch_both_multi(amounts=[3100.0], banks=banks_to_search)

        # Виводимо ТОП-5 ордерів на КУПІВЛЮ (де мерчанти продають нам USDT)
        logger.info(f"🛒 Знайдено {len(buy_orders)} ордерів на КУПІВЛЮ (Сортуємо від найдешевшого):")
        # Сортуємо від найменшої ціни (щоб купити дешевше)
        sorted_buys = sorted(buy_orders, key=lambda o: o.price)
        for i, order in enumerate(sorted_buys[:5], 1):
            logger.info(
                f"  {i}. {order.price} ₴ | {order.merchant_name} ({order.finish_rate_pct}% | {order.month_order_count} угод) | "
                f"Ліміти: {order.min_limit}-{order.max_limit} ₴ | Банки: {order.bank_codes}"
            )

        print("-" * 80)

        # Виводимо ТОП-5 ордерів на ПРОДАЖ (де мерчанти купують у нас USDT)
        logger.info(f"💸 Знайдено {len(sell_orders)} ордерів на ПРОДАЖ (Сортуємо від найдорожчого):")
        # Сортуємо від найбільшої ціни (щоб продати дорожче)
        sorted_sells = sorted(sell_orders, key=lambda o: o.price, reverse=True)
        for i, order in enumerate(sorted_sells[:5], 1):
            logger.info(
                f"  {i}. {order.price} ₴ | {order.merchant_name} ({order.finish_rate_pct}% | {order.month_order_count} угод) | "
                f"Ліміти: {order.min_limit}-{order.max_limit} ₴ | Банки: {order.bank_codes}"
            )


if __name__ == "__main__":
    # Запускаємо асинхронний цикл
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        logger.info("🛑 Тест зупинено користувачем.")