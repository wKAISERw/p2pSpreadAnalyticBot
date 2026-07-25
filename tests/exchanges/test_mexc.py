# exchanges/test_mexc.py
import asyncio
import logging
import sys, os
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from infrastructure.http.mexc_client import MexcClient
from exchanges.mexc import MexcExchange

logging.basicConfig(level=logging.INFO, format="%(asctime)s | %(levelname)s | %(message)s")
logger = logging.getLogger("MexcTest")


async def main():
    logger.info("🚀 Тест MEXC OTC...")

    async with MexcClient() as client:
        exchange = MexcExchange(client)
        banks = ["43", "14", "64"]  # Mono, Privat, PUMB

        buy_orders, sell_orders = await exchange.fetch_both_multi(
            amounts=[5100.0], banks=banks
        )

        logger.info("🛒 BUY ордери (топ-5, від найдешевшого):")
        for i, o in enumerate(sorted(buy_orders, key=lambda x: x.price)[:5], 1):
            logger.info(
                "  %d. ₴%s | %s (%.1f%% | %d угод) | %s-%s ₴ | %s",
                i, o.price, o.merchant_name, o.finish_rate_pct,
                o.month_order_count, o.min_limit, o.max_limit, o.bank_codes
            )

        print("-" * 70)

        logger.info("💸 SELL ордери (топ-5, від найдорожчого):")
        for i, o in enumerate(sorted(sell_orders, key=lambda x: x.price, reverse=True)[:5], 1):
            logger.info(
                "  %d. ₴%s | %s (%.1f%% | %d угод) | %s-%s ₴ | %s",
                i, o.price, o.merchant_name, o.finish_rate_pct,
                o.month_order_count, o.min_limit, o.max_limit, o.bank_codes
            )

        logger.info("✅ Всього: %d buy / %d sell", len(buy_orders), len(sell_orders))


if __name__ == "__main__":
    asyncio.run(main())