# core/utils/dedup_cache.py
"""
Дедуплікація спредів — тонка обгортка над TTLCache.
Логіка seen/mark залишилась незмінною, реалізація — в core/utils/cache.py.
"""
from config import settings
from core.utils.cache import TTLCache

# Re-export для зворотної сумісності — весь код що робить
# `from core.utils.dedup_cache import TTLCache` продовжує працювати
__all__ = ["TTLCache", "build_dedup_key"]


def build_dedup_key(side: str, order_id: str, price: str, available_amount: str = "") -> str:
    """Будує ключ дедуплікації відповідно до налаштувань."""
    if settings.dedup_strict:
        return f"{side}:{order_id}:{price}:{available_amount}"
    return f"{side}:{order_id}:{price}"