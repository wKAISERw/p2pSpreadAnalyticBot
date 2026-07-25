import asyncio
import logging
import sys
import os
from dotenv import load_dotenv

load_dotenv()

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core.workers.session_manager import SessionManager, TARGETS
from core.storage.merchant_db import MerchantDB

logging.basicConfig(level=logging.DEBUG)

async def test_binance_session():
    print("🚀 Запуск ізольованого тесту SessionManager для Binance...")
    db = MerchantDB()
    await db.start()
    
    sm = SessionManager(db)
    # Звичайний фоновий моніторинг (автоматично буде перехоплювати лише ті, де вийшов час)
    print("👉 SessionManager працює у фоновому режимі...")
    task = asyncio.create_task(sm.start()) 
    await asyncio.sleep(60) # Даємо 60 секунд на тестування
    await sm.stop()
    await db.stop()
    print("✅ Тест завершено")

if __name__ == "__main__":
    asyncio.run(test_binance_session())
