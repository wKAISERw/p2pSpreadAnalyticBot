import asyncio
import logging
import sys
import os
from dotenv import load_dotenv

# Додаємо корінь проєкту до шляхів Python, щоб бачило папку `config` і `core`
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from config import settings
from core.storage.merchant_db import MerchantDB
from core.engine.trade_worker import TradeWorker

load_dotenv()

load_dotenv()

logging.basicConfig(level=logging.INFO)

async def test_tt_dry_run():
    # Примусово вмикаємо DRY RUN
    settings.dry_run_mode = True
    print("🚀 Запуск ізольованого тесту TradeWorker (DRY_RUN_MODE=True)...")

    db = MerchantDB()
    await db.start()

    # Створюємо фіктивних мерчантів з APPROVED статусом для тесту
    # В реальності ці дані з'являються під час парсингу сканера і вердикту LLM
    merchant_buy = "test_seller"
    merchant_sell = "test_buyer"

    await db.save_verdict("Bybit", merchant_buy, "LLM Approved Seller", "", "APPROVED")
    await db.save_verdict("Binance", merchant_sell, "LLM Approved Buyer", "", "APPROVED")

    worker = TradeWorker(db)

    # Імітуємо знайдений CROSS маршрут (Купуємо на Bybit, Продаємо на Binance)
    buy_leg = {
        "exchange": "Bybit",
        "ad_id": "11111111",
        "price": 41.50,
        "merchant_id": merchant_buy
    }

    sell_leg = {
        "exchange": "Binance",
        "ad_id": "22222222",
        "price": 42.00,
        "merchant_id": merchant_sell
    }
    
    amount_usdt = 100.0

    print("\n[КРОК 1] Виконуємо TT-Трейд з УСПІШНИМИ статусами...")
    success = await worker.execute_tt_route(buy_leg, sell_leg, amount_usdt)
    print(f"Результат тесту 1: {'✅ УСПІХ' if success else '❌ ПОМИЛКА'}")

    # ---
    print("\n[КРОК 2] Змінюємо статус одного з мерчантів на REJECTED...")
    await db.save_verdict("Binance", merchant_sell, "Scammer detected", "", "REJECTED")
    
    success = await worker.execute_tt_route(buy_leg, sell_leg, amount_usdt)
    print(f"Результат тесту 2 (має відхилитися): {'✅ УСПІХ' if not success else '❌ ДОЗВОЛЕНО ТОРГУВАТИ (BUG)'}")

    await db.stop()

if __name__ == "__main__":
    asyncio.run(test_tt_dry_run())
