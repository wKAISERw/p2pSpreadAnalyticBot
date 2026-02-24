import logging
import statistics
from decimal import Decimal
from typing import List

from exchanges.base import Order
from config import settings

logger = logging.getLogger(__name__)


class AnomalyFilter:
    def __init__(self, method: str = settings.anomaly_method,
                 multiplier: float = settings.anomaly_threshold_multiplier):
        self.method = method
        self.multiplier = Decimal(str(multiplier))

    def filter_orders(self, orders: List[Order]) -> List[Order]:
        # Збільшили поріг до 5 для адекватної математичної вибірки
        if not orders or len(orders) < 5:
            return orders

        prices = [order.price for order in orders]

        if self.method == "mad":
            return self._filter_by_mad(orders, prices)
        elif self.method == "mean":
            return self._filter_by_mean(orders, prices)
        else:
            logger.warning("⚠️ Невідомий метод аномалій: %s. Фільтр вимкнено.", self.method)
            return orders

    def _filter_by_mad(self, orders: List[Order], prices: List[Decimal]) -> List[Order]:
        # Явний каст у float для безпечної роботи statistics (Python 3.11+)
        float_prices = [float(p) for p in prices]
        median_price = Decimal(str(statistics.median(float_prices)))

        float_deviations = [abs(p - float(median_price)) for p in float_prices]
        mad = Decimal(str(statistics.median(float_deviations)))

        if mad == Decimal("0"):
            return orders

        threshold = mad * self.multiplier

        valid_orders = []
        for order in orders:
            if abs(order.price - median_price) <= threshold:
                valid_orders.append(order)
            else:
                logger.debug("🗑 Відкинуто аномалію (MAD): %s ₴ (Мерчант: %s)", order.price, order.merchant_name)

        return valid_orders

    def _filter_by_mean(self, orders: List[Order], prices: List[Decimal]) -> List[Order]:
        float_prices = [float(p) for p in prices]
        mean_price = Decimal(str(statistics.mean(float_prices)))
        stdev = Decimal(str(statistics.stdev(float_prices)))

        if stdev == Decimal("0"):
            return orders

        threshold = stdev * self.multiplier

        valid_orders = []
        for order in orders:
            if abs(order.price - mean_price) <= threshold:
                valid_orders.append(order)
            else:
                logger.debug("🗑 Відкинуто аномалію (Mean): %s ₴ (Мерчант: %s)", order.price, order.merchant_name)

        return valid_orders