# core/behavioral_analyzer.py
from __future__ import annotations
from dataclasses import dataclass, field
from typing import Any, Dict, List

MIN_SNAPSHOTS = 3
LIMIT_EPSILON = 0.01
EXACT_TOLERANCE = 5.0  # 🚀 НОВЕ: ловимо трюк з різницею в 1-5 грн (напр. 4991-4992)
# Нова вага балів
API_REPLENISH_SCORE = 50   # 100% бот, який авто-поповнює об'єм
STATIC_DROP_SCORE = 30     # Висить фіксований дроп (напр. 4150-4150) і чекає
VELOCITY_SPIKE_SCORE = 30  # Дуже швидка накрутка

STICKY_MIN_CHAIN = 3
VELOCITY_MIN_WINDOW_HOURS = 0.16   # ~10 хв
VELOCITY_SPIKE_PER_HOUR = 20.0
BEHAVIOR_LLM_THRESHOLD = 60

@dataclass(slots=True)
class BehavioralResult:
    score: int = 0
    flags: List[str] = field(default_factory=list)
    reason: str = ""
    needs_llm: bool = False

def _to_float(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default

def _to_int(value: Any, default: int = 0) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default

def _is_exact_limits(min_lim: float, max_lim: float) -> bool:
    # 🚀 НОВЕ: Тепер різниця до 5 грн вважається EXACT_LIMITS
    return min_lim > 0 and max_lim > 0 and abs(max_lim - min_lim) <= EXACT_TOLERANCE

def _same_limit_pair(a_min: float, a_max: float, b_min: float, b_max: float) -> bool:
    # А тут залишаємо EPSILON, бо боти тримають ліміти копійка в копійку стабільно
    return abs(a_min - b_min) <= LIMIT_EPSILON and abs(a_max - b_max) <= LIMIT_EPSILON

def analyze_history(current_order, snapshots: List[Dict[str, Any]]) -> BehavioralResult:
    result = BehavioralResult()
    min_lim = _to_float(getattr(current_order, "min_limit", 0.0))
    max_lim = _to_float(getattr(current_order, "max_limit", 0.0))

    if min_lim <= 0 or max_lim <= 0:
        return result

    # 🚀 ФІКС 1: МИТТЄВА ДЕТЕКЦІЯ (не чекаємо 3 снапшоти)
    is_exact = _is_exact_limits(min_lim, max_lim)
    if is_exact:
        result.flags.append("EXACT_LIMITS")

    # Якщо снапшотів мало, повертаємо тільки результат миттєвої перевірки
    if not snapshots or len(snapshots) < MIN_SNAPSHOTS:
        return result

    snapshots = sorted(snapshots, key=lambda s: _to_float(s.get("recorded_at", 0.0)))
    oldest = snapshots[0]
    newest = snapshots[-1]
    delta_orders = _to_int(newest.get("order_count", 0)) - _to_int(oldest.get("order_count", 0))
    time_span_hours = max(0.001, (_to_float(newest.get("recorded_at", 0.0)) - _to_float(oldest.get("recorded_at", 0.0))) / 3600.0)

    sticky_count = 0
    for snap in reversed(snapshots):
        if _same_limit_pair(_to_float(snap.get("min_limit")), _to_float(snap.get("max_limit")), min_lim, max_lim):
            sticky_count += 1
        else:
            break

    # 🚀 ФІКС 2: ПРАПОРЦІ БІЛЬШЕ НЕ ВЗАЄМОВИКЛЮЧНІ
    if sticky_count >= STICKY_MIN_CHAIN:
        if delta_orders > 0:
            result.score += API_REPLENISH_SCORE
            result.flags.append(f"API_REPLENISH:{sticky_count}")
        elif is_exact:
            result.score += STATIC_DROP_SCORE
            result.flags.append(f"STATIC_DROP:{sticky_count}")

    if time_span_hours >= VELOCITY_MIN_WINDOW_HOURS and delta_orders > 0:
        velocity = delta_orders / time_span_hours
        if velocity >= VELOCITY_SPIKE_PER_HOUR:
            result.score += VELOCITY_SPIKE_SCORE
            result.flags.append(f"VELOCITY_SPIKE:{velocity:.1f}/h")

    result.needs_llm = result.score >= BEHAVIOR_LLM_THRESHOLD

    if result.flags:
        parts = [f"flags={','.join(result.flags)}", f"score={result.score}"]
        if sticky_count >= STICKY_MIN_CHAIN:
            parts.append(f"sticky={sticky_count}")
        if time_span_hours >= VELOCITY_MIN_WINDOW_HOURS and delta_orders > 0:
            velocity = delta_orders / time_span_hours
            parts.append(f"orders_delta={delta_orders}")
            parts.append(f"velocity={velocity:.1f}/h")
        result.reason = " | ".join(parts)

    return result

