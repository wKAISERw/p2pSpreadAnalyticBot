# tests/exchanges/test_cryptobot_web.py
import asyncio
import logging
import sys
import os

sys.path.append(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from infrastructure.http.cryptobot_client import CryptoBotWebClient
from exchanges.cryptobot_web import CryptoBotWebExchange
from exchanges.cryptobot_userbot import CryptoBotUserbot
from config import settings

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(message)s",
    datefmt="%H:%M:%S"
)
logger = logging.getLogger("CryptoBotWebTest")


async def main():
    logger.info("🚀 Запуск тесту CryptoBot Web P2P...")

    # Ініціалізуємо юзербота (помічника токенів)
    session_path = os.path.abspath("data/cryptobot_session")
    cb_userbot = CryptoBotUserbot(
        api_id=settings.telegram_api_id,
        api_hash=settings.telegram_api_hash,
        session_name=session_path,
        use_scraper=False,
    )
    await cb_userbot.start()

    try:
        # Ініціалізуємо клієнт та біржу
        async with CryptoBotWebClient(proxy=settings.proxy_url) as client:
            client.set_userbot(cb_userbot)
            exchange = CryptoBotWebExchange(client)

            banks_to_search = ["43", "14"]
            logger.info(f"🔍 Шукаємо ордери для банків: {banks_to_search}")

            # Викликаємо наш метод
            buy_orders, sell_orders = await exchange.fetch_both_multi(amounts=[1000.0], banks=banks_to_search)

            # Виводимо ТОП-5 ордерів на КУПІВЛЮ (де мерчанти продають нам USDT)
            logger.info(f"🛒 Знайдено {len(buy_orders)} ордерів на КУПІВЛЮ (Сортуємо від найдешевшого):")
            sorted_buys = sorted(buy_orders, key=lambda o: o.price)
            for i, order in enumerate(sorted_buys[:5], 1):
                logger.info(
                    f"  {i}. {order.price} ₴ | {order.merchant_name} (Рейтинг: {order.finish_rate_pct}% | Угод: {order.month_order_count} | Вік: {order.account_age_days} днів) | "
                    f"Ліміти: {order.min_limit}-{order.max_limit} ₴ | Банки: {order.bank_codes}"
                )
                if order.trade_terms:
                    logger.info(f"     Умови: {order.trade_terms[:150]}...")

            print("-" * 80)

            # Виводимо ТОП-12 ордерів на ПРОДАЖ (де мерчанти купують у нас USDT)
            logger.info(f"💸 Знайдено {len(sell_orders)} ордерів на ПРОДАЖ (Порядок як у відповіді API, перші 12 мають бути з віком):")
            for i, order in enumerate(sell_orders[:12], 1):
                logger.info(
                    f"  {i}. {order.price} ₴ | {order.merchant_name} (Рейтинг: {order.finish_rate_pct}% | Угод: {order.month_order_count} | Вік: {order.account_age_days} днів) | "
                    f"Ліміти: {order.min_limit}-{order.max_limit} ₴ | Банки: {order.bank_codes}"
                )
                if order.trade_terms:
                    logger.info(f"     Умови: {order.trade_terms[:100]}...")

    finally:
        await cb_userbot.stop()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        logger.info("🛑 Тест зупинено користувачем.")
