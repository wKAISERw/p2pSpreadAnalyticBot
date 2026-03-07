import time
import logging

logger = logging.getLogger("StabilityFilter")


class SpreadStabilityFilter:
    """
    Фільтр стабільності. Пропускає спред тільки якщо він протримався
    в стакані N сканувань підряд (захист від фантомів і скамерів-маніпуляторів).
    """

    def __init__(self, required_hits: int = 2, ttl_seconds: float = 15.0):
        self.required_hits = required_hits
        self.ttl = ttl_seconds
        self.cache = {}

    def check(self, buy_ex: str, sell_ex: str, buy_price: str, sell_price: str,
              buy_merchant: str, sell_merchant: str) -> bool:

        now = time.monotonic()

        # Робимо ключ по ціні та мерчанту (надійніше ніж Order ID)
        key = f"{buy_ex}:{buy_merchant}:{buy_price}::{sell_ex}:{sell_merchant}:{sell_price}"

        # Очистка старих записів
        self._cleanup(now)

        if key not in self.cache:
            # Бачимо вперше
            self.cache[key] = {"hits": 1, "first_seen": now}
            return False

        data = self.cache[key]
        data["hits"] += 1
        data["first_seen"] = now  # Оновлюємо час життя

        if data["hits"] >= self.required_hits:
            return True

        return False

    def _cleanup(self, now: float):
        keys_to_delete = [k for k, v in self.cache.items() if now - v["first_seen"] > self.ttl]
        for k in keys_to_delete:
            del self.cache[k]