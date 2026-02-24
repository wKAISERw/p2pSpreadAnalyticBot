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

    # ВИПРАВЛЕНО ТУТ: Тепер інтерфейс вимагає мульти-запит
    @abstractmethod
    async def fetch_both_multi(self, amounts: list[float], banks: List[str]) -> Tuple[List[Order], List[Order]]:
        """Паралельний мульти-запит для максимальної швидкості."""
        pass