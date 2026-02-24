import asyncio
import logging
import time
from infrastructure.http.bybit_p2p_client import BybitP2PClient
import json


logging.basicConfig(level=logging.INFO, format="%(asctime)s | %(levelname)s | %(message)s")


async def main():
    payload = {
        "userId": "",
        "tokenId": "USDT",
        "currencyId": "UAH",
        "payment": ["43"],
        "side": "1",
        "size": "5",
        "page": "1",
        "amount": "3100",
        "authMaker": False,
        "canTrade": False
    }

    # 1. Тестуємо стабільний API (api2)
    url = "https://api2.bybit.com/fiat/otc/item/online"

    # Якщо api2 видасть помилку 404, розкоментуй цей рядок:
    # url = "https://www.bybit.com/x-api/fiat/otc/item/online"

    print(f"🚀 Запуск стелс-клієнта Bybit... Ендпоінт: {url}")

    async with BybitP2PClient() as client:
        try:
            # 2. Заміряємо Latency
            start_time = time.monotonic()
            data = await client.fetch(url, payload)
            latency = time.monotonic() - start_time

            # 3. Витягуємо час сервера
            server_time = data.get("time_now") or data.get("time") or data.get("retExtInfo", {}).get("time")

            print(f"\n⏱️ Затримка (Latency): {latency:.3f} сек")
            print(f"🕐 Час сервера Bybit: {server_time}")

            items = data.get("result", {}).get("items", [])
            if items:
                print("\n🔍 Сирий JSON першого ордера (для мапінгу моделі):")
                print(json.dumps(items[0], indent=2, ensure_ascii=False))
                print("-" * 50 + "\n")
            print(f"✅ Успіх! Знайдено ордерів: {len(items)}\n")

            for i, item in enumerate(items):
                price = item.get("price")
                merchant = item.get("nickName")
                min_limit = item.get("minAmount")
                max_limit = item.get("maxAmount")

                print(f"#{i + 1} | Курс: {price} ₴ | Мерчант: {merchant} | Ліміти: {min_limit} - {max_limit} ₴")

        except Exception as e:
            print(f"\n❌ Помилка виконання: {e}")
            print("💡 Спробуй закоментувати URL 'api2' і розкоментувати 'www.bybit.com/x-api'")


if __name__ == "__main__":
    asyncio.run(main())