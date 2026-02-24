from decimal import Decimal
from exchanges.base import Order
from config import settings


class LimitFilter:
    def __init__(self, capital: float = settings.working_capital_uah, min_usdt: float = settings.min_usdt_threshold):
        self.capital = Decimal(str(capital))
        self.min_usdt = Decimal(str(min_usdt))

    def passed(self, order: Order) -> bool:
        # Відкидаємо ТІЛЬКИ якщо наш капітал менший за мінімальний поріг входу мерчанта
        if self.capital < order.min_limit:
            return False

        # Відкидаємо, якщо в нього залишилися копійки (менше нашого мінімуму USDT)
        if order.available_amount < self.min_usdt:
            return False

        return True