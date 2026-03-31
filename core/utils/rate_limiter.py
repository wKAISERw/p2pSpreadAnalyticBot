import asyncio
import time
import logging
from typing import Dict

logger = logging.getLogger("RateLimiter")

class RateLimiter:
    """
    Асинхронний лімітер запитів (Rate Limiter).
    Дозволяє контролювати частоту звернень до API різних бірж (наприклад, Binance, Bybit).
    Допомагає уникати помилок "429 Too Many Requests".
    """

    def __init__(self, calls: int = 10, period: float = 1.0):
        """
        :param calls: Кількість дозволених викликів.
        :param period: Часовий період у секундах.
        """
        self.calls = calls
        self.period = period
        self._tokens: Dict[str, float] = {}
        self._lock = asyncio.Lock()
        
        # Специфічні ліміти для різних бірж (можна розширювати)
        self.limit_configs = {
            "Bybit": {"calls": 10, "period": 1.0},
            "Binance": {"calls": 5, "period": 1.0},
            "CryptoBot": {"calls": 3, "period": 1.0},
            "default": {"calls": 10, "period": 1.0}
        }
        
        # Зберігаємо часові мітки викликів для кожної біржі
        self._history: Dict[str, list[float]] = {}

    async def acquire(self, exchange: str = "default"):
        """
        Очікує, поки з'явиться можливість зробити запит до вказаної біржі.
        Використовує Sliding Window алгоритм.
        """
        async with self._lock:
            config = self.limit_configs.get(exchange, self.limit_configs["default"])
            max_calls = config["calls"]
            period = config["period"]
            
            if exchange not in self._history:
                self._history[exchange] = []
            
            history = self._history[exchange]
            
            while True:
                now = time.time()
                # Видаляємо записи, що старіші за період
                while history and history[0] <= now - period:
                    history.pop(0)
                
                if len(history) < max_calls:
                    # Є вільний слот
                    history.append(now)
                    return True
                
                # Чекаємо до звільнення найстарішого слота
                wait_time = history[0] + period - now
                if wait_time > 0:
                    logger.debug(f"[RateLimit] Exchange {exchange} limit reached. Waiting {wait_time:.2f}s...")
                    await asyncio.sleep(wait_time)
                else:
                    # Малоймовірно, але на випадок дрифту часу
                    history.pop(0)

    def __call__(self, exchange: str = "default"):
        """Дозволяє використовувати як асинхронний контекстний менеджер."""
        return self.ContextManager(self, exchange)

    class ContextManager:
        def __init__(self, limiter: 'RateLimiter', exchange: str):
            self.limiter = limiter
            self.exchange = exchange

        async def __aenter__(self):
            await self.limiter.acquire(self.exchange)

        async def __aexit__(self, exc_type, exc, tb):
            pass

# Глобальний екземпляр для використання у всьому додатку
global_rate_limiter = RateLimiter()
