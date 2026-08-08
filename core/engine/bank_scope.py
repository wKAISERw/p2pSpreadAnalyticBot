"""
Які банки використовує конкретний режим.

Колонок для банків три, і вони спільні для всієї системи:
    bank_codes       — загальні,
    buy_bank_codes   — для купівлі (порожньо → загальні),
    sell_bank_codes  — для продажу (порожньо → загальні).

Проблема була в тому, що майстер Taker Buy записував обраний список просто
в buy_bank_codes. Тобто налаштування банків у тейкері мовчки перевизначало
банки купівлі і для спред-режиму: два режими писали в одну комірку, і той,
хто зберігся останнім, вигравав. Помітити це було майже неможливо —
спред просто починав пропускати частину зв'язок.

Тепер поверх базових списків є необов'язкові перевизначення на режим:

    {"TAKER_BUY": {"buy": ["43"]}, "SPREAD": {"sell": ["14", "43"]}}

Порожньо (типовий випадок) означає «беремо базові» — тобто поведінка
незмінна, поки перевизначення явно не задали.
"""
from __future__ import annotations

SIDES = ("buy", "sell")


def _as_list(value) -> list[str]:
    """Банки приходять і списком, і CSV-рядком — зводимо до списку."""
    if not value:
        return []
    if isinstance(value, (list, tuple, set)):
        return [str(v).strip() for v in value if str(v).strip()]
    return [chunk.strip() for chunk in str(value).split(",") if chunk.strip()]


def base_banks(user: dict, side: str) -> list[str]:
    """Банки без урахування режиму: сторона, інакше загальні."""
    if side not in SIDES:
        raise ValueError(f"side: очікується buy або sell, отримано {side!r}")

    specific = _as_list(user.get(f"{side}_bank_codes"))
    return specific or _as_list(user.get("bank_codes"))


def resolve_banks(user: dict, mode: str, side: str) -> list[str]:
    """
    Остаточний список банків для пари (режим, сторона).

    Перевизначення режиму має пріоритет; якщо його немає — базовий список.
    Порожній список означає «банки не обмежені» рівно так само, як і до
    появи перевизначень.
    """
    overrides = user.get("mode_bank_overrides") or {}
    per_mode = overrides.get(str(mode).upper()) if isinstance(overrides, dict) else None

    if isinstance(per_mode, dict):
        override = _as_list(per_mode.get(side))
        if override:
            return override

    return base_banks(user, side)


def has_override(user: dict, mode: str, side: str) -> bool:
    """Чи задане перевизначення саме для цієї пари — для підписів в UI."""
    overrides = user.get("mode_bank_overrides") or {}
    per_mode = overrides.get(str(mode).upper()) if isinstance(overrides, dict) else None
    return bool(isinstance(per_mode, dict) and _as_list(per_mode.get(side)))


def normalize_overrides(raw: dict, known_modes: tuple[str, ...]) -> dict:
    """
    Чистить структуру перед записом: лише відомі режими й сторони, лише
    непорожні списки. Порожнє перевизначення не зберігаємо — воно нічим не
    відрізняється від «беремо базові», і зайвий ключ лише плутав би.
    """
    if not isinstance(raw, dict):
        return {}

    cleaned: dict[str, dict[str, list[str]]] = {}
    for mode, sides in raw.items():
        mode_key = str(mode).upper()
        if mode_key not in known_modes or not isinstance(sides, dict):
            continue

        per_mode = {}
        for side in SIDES:
            values = _as_list(sides.get(side))
            if values:
                per_mode[side] = values

        if per_mode:
            cleaned[mode_key] = per_mode

    return cleaned
