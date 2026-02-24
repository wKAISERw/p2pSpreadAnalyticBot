from abc import ABC, abstractmethod
from decimal import Decimal
from dataclasses import dataclass

@dataclass
class FeeResult:
    amount: Decimal
    description: str

class BaseFee(ABC):
    @abstractmethod
    def calculate(self, current_amount: Decimal) -> FeeResult:
        pass

class FixedFee(BaseFee):
    def __init__(self, fee_amount: str, description: str):
        self._fee_amount = Decimal(fee_amount)
        self._description = description

    def calculate(self, current_amount: Decimal) -> FeeResult:
        actual_fee = min(self._fee_amount, current_amount)
        return FeeResult(actual_fee, self._description)

class PercentFee(BaseFee):
    def __init__(self, rate_pct: str, description: str):
        self._rate = Decimal(rate_pct) / Decimal("100.0")
        self._description = description

    def calculate(self, current_amount: Decimal) -> FeeResult:
        fee = current_amount * self._rate
        return FeeResult(fee, self._description)

class FeeCalculator:
    def __init__(self, fees: list[BaseFee]):
        self._fees = fees

    def calculate_net(self, initial_amount: Decimal) -> tuple[Decimal, Decimal, list[FeeResult]]:
        current_amount = initial_amount
        results = []
        for fee in self._fees:
            fee_result = fee.calculate(current_amount)
            current_amount -= fee_result.amount
            results.append(fee_result)
        total_fee = initial_amount - current_amount
        return current_amount, total_fee, results

BANK_ROUTES = {
    "43_to_43": FeeCalculator([]),
    "14_to_14": FeeCalculator([]),
    "64_to_64": FeeCalculator([]),
    "43_to_14": FeeCalculator([]),
    "43_to_64": FeeCalculator([]),
    "14_to_43": FeeCalculator([PercentFee("0.5", "ПриватБанк міжбанк (0.5%)")]),
    "14_to_64": FeeCalculator([PercentFee("0.5", "ПриватБанк міжбанк (0.5%)")]),
    "64_to_43": FeeCalculator([PercentFee("0.5", "ПУМБ переказ (0.5%)")]),
    "64_to_14": FeeCalculator([PercentFee("0.5", "ПУМБ переказ (0.5%)")]),
}

def get_calculator(buy_bank: str, sell_bank: str) -> FeeCalculator:
    route_key = f"{buy_bank}_to_{sell_bank}"
    return BANK_ROUTES.get(route_key, FeeCalculator([]))