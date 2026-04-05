# exchanges/base.py
from dataclasses import dataclass, field
from typing import List, Tuple
from abc import ABC, abstractmethod
from decimal import Decimal


@dataclass
class Order:
    """Єдина модель ордера для всієї системи."""
    id: str
    price: Decimal
    available_amount: Decimal
    min_limit: Decimal
    max_limit: Decimal
    merchant_id: str
    merchant_name: str
    month_order_count: int
    finish_rate_pct: float
    exchange: str = "Bybit"
    link: str = ""
    bank_codes: list[str] = field(default_factory=list)

    # ── Risk Engine поля ──────────────────────────────────────────────────
    # Умови угоди від мерчанта (нижній регістр, нормалізовані)
    trade_terms: str = ""
    # Вирок RiskEngine: "OK", "TRIANGLE", "CASINO", "LOW_STATS", "EMPTY_TERMS"
    risk_flag: str = ""
    # Верифікований мерчант (жовта/синя галочка де доступно)
    is_verified: bool = False
# ── НОВІ ПОЛЯ (ДОДАТИ СЮДИ) ──
    account_age_days: int = 0
    composite_score: int = 0
    review_score: int = 0
    review_neg_pct: float = 0.0
    review_fetched: bool = False
    # Binance: positiveRate (% позитивних відгуків, 0.0–1.0) — відрізняється від finish_rate_pct!
    positive_rate: float = 0.0
    # Сторона ордера: "buy" (мерчант купує USDT) або "sell" (мерчант продає USDT)
    side: str = ""

class BaseExchange(ABC):
    """Абстрактний клас (Інтерфейс) для всіх бірж."""

    @abstractmethod
    async def get_buy_orders(self, amount: float, banks: List[str]) -> List[Order]:
        """Отримати ордери конкурентів (ті, хто продає USDT)."""
        pass

    @abstractmethod
    async def get_sell_orders(self, amount: float, banks: List[str]) -> List[Order]:
        """Отримати ордери тих, хто скуповує USDT."""
        pass

    @abstractmethod
    async def fetch_both_multi(self, amounts: list[float], banks: List[str]) -> Tuple[List[Order], List[Order]]:
        """Паралельний мульти-запит для максимальної швидкості."""
        pass

    @staticmethod
    def dedup(orders: "List[Order]") -> "List[Order]":
        """
        Дедуплікація ордерів по id.
        Замінює _dedup/_dedup_by_id у всіх exchange файлах.
        """
        seen: set = set()
        result = []
        for order in orders:
            if order.id not in seen:
                seen.add(order.id)
                result.append(order)
        return result