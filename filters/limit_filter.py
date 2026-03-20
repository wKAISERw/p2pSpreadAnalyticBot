# filters/limit_filter.py
from decimal import Decimal
from exchanges.base import Order
from config import settings
from config.runtime import runtime_config


class LimitFilter:
    def __init__(self, min_usdt: float = getattr(settings, "min_usdt_threshold", 50.0)):
        # min_usdt зазвичай статичний, але можна винести теж
        self.min_usdt = Decimal(str(min_usdt))

    def passed(self, order: Order) -> bool:
        # 🚀 ДИНАМІЧНИЙ КАПІТАЛ: беремо на льоту з runtime_config
        current_capital = Decimal(
            str(runtime_config.get("working_capital_uah", settings.working_capital_uah))
        )

        # Відкидаємо ТІЛЬКИ якщо наш капітал менший за мінімальний поріг входу мерчанта
        if current_capital < order.min_limit:
            return False

        # Відкидаємо, якщо в нього залишилися копійки (менше нашого мінімуму USDT)
        if order.available_amount < self.min_usdt:
            return False

        return True