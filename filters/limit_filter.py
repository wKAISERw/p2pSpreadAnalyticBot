# filters/limit_filter.py
"""
LimitFilter — відсіює ордери де:
  1. Мін. ліміт мерчанта перевищує будь-який активний капітал
  2. Залишок USDT занадто малий

НЕ фільтрує по конкретному капіталу юзера — це робить AlertDispatcher.
Тут ми тільки прибираємо явно непридатні ордери (напр. мерчант хоче від 100к).
"""
from decimal import Decimal
from exchanges.base import Order
from config import settings

# Глобальний кеш максимального капіталу серед активних юзерів
# Оновлюється scanner.py на кожному циклі через set_max_capital()
_max_capital: Decimal = Decimal(str(getattr(settings, "working_capital_uah", 5100.0)))


def set_max_capital(capital_uah: float) -> None:
    """Встановлює максимальний капітал серед активних юзерів. Викликається з scanner.py."""
    global _max_capital
    _max_capital = Decimal(str(capital_uah))


class LimitFilter:
    def __init__(self, min_usdt: float = getattr(settings, "min_usdt_threshold", 50.0)):
        self.min_usdt = Decimal(str(min_usdt))

    def passed(self, order: Order) -> bool:
        # Відкидаємо тільки якщо навіть найбагатший юзер не може зайти
        if _max_capital < order.min_limit:
            return False

        # Відкидаємо якщо залишилися копійки USDT
        if order.available_amount < self.min_usdt:
            return False

        return True