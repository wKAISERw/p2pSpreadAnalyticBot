from exchanges.base import Order


class BankFilter:
    def __init__(self, target_banks: list[str]):
        self.target_banks = target_banks

    def passed(self, order: Order) -> bool:
        # TODO: Коли додамо payment_methods до моделі Order (у base.py та bybit.py),
        # розкоментувати цей рядок:
        # return any(b in order.payment_methods for b in self.target_banks)

        # Зараз Bybit надійно фільтрує банки через payload, тому довіряємо серверу.
        return True