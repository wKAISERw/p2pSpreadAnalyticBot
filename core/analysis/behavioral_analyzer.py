# core/analysis/behavioral_analyzer.py
from __future__ import annotations
from dataclasses import dataclass, field
from typing import Any, Dict, List

from config.defaults import (
    LIMIT_EPSILON, STICKY_MIN_CHAIN,
    VELOCITY_MIN_WINDOW_HOURS, VELOCITY_SPIKE_PER_HOUR,
    BEHAVIOR_LLM_THRESHOLD,
)

MIN_SNAPSHOTS = 3

# Бали детекторів — тут бо специфічні для behavioral шару
API_REPLENISH_SCORE = 50   # бот що авто-поповнює об'єм: sticky + delta > 0
STATIC_DROP_SCORE   = 30   # фіксований дроп: sticky + exact + delta == 0
VELOCITY_SPIKE_SCORE = 30  # аномальна швидкість угод

# EXACT_TOLERANCE: різниця до 5 грн вважається "рівними" лімітами
# (напр. 4991–4992 — класичний трюк ботів)
EXACT_TOLERANCE = 5.0

# FLICKER_RELIST: параметри детектора зникнення/повернення
FLICKER_MIN_GAP_MIN   = 10     # мін. пауза між сесіями (хв) — менше це не зникнення
FLICKER_MAX_GAP_HOURS = 6.0    # макс. пауза — більше це просто офлайн на ніч
FLICKER_SCORE         = 15     # ЗНИЖЕНО: бал за кожен flicker (щоб не спамило LLM)   # бал за кожен flicker (додається до загального)

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

    # ── 3. FLICKER_RELIST ────────────────────────────────────────────────────
    # Мерчант зникає і повертається з тим самим terms_hash за короткий час.
    flicker_count = _detect_flicker(snapshots)
    if flicker_count > 1:  # 🚀 ХОТФІКС 3: Реагуємо ТІЛЬКИ якщо блимав хоча б 2 рази!
        result.score += FLICKER_SCORE * flicker_count
        result.flags.append(f"FLICKER_RELIST:{flicker_count}")

    result.needs_llm = result.score >= BEHAVIOR_LLM_THRESHOLD

    if result.flags:
        parts = [f"flags={','.join(result.flags)}", f"score={result.score}"]
        if sticky_count >= STICKY_MIN_CHAIN:
            parts.append(f"sticky={sticky_count}")
        if time_span_hours >= VELOCITY_MIN_WINDOW_HOURS and delta_orders > 0:
            velocity = delta_orders / time_span_hours
            parts.append(f"orders_delta={delta_orders}")
            parts.append(f"velocity={velocity:.1f}/h")
        if flicker_count > 0:
            parts.append(f"flicker={flicker_count}")
        result.reason = " | ".join(parts)

    return result


def _detect_flicker(snapshots: List[Dict[str, Any]]) -> int:
    """
    Рахує кількість flicker-подій у history мерчанта.
    Flicker = пауза між двома snapshot-ами більша за FLICKER_MIN_GAP_MIN
              але менша за FLICKER_MAX_GAP_HOURS, при тому що terms_hash збігається.

    Умова terms_hash: виключає звичайні зміни умов — нам цікаво лише
    повернення з ТИМИ САМИМИ умовами (класична поведінка скрипту).
    """
    if len(snapshots) < 2:
        return 0

    min_gap_sec  = FLICKER_MIN_GAP_MIN * 60
    max_gap_sec  = FLICKER_MAX_GAP_HOURS * 3600
    flicker_count = 0

    for i in range(1, len(snapshots)):
        prev = snapshots[i - 1]
        curr = snapshots[i]

        gap = _to_float(curr.get("recorded_at", 0)) - _to_float(prev.get("recorded_at", 0))

        if gap < min_gap_sec or gap > max_gap_sec:
            continue

        prev_hash = prev.get("terms_hash") or ""
        curr_hash = curr.get("terms_hash") or ""

        # Обидва хеші мають бути непорожніми і збігатись
        if prev_hash and curr_hash and prev_hash == curr_hash:
            flicker_count += 1

    return flicker_count