# core/identity_analyzer.py

from dataclasses import dataclass, field
from typing import Any, Dict, List

LIMIT_EPSILON = 0.01

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

def analyze_identity(current_order, twins_snapshots: List[Dict[str, Any]]) -> IdentityResult:
    """
    Порівнює поточний ордер з унікальними станами двійників.
    """
    if not twins_snapshots:
        return IdentityResult()

    c_min = _to_float(getattr(current_order, "min_limit", 0.0))
    c_max = _to_float(getattr(current_order, "max_limit", 0.0))

    if c_min <= 0 or c_max <= 0:
        return IdentityResult()

    matched = set()

    for snap in twins_snapshots:
        s_min = _to_float(snap.get("min_limit", 0.0))
        s_max = _to_float(snap.get("max_limit", 0.0))

        if abs(c_min - s_min) <= LIMIT_EPSILON and abs(c_max - s_max) <= LIMIT_EPSILON:
            matched.add(snap["exchange"])

    if matched:
        ex_str = ",".join(sorted(matched))
        return IdentityResult(
            is_twin=True,
            twin_exchanges=list(matched),
            reason=f"CLONES:{ex_str}"
        )

    return IdentityResult()