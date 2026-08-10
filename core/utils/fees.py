# core/utils/fees.py
from abc import ABC, abstractmethod
from decimal import Decimal
from dataclasses import dataclass
from config.banks import get_bank_profile, normalize_bank
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


class ThresholdFee(BaseFee):
    """
    Безкоштовно до порогу, далі відсоток (+ фіксована частина).

    Саме так тарифікує більшість українських банків: «до 20 000 — 0%, далі
    1% + 5 ₴». Раніше поріг описувався в коментарі біля константи, але в
    розрахунку не брав участі — переказ на 10к у Sense, де до 20к
    безкоштовно, отримував повну комісію.

    Межа моделі: пороги довідника — місячні, а калькулятор бачить лише один
    переказ. Без історії переказів поріг читається як «на цю операцію», тож
    для користувача, який уже вибрав місячний безкоштовний обсяг, комісія
    вийде заниженою. Полагодити це можна лише разом із лічильником обороту
    по картці — див. PLAN_CARD_MATCHING.md, 7.6.
    """

    def __init__(self, rate_pct: float, fixed_uah: float = 0.0,
                 threshold_uah: float | None = None, description: str = ""):
        self._rate = float(rate_pct) / 100.0
        self._fixed = float(fixed_uah)
        self._threshold = float(threshold_uah) if threshold_uah else None
        self._description = description

    def calculate(self, current_amount: float, usdt_price: float) -> FeeResult:
        if self._threshold is not None and current_amount <= self._threshold:
            return FeeResult(0.0, f"{self._description} — у межах безкоштовного ліміту")
        fee = current_amount * self._rate + min(self._fixed, max(0.0, current_amount))
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


def bank_transfer_fee(buy_bank: str, sell_bank: str) -> BaseFee | None:
    """
    Комісія банку за вихідний переказ фіату — з довідника, а не з `if`-ів.

    Тут стояли чотири константи й ланцюжок `if buy_bank == "14"`, який
    покривав п'ять банків із ~30: для решти комісія дорівнювала нулю.
    Ощадбанк (1% + 5 ₴), KredoBank (0.7% + 2.5 ₴), Таскомбанк (0.5% + 10 ₴)
    система вважала безкоштовними. Заодно ставки розійшлися з практикою в
    обидва боки — А-Банк рахувався як 0.5% там, де насправді 2% після
    порогу, а Monobank платив 0.5% там, де по Україні 0%.

    Тепер ставка одна на весь проєкт — у профілі банку (config/banks.py).
    """
    fee = get_bank_profile(buy_bank).p2p_fee
    if fee is None:
        return None

    # Комісія «лише в інший банк»: Mono→Mono і Приват→Приват безкоштовні.
    if fee.cross_bank_only and normalize_bank(buy_bank) == normalize_bank(sell_bank):
        return None

    if not fee.pct and not fee.fixed_uah:
        return None

    return ThresholdFee(
        rate_pct=fee.pct,
        fixed_uah=fee.fixed_uah,
        threshold_uah=fee.free_until_uah,
        description=fee.label or f"Комісія переказу ({fee.pct}%)",
    )


def get_calculator(buy_bank: str, sell_bank: str, buy_ex: str, sell_ex: str) -> FeeCalculator:
    """
    ДИНАМІЧНИЙ МАРШРУТИЗАТОР КОМІСІЙ.
    Прораховує наживо як вихідні банківські перекази, так і мікро-комісії криптомереж.
    """
    base_fees = []

    # 🧮 1. АНАЛІЗ БАНКІВСЬКОЇ НОГИ (Купівля)
    transfer_fee = bank_transfer_fee(buy_bank, sell_bank)
    if transfer_fee is not None:
        base_fees.append(transfer_fee)

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