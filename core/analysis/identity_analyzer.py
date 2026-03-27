# core/analysis/identity_analyzer.py

import hashlib
from dataclasses import dataclass, field
from typing import Any, Dict, List

# 🚀 НОВЕ: Відносна похибка для лімітів 2% (замість 1 копійки)
LIMIT_TOLERANCE_PCT = 0.02
PRICE_BAND_TOLERANCE = 0.005  # ±0.5% для ціни

@dataclass(slots=True)
class IdentityResult:
    is_twin: bool = False
    twin_exchanges: List[str] = field(default_factory=list)
    reason: str = ""

def _to_float(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default

def _get_hash(terms: str) -> str:
    if not terms:
        return ""
    return hashlib.md5(terms.encode('utf-8')).hexdigest()

def _limits_match(c_val: float, s_val: float) -> bool:
    """Порівнює ліміти з допустимою похибкою 2%."""
    if s_val == 0: return False
    return abs(c_val - s_val) / s_val <= LIMIT_TOLERANCE_PCT

def analyze_identity(current_order, twins_snapshots: List[Dict[str, Any]]) -> IdentityResult:
    if not twins_snapshots:
        return IdentityResult()

    c_min = _to_float(getattr(current_order, "min_limit", 0.0))
    c_max = _to_float(getattr(current_order, "max_limit", 0.0))
    c_price = _to_float(getattr(current_order, "price", 0.0))
    c_terms = getattr(current_order, "trade_terms", "") or ""
    c_terms_hash = getattr(current_order, "terms_hash", "") or _get_hash(c_terms)

    if c_min <= 0 or c_max <= 0 or c_price <= 0 or not c_terms_hash:
        return IdentityResult()

    matched = set()

    for snap in twins_snapshots:
        s_min = _to_float(snap.get("min_limit", 0.0))
        s_max = _to_float(snap.get("max_limit", 0.0))
        s_price = _to_float(snap.get("price", 0.0))
        s_terms_hash = snap.get("terms_hash", "")

        # 1. Збіг лімітів (відносний)
        limits_match = _limits_match(c_min, s_min) and _limits_match(c_max, s_max)

        # 2. Збіг ціни (±0.5%)
        price_match = False
        if s_price > 0:
            price_match = abs(c_price - s_price) / s_price <= PRICE_BAND_TOLERANCE

        # 3. Збіг тексту умов
        hash_match = bool(s_terms_hash) and c_terms_hash == s_terms_hash

        if limits_match and price_match and hash_match:
            matched.add(snap.get("exchange", "Unknown"))

    if matched:
        ex_str = ",".join(sorted(matched))
        return IdentityResult(is_twin=True, twin_exchanges=list(matched), reason=f"CLONES:{ex_str}")

    return IdentityResult()