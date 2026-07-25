# core/utils/cache.py
"""
Єдиний TTL-кеш для всього проекту.
Замінює три окремі реалізації:
  - TTLCache в dedup_cache.py       (seen/mark для дедуплікації спредів)
  - _BoundedTTLCache в risk_engine  (get/set для behavioral/identity кешу)
  - dict в stability.py             (hits-counter — залишається окремим)

Інтерфейс:
  seen(key)         → bool          # чи є ключ і чи не протух
  mark(key)         → None          # додати ключ без значення
  get(key)          → value | None  # отримати значення (None якщо протухло)
  set(key, value)   → None          # зберегти значення

Всі методи потокобезпечні в рамках asyncio (GIL достатньо).
"""
from __future__ import annotations

import time
from collections import OrderedDict
from typing import Any, Hashable, Optional


class TTLCache:
    """
    Уніфікований TTL-кеш з обмеженим розміром і LRU-евікцією.

    Параметри:
        ttl_seconds  — час життя запису (секунди)
        max_size     — максимальна кількість записів
        evict_ratio  — частка записів що видаляється при переповненні (0.0–1.0)
    """
    __slots__ = ("_ttl", "_max", "_evict_n", "_store")

    def __init__(
        self,
        ttl_seconds: float = 60.0,
        max_size: int = 1000,
        evict_ratio: float = 0.1,
    ):
        self._ttl = ttl_seconds
        self._max = max_size
        # Кількість записів для евікції при переповненні (мін. 1)
        self._evict_n = max(1, int(max_size * evict_ratio))
        # OrderedDict: key → (timestamp, value)
        # value=_SENTINEL означає "тільки мітка", без значення (seen/mark)
        self._store: OrderedDict[Hashable, tuple[float, Any]] = OrderedDict()

    # ─── Базовий інтерфейс (seen/mark) ───────────────────────────────────────

    def seen(self, key: Hashable) -> bool:
        """Перевіряє чи є ключ і чи він не протух. Не оновлює TTL."""
        entry = self._store.get(key)
        if entry is None:
            return False
        ts, _ = entry
        if time.monotonic() - ts > self._ttl:
            del self._store[key]
            return False
        return True

    def mark(self, key: Hashable) -> None:
        """Додає ключ (без значення). Оновлює TTL якщо вже є."""
        self._put(key, None)

    # ─── Розширений інтерфейс (get/set з value) ───────────────────────────────

    def get(self, key: Hashable) -> Optional[Any]:
        """
        Повертає збережене значення або None якщо ключ відсутній/протух.
        Не оновлює TTL при читанні.
        """
        entry = self._store.get(key)
        if entry is None:
            return None
        ts, val = entry
        if time.monotonic() - ts > self._ttl:
            del self._store[key]
            return None
        return val

    def set(self, key: Hashable, value: Any) -> None:
        """Зберігає значення. Оновлює TTL якщо ключ вже є."""
        self._put(key, value)

    # ─── Утиліти ──────────────────────────────────────────────────────────────

    def delete(self, key: Hashable) -> None:
        self._store.pop(key, None)

    def clear(self) -> None:
        self._store.clear()

    def __len__(self) -> int:
        return len(self._store)

    def purge_expired(self) -> int:
        """Явне очищення протухлих записів. Повертає кількість видалених."""
        now = time.monotonic()
        expired = [k for k, (ts, _) in self._store.items() if now - ts > self._ttl]
        for k in expired:
            del self._store[k]
        return len(expired)

    # ─── Внутрішня логіка ─────────────────────────────────────────────────────

    def _put(self, key: Hashable, value: Any) -> None:
        now = time.monotonic()

        # Якщо ключ вже є — переміщуємо в кінець (LRU) і оновлюємо
        if key in self._store:
            del self._store[key]
        elif len(self._store) >= self._max:
            self._evict(now)

        self._store[key] = (now, value)

    def _evict(self, now: float) -> None:
        """
        Двохетапна евікція:
        1. Видаляємо протухлі записи
        2. Якщо все ще переповнено — видаляємо найстаріші (LRU з початку OrderedDict)
        """
        # Крок 1: протухлі
        expired = [k for k, (ts, _) in self._store.items() if now - ts > self._ttl]
        for k in expired:
            del self._store[k]

        # Крок 2: LRU якщо все ще >= max
        while len(self._store) >= self._max:
            self._store.popitem(last=False)