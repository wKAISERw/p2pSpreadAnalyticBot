import time
import logging

logger = logging.getLogger("StabilityFilter")


class SpreadStabilityFilter:
    """
    Фільтр стабільності. Пропускає спред тільки якщо він протримався
    в стакані N сканувань підряд (захист від фантомів і скамерів-маніпуляторів).

    ВАЖЛИВО: first_seen НЕ оновлюється при кожному хіті — тільки при першому.
    Якщо спред оновлюється частіше ніж TTL, він проходить як тільки набирає hits.
    last_seen оновлюється — це час до якого запис живе.
    """

    def __init__(self, required_hits: int = 2, ttl_seconds: float = 15.0):
        self.required_hits = required_hits
        self.ttl = ttl_seconds
        self.cache: dict[str, dict] = {}

    def check(
        self,
        buy_ex: str, sell_ex: str,
        buy_price: str, sell_price: str,
        buy_merchant: str, sell_merchant: str,
    ) -> bool:
        now = time.monotonic()
        key = f"{buy_ex}:{buy_merchant}:{buy_price}::{sell_ex}:{sell_merchant}:{sell_price}"

        self._cleanup(now)

        if key not in self.cache:
            self.cache[key] = {"hits": 1, "first_seen": now, "last_seen": now}
            return False

        data = self.cache[key]
        data["hits"] += 1
        data["last_seen"] = now  # ← оновлюємо ТІЛЬКИ last_seen, не first_seen

        return data["hits"] >= self.required_hits

    def _cleanup(self, now: float) -> None:
        # Видаляємо по last_seen — якщо спред зник зі стакану
        expired = [k for k, v in self.cache.items() if now - v["last_seen"] > self.ttl]
        for k in expired:
            del self.cache[k]