# core/utils/fees.py
from abc import ABC, abstractmethod
from decimal import Decimal
from dataclasses import dataclass
from core.engine.network_fee_engine import NetworkFeeEngine  # Інтегруємо двигун мереж


@dataclass
class FeeResult:
    amount: float
    description: str


class BaseFee(ABC):
    @abstractmethod
    def calculate(self, current_amount: float, usdt_price: float) -> FeeResult:
        pass


class FixedFee(BaseFee):
    def __init__(self, fee_amount: str, description: str):
        self._fee_amount = float(fee_amount)
        self._description = description

    def calculate(self, current_amount: float, usdt_price: float) -> FeeResult:
        actual_fee = min(self._fee_amount, current_amount)
        return FeeResult(actual_fee, self._description)


class PercentFee(BaseFee):
    def __init__(self, rate_pct: str, description: str):
        self._rate = float(rate_pct) / 100.0
        self._description = description

    def calculate(self, current_amount: float, usdt_price: float) -> FeeResult:
        fee = current_amount * self._rate
        return FeeResult(fee, self._description)


class NetworkFee(BaseFee):
    def __init__(self, usdt_amount: str, description: str):
        self._usdt_amount = float(usdt_amount)
        self._description = description

    def calculate(self, current_amount: float, usdt_price: float) -> FeeResult:
        fee_in_uah = self._usdt_amount * usdt_price
        return FeeResult(fee_in_uah, self._description)


class FeeCalculator:
    def __init__(self, fees: list[BaseFee]):
        self._fees = fees

    def calculate_net(self, initial_amount: float, usdt_price: float) -> tuple[float, float, list[FeeResult]]:
        current_amount = initial_amount
        results = []
        for fee in self._fees:
            fee_result = fee.calculate(current_amount, usdt_price)
            current_amount -= fee_result.amount
            results.append(fee_result)
        total_fee = initial_amount - current_amount
        return current_amount, total_fee, results


# ── СТАНДАРТНІ ТАРИФИ ДЛЯ ВЕЛИКОГО ОБОРОТУ (P2P ВЛАСНІ КОШТИ) ──
PRIVAT_P2P_FEE = PercentFee("0.5", "ПриватБанк P2P (0.5%)")
CROSS_BANK_FEE = PercentFee("0.5", "Міжбанк / Надліміт P2P (0.5%)")
SENSE_PERCENT = PercentFee("1.0", "Sense Міжбанк (1.0%)")
SENSE_FIXED = FixedFee("5.0", "Sense Фікс (5 ₴)")


def get_calculator(buy_bank: str, sell_bank: str, buy_ex: str, sell_ex: str) -> FeeCalculator:
    """
    ДИНАМІЧНИЙ МАРШРУТИЗАТОР КОМІСІЙ.
    Прораховує наживо як вихідні банківські перекази, так і мікро-комісії криптомереж.
    """
    base_fees = []

    # 🧮 1. АНАЛІЗ БАНКІВСЬКОЇ НОГИ (Купівля)
    # Перевіряємо умови вихідного переказу фіату залежно від банку мерчанта та користувача
    if buy_bank == "14":  # PrivatBank
        # Приват завжди бере 0.5% за переказ на будь-яку картку (свою чи чужу)
        base_fees.append(PRIVAT_P2P_FEE)

    elif buy_bank == "328":  # Sense Bank
        # Поза лімітами пакету Сенс бере 1% + 5 ₴ за міжбанк
        base_fees.append(SENSE_PERCENT)
        base_fees.append(SENSE_FIXED)

    elif buy_bank != sell_bank:
        # КРОС-БАНК (Наприклад: Оплата з Монобанку на ПУМБ або Приват)
        # Оскільки ліміти безкоштовних переказів (20к у Моно/А-Банку) в арбітражі тануть миттєво,
        # безпечно закладати 0.5% на будь-які надлімітні міжбанківські P2P операції.
        # Якщо банки однакові (Mono->Mono завдяки AlertDispatcher), цей блок м'яко пропускається (0%)!
        if buy_bank in ("43", "48", "553"):  # Mono, A-Bank, Izi
            base_fees.append(CROSS_BANK_FEE)

    # 🌐 2. АНАЛІЗ КРИПТО-МЕРЕЖІ (Для CROSS-біржових кіл)
    if buy_ex != sell_ex:
        # Автоматично беремо НАЙДЕШЕВШУ спільну мережу (напр. TON або SOL за 0.01 USDT)
        net_name, net_fee_usdt = NetworkFeeEngine.get_optimal_network(buy_ex, sell_ex)
        dynamic_net_fee = NetworkFee(
            str(net_fee_usdt),
            f"Комісія мережі {net_name} ({net_fee_usdt} USDT)"
        )
        base_fees.append(dynamic_net_fee)

    return FeeCalculator(base_fees)