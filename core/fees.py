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


# === РЕЄСТР МАРШРУТІВ (Біржа_Банк ➔ Біржа_Банк) ===
# Комісія мережі (TRC20 ~ 1 USDT = ~40 UAH)
CRYPTO_TRANSFER_FEE = FixedFee("40.0", "Комісія мережі TRC20 (~1 USDT)")
PRIVAT_FEE = PercentFee("0.5", "ПриватБанк (0.5%)")
PUMB_FEE = PercentFee("0.5", "ПУМБ (0.5%)")

ROUTES = {
    # ── Bybit internal ───────────────────────────────────────────────────────
    "Bybit_43_to_Bybit_43": FeeCalculator([]),
    "Bybit_43_to_Bybit_14": FeeCalculator([]),
    "Bybit_14_to_Bybit_43": FeeCalculator([PRIVAT_FEE]),
    "Bybit_64_to_Bybit_43": FeeCalculator([PUMB_FEE]),

    # ── Bybit ↔ OKX ──────────────────────────────────────────────────────────
    "Bybit_43_to_OKX_43":   FeeCalculator([CRYPTO_TRANSFER_FEE]),
    "Bybit_43_to_OKX_14":   FeeCalculator([CRYPTO_TRANSFER_FEE]),
    "Bybit_14_to_OKX_43":   FeeCalculator([PRIVAT_FEE, CRYPTO_TRANSFER_FEE]),
    "OKX_43_to_Bybit_43":   FeeCalculator([CRYPTO_TRANSFER_FEE]),
    "OKX_14_to_Bybit_43":   FeeCalculator([PRIVAT_FEE, CRYPTO_TRANSFER_FEE]),

    # ── Bybit ↔ Binance ───────────────────────────────────────────────────────
    # Binance P2P — безкоштовний вивід USDT (TRC20) до 3 разів/міс
    # Решта — CRYPTO_TRANSFER_FEE як у всіх
    "Binance_43_to_Bybit_43":  FeeCalculator([CRYPTO_TRANSFER_FEE]),
    "Binance_14_to_Bybit_43":  FeeCalculator([PRIVAT_FEE, CRYPTO_TRANSFER_FEE]),
    "Binance_43_to_Bybit_14":  FeeCalculator([CRYPTO_TRANSFER_FEE]),
    "Bybit_43_to_Binance_43":  FeeCalculator([CRYPTO_TRANSFER_FEE]),
    "Bybit_14_to_Binance_43":  FeeCalculator([PRIVAT_FEE, CRYPTO_TRANSFER_FEE]),

    # ── OKX ↔ Binance ────────────────────────────────────────────────────────
    "Binance_43_to_OKX_43":    FeeCalculator([CRYPTO_TRANSFER_FEE]),
    "Binance_14_to_OKX_43":    FeeCalculator([PRIVAT_FEE, CRYPTO_TRANSFER_FEE]),
    "OKX_43_to_Binance_43":    FeeCalculator([CRYPTO_TRANSFER_FEE]),

    # ── Wallet ↔ всі ─────────────────────────────────────────────────────────
    # Wallet не має власного гаманця — завжди потребує переказу
    "Wallet_43_to_Bybit_43":   FeeCalculator([CRYPTO_TRANSFER_FEE]),
    "Wallet_43_to_OKX_43":     FeeCalculator([CRYPTO_TRANSFER_FEE]),
    "Wallet_43_to_Binance_43": FeeCalculator([CRYPTO_TRANSFER_FEE]),
    "Bybit_43_to_Wallet_43":   FeeCalculator([CRYPTO_TRANSFER_FEE]),
    "OKX_43_to_Wallet_43":     FeeCalculator([CRYPTO_TRANSFER_FEE]),
    "Binance_43_to_Wallet_43": FeeCalculator([CRYPTO_TRANSFER_FEE]),

    # ── CryptoBot ↔ всі ──────────────────────────────────────────────────────
    # CryptoBot — внутрішній гаманець Telegram, вивід через TON/TRC20
    "CryptoBot_43_to_Bybit_43":    FeeCalculator([CRYPTO_TRANSFER_FEE]),
    "CryptoBot_43_to_OKX_43":      FeeCalculator([CRYPTO_TRANSFER_FEE]),
    "CryptoBot_43_to_Binance_43":  FeeCalculator([CRYPTO_TRANSFER_FEE]),
    "CryptoBot_43_to_Wallet_43":   FeeCalculator([CRYPTO_TRANSFER_FEE]),
    "CryptoBot_14_to_Bybit_43":    FeeCalculator([PRIVAT_FEE, CRYPTO_TRANSFER_FEE]),
    "Bybit_43_to_CryptoBot_43":    FeeCalculator([CRYPTO_TRANSFER_FEE]),
    "OKX_43_to_CryptoBot_43":      FeeCalculator([CRYPTO_TRANSFER_FEE]),
    "Binance_43_to_CryptoBot_43":  FeeCalculator([CRYPTO_TRANSFER_FEE]),
}


def get_calculator(buy_bank: str, sell_bank: str, buy_ex: str, sell_ex: str) -> FeeCalculator:
    """Визначає маршрут з урахуванням бірж та банків."""
    route_key = f"{buy_ex}_{buy_bank}_to_{sell_ex}_{sell_bank}"

    # Якщо маршрут не описаний явно, але біржі різні — додаємо комісію мережі за замовчуванням
    if route_key not in ROUTES:
        if buy_ex != sell_ex:
            return FeeCalculator([CRYPTO_TRANSFER_FEE])
        return FeeCalculator([])

    return ROUTES.get(route_key)