import asyncio
from core.utils.circuit_breaker import CircuitBreaker
import logging

logging.basicConfig(level=logging.INFO, format="%(message)s")


async def failing_request():
    raise ConnectionError("Bybit is down!")


async def main():
    # Ставимо таймаут 2 секунди для швидкого тесту
    cb = CircuitBreaker(failure_threshold=3, recovery_timeout=2.0)

    print("--- Тест 1: Набиваємо 3 помилки ---")
    for i in range(3):
        try:
            await cb.call(failing_request())
        except Exception as e:
            print(f"Спроба {i + 1} впала: {e}")

    print("\n--- Тест 2: Перевіряємо що Circuit OPEN ---")
    try:
        await cb.call(failing_request())
    except RuntimeError as e:
        print(f"Успішно перехоплено: {e}")

    print("\n--- Тест 3: Чекаємо recovery_timeout (2 сек) ---")
    await asyncio.sleep(2.1)
    print(f"Поточний стан: {cb.state.name} (має бути HALF_OPEN)")


if __name__ == "__main__":
    asyncio.run(main())