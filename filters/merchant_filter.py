from exchanges.base import Order


class MerchantFilter:
    # Встановили жорсткі ліміти: мінімум 50 угод за місяць і 95% успішних завершень
    def __init__(self, min_orders: int = 50, min_finish_rate: float = 95.0, blocked_names: list[str] = None):
        self.min_orders = min_orders
        self.min_finish_rate = min_finish_rate
        self.blocked_names = blocked_names or []  # Тут згодом зможеш вписати свій нік

    def passed(self, order: Order) -> bool:
        """Повертає True, якщо мерчант надійний як швейцарський банк."""
        # 1. Відсікаємо себе та заблокованих
        if order.merchant_name in self.blocked_names:
            return False

        # 2. Відсікаємо новачків (менше 50 угод)
        if order.month_order_count < self.min_orders:
            return False

        # 3. Відсікаємо тих, хто часто скасовує або кидає в реф (успішність нижче 95%)
        if order.finish_rate_pct < self.min_finish_rate:
            return False

        return True