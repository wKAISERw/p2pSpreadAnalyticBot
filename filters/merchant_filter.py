# filters/merchant_filter.py
"""
MerchantFilter — приймає рішення на основі risk_flag від RiskEngine.

Режими (RISK_MODE в .env):
  STRICT  — блокує все крім "OK" та "EMPTY_TERMS"
  WARNING — пропускає все, але зберігає risk_flag для відображення в TG
"""
import logging
from exchanges.base import Order

logger = logging.getLogger("MerchantFilter")

# Флаги які вважаються безпечними в обох режимах
SAFE_FLAGS = {"OK", "EMPTY_TERMS", ""}


class MerchantFilter:
    def __init__(
        self,
        risk_mode: str = "WARNING",   # "STRICT" або "WARNING"
        blocked_names: list[str] = None,
    ):
        self.risk_mode = risk_mode.upper()
        self.blocked_names = set(blocked_names or [])

    def passed(self, order: Order) -> bool:
        """
        Повертає True якщо ордер проходить через фільтр.
        В режимі WARNING завжди True (крім заблокованих).
        В режимі STRICT блокує ризикові флаги.
        """
        # Завжди блокуємо себе
        if order.merchant_name in self.blocked_names:
            return False

        # WARNING — пропускаємо все, risk_flag видно в TG
        if self.risk_mode == "WARNING":
            return True

        # STRICT — блокуємо ризикові
        if order.risk_flag not in SAFE_FLAGS:
            logger.debug("🚫 STRICT блок: %s [%s] → %s",
                         order.merchant_name, order.exchange, order.risk_flag)
            return False

        return True