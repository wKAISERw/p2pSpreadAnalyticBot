# filters/price_filter.py
"""
PriceRangeFilter — фільтрація ордерів по діапазону ціни.

Підтримує 4 режими:
  - range: ціна в діапазоні [min, max]
  - exact: ціна ≈ value (допуск ±0.01)
  - max:   ціна ≤ value
  - min:   ціна ≥ value

Конфіг зберігається в scanner_users.price_range_json:
  {"mode": "range", "min": 42.0, "max": 42.8}
  {"mode": "exact", "value": 42.5}
  {"mode": "max", "value": 42.8}
  {"mode": "min", "value": 42.0}
  {} — фільтр вимкнений
"""
from __future__ import annotations

import logging
from decimal import Decimal
from typing import Optional

from exchanges.base import Order

logger = logging.getLogger("PriceFilter")

# Допуск для exact-режиму (±0.01 UAH)
EXACT_TOLERANCE = 0.01


class PriceRangeFilter:
    """Фільтрує ордери за ціновим діапазоном юзера."""

    def __init__(self, config: dict):
        """
        :param config: dict з price_range_json юзера
            {} — вимкнений, {"mode": "range", "min": 42.0, "max": 42.8}, etc.
        """
        self._mode: str = config.get("mode", "")
        self._min: float = float(config.get("min", 0))
        self._max: float = float(config.get("max", 0))
        self._value: float = float(config.get("value", 0))

    @property
    def is_active(self) -> bool:
        """True якщо фільтр налаштований і активний."""
        return bool(self._mode)

    def matches(self, order: Order) -> bool:
        """
        Перевіряє чи ордер проходить фільтр по ціні.
        Якщо фільтр не активний — пропускає все.
        """
        if not self._mode:
            return True

        price = float(order.price)

        if self._mode == "range":
            return self._min <= price <= self._max

        if self._mode == "exact":
            return abs(price - self._value) <= EXACT_TOLERANCE

        if self._mode == "max":
            return price <= self._value

        if self._mode == "min":
            return price >= self._value

        # Невідомий режим — пропускаємо
        return True

    def describe(self) -> str:
        """Людський опис фільтра для UI."""
        if not self._mode:
            return "вимкнено"
        if self._mode == "range":
            return f"{self._min:.2f} – {self._max:.2f} ₴"
        if self._mode == "exact":
            return f"= {self._value:.2f} ₴"
        if self._mode == "max":
            return f"≤ {self._value:.2f} ₴"
        if self._mode == "min":
            return f"≥ {self._value:.2f} ₴"
        return "?"

    @staticmethod
    def validate_config(config: dict) -> Optional[str]:
        """
        Валідує конфіг. Повертає None якщо ОК, або текст помилки.
        """
        mode = config.get("mode", "")
        if not mode:
            return None  # Вимкнений — ОК

        if mode == "range":
            mn = config.get("min")
            mx = config.get("max")
            if mn is None or mx is None:
                return "Потрібні поля min і max"
            try:
                mn, mx = float(mn), float(mx)
            except (TypeError, ValueError):
                return "min і max мають бути числами"
            if mn <= 0 or mx <= 0:
                return "Значення мають бути > 0"
            if mn >= mx:
                return "min має бути менше max"
            return None

        if mode in ("exact", "max", "min"):
            val = config.get("value")
            if val is None:
                return "Потрібне поле value"
            try:
                val = float(val)
            except (TypeError, ValueError):
                return "value має бути числом"
            if val <= 0:
                return "value має бути > 0"
            return None

        return f"Невідомий режим: {mode}"

