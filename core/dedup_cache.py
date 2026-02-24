import time
from collections import OrderedDict

from config import settings


class TTLCache:
    """Легкий TTL-кеш для запобігання повторним алертам."""

    # Дефолтні значення передаємо явно, без імпорту settings
    def __init__(self, ttl_seconds: float = 600.0, max_size: int = 1000):
        self._ttl = ttl_seconds
        self._max_size = max_size
        self._store: OrderedDict[str, float] = OrderedDict()

    def seen(self, key: str) -> bool:
        """Перевіряє, чи ми вже бачили цей ключ і чи він актуальний."""
        self._evict_expired()
        return key in self._store

    def mark(self, key: str):
        """Додає або оновлює мітку часу для ключа."""
        if key in self._store:
            del self._store[key]

        if len(self._store) >= self._max_size:
            self._store.popitem(last=False)

        self._store[key] = time.monotonic()

    def _evict_expired(self):
        """Видаляє записи, термін дії яких вичерпався."""
        now = time.monotonic()
        while self._store:
            key, timestamp = next(iter(self._store.items()))
            if now - timestamp > self._ttl:
                self._store.popitem(last=False)
            else:
                break

# Додаємо імпорт налаштувань (виправдано для допоміжної функції)

def build_dedup_key(side: str, order_id: str, price: str, available_amount: str = "") -> str:
    """Будує ключ дедуплікації відповідно до налаштувань."""
    if settings.dedup_strict:
        return f"{side}:{order_id}:{price}:{available_amount}"
    return f"{side}:{order_id}:{price}"
