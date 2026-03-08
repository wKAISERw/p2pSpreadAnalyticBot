# filters/merchant_filter.py
"""
MerchantFilter — приймає рішення на основі risk_flag від RiskEngine.

Режими:
- WARNING: пропускає все, крім ручного blocked_names
- STRICT: блокує тільки hard-block флаги
"""

import logging
from exchanges.base import Order

logger = logging.getLogger("MerchantFilter")

SAFE_FLAGS = {"OK", "EMPTY_TERMS", "", "PENDING"}

SAFE_PREFIXES = (
    "REGEX_WEAK:",
    "LLM_PENDING:",
)

SOFT_WARNING_FLAGS = {
    "LOW_STATS",
    "PERFECT_RATING",
    "SUSPICIOUS_LIMITS",
    "HIGH_RISK_SCORE",
    "LLM_UNKNOWN",
    "LLM_SUSPICIOUS",
}

HARD_BLOCK_PREFIXES = (
    "BLOCK:",
)

HARD_BLOCK_EXACT = {
    "BLOCK:CACHED",
}


class MerchantFilter:
    def __init__(
        self,
        risk_mode: str = "WARNING",
        blocked_names: list[str] | None = None,
    ):
        self.risk_mode = risk_mode.upper()
        self.blocked_names = set(blocked_names or [])

    def passed(self, order: Order) -> bool:
        if order.merchant_name in self.blocked_names:
            return False

        if self.risk_mode == "WARNING":
            return True

        risk_flag = (order.risk_flag or "").strip()

        if not risk_flag or risk_flag in SAFE_FLAGS:
            return True

        parts = [p.strip() for p in risk_flag.split(",") if p.strip()]

        for part in parts:
            if part in HARD_BLOCK_EXACT:
                logger.debug(
                    "🚫 STRICT hard block: %s [%s] → %s",
                    order.merchant_name, order.exchange, part
                )
                return False

            if any(part.startswith(prefix) for prefix in HARD_BLOCK_PREFIXES):
                logger.debug(
                    "🚫 STRICT hard block: %s [%s] → %s",
                    order.merchant_name, order.exchange, part
                )
                return False

        for part in parts:
            if part in SOFT_WARNING_FLAGS:
                continue
            if any(part.startswith(prefix) for prefix in SAFE_PREFIXES):
                continue

            logger.debug(
                "⚠️ STRICT unknown risk flag, пропускаємо як soft: %s [%s] → %s",
                order.merchant_name, order.exchange, part
            )

        return True
