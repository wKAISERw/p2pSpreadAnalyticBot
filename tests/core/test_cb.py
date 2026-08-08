import asyncio
import sys
from pathlib import Path

# Add project root to sys.path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

from core.utils.circuit_breaker import CircuitBreaker, State
import logging

logging.basicConfig(level=logging.INFO, format="%(message)s")


async def failing_request():
    raise ConnectionError("Bybit is down!")


async def successful_request():
    return "OK"


async def main():
    cb = CircuitBreaker(failure_threshold=3, recovery_timeout=0.2)

    print("--- Тест 1: Набиваємо 3 помилки ---")
    for i in range(3):
        try:
            await cb.call(failing_request())
        except Exception as e:
            print(f"Спроба {i + 1} впала: {e}")

    print("\n--- Тест 2: Перевіряємо що Circuit OPEN ---")
    assert cb.state == State.OPEN
    try:
        await cb.call(failing_request())
    except RuntimeError as e:
        print(f"Успішно перехоплено: {e}")

    print("\n--- Тест 3: Чекаємо recovery_timeout (0.2 сек) ---")
    await asyncio.sleep(0.3)
    assert cb.state == State.HALF_OPEN
    print(f"Поточний стан: {cb.state.name} (має бути HALF_OPEN)")

    print("\n--- Тест 4: Пробний запит у HALF_OPEN і відновлення ---")
    res = await cb.call(successful_request())
    assert res == "OK"
    assert cb.state == State.CLOSED
    print(f"Поточний стан після успішного пробного запиту: {cb.state.name} (має бути CLOSED)")

    print("\n✅ Усі тести CircuitBreaker пройдено!")


if __name__ == "__main__":
    asyncio.run(main())