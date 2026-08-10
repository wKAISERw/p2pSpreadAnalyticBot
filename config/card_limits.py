# config/card_limits.py
"""
Спільна конфігурація карткового модуля: дефолтні ліміти й режими спліту.

Той самий словник `150000 / 400000 / 29999 / 15` лежав скопійованим у семи
місцях: DDL таблиці `user_bank_limits`, card_repo, card_matching_engine,
card_notifier, bot/handlers/cards і api/routers/personal. Копії розійшлись би
при першій же спробі підключити довідник банків — профіль діяв би через раз,
залежно від того, чий `.get(..., 400000.0)` спрацював останнім.

Тепер джерело одне, і воно тришарове:

    довідник банку (config/banks.BANK_PROFILES)
        ↑ перекриває
    глобальний дефолт (GLOBAL_DEFAULTS — для банків, яких у довіднику немає)
        ↑ перекриває
    налаштування користувача (user_bank_limits → limits_override_json картки)

Заповнене поле користувача завжди сильніше за довідник — інакше зламаються
вже налаштовані картки.
"""
from __future__ import annotations

from config.banks import UNLIMITED, get_bank_profile

# ── Режими спліту (етап 4 плану) ──────────────────────────────────────────
#
# Живуть тут, а не в движку: їх читає і сховище (валідація при записі), і
# движок (рішення), і клавіатури бота. Константа в движку означала б, що
# сховище імпортує движок — і шар даних починає залежати від шару логіки.
SPLIT_OFF = "off"                # одна картка, один переказ
SPLIT_INTRA_BANK = "intra_bank"  # кілька карток або переказів у межах банку
SPLIT_INTER_BANK = "inter_bank"  # ...і між банками (потребує етапу 3)

SPLIT_MODES: tuple[str, ...] = (SPLIT_OFF, SPLIT_INTRA_BANK, SPLIT_INTER_BANK)

SPLIT_MODE_LABELS: dict[str, str] = {
    SPLIT_OFF: "Вимкнено (1 картка, 1 переказ)",
    SPLIT_INTRA_BANK: "У межах одного банку",
    SPLIT_INTER_BANK: "Між банками",
}

# Режим «між банками» вмикається експериментальною фічею
# `inter_bank_matching` (/features → КАРТКИ). Доки її не увімкнено, пункт
# лишається видимим, але недоступним: мовчазний пункт, який нічого не
# змінює, гірший за замок із поясненням.
SPLIT_MODES_AVAILABLE: tuple[str, ...] = (SPLIT_OFF, SPLIT_INTRA_BANK)

# Ключі фіч, від яких залежить картковий матчинг. Тримаємо тут, щоб
# сховище й движок не тягли рядкові літерали з хендлерів бота.
FEATURE_INTER_BANK = "inter_bank_matching"
FEATURE_IGNORE_MERCHANT_BANKS = "ignore_merchant_bank_filter"


def available_split_modes(inter_bank_enabled: bool = False) -> tuple[str, ...]:
    """Режими спліту, які движок реально вміє для цього користувача."""
    if inter_bank_enabled:
        return (SPLIT_OFF, SPLIT_INTRA_BANK, SPLIT_INTER_BANK)
    return SPLIT_MODES_AVAILABLE


# Поля, які приймають set_user_bank_limit і update_card_limit_override.
LIMIT_FIELDS: tuple[str, ...] = (
    "daily_out_max", "daily_in_max",
    "monthly_out_max", "monthly_in_max",
    "max_single_tx_out", "max_single_tx_in",
    "max_tx_per_day", "cooldown_hours",
)

# Цілочисельні поля — решта суми в гривнях.
INT_LIMIT_FIELDS: frozenset[str] = frozenset({"max_tx_per_day", "cooldown_hours"})

# Дефолти для банку, якого немає в довіднику.
#
# Місячні 50 000 — це загальний орієнтир джерела: рівень, на якому ймовірність
# блоку мінімальна. Він консервативніший за будь-який рядок довідника, тож
# годиться як межа за замовчуванням для невідомого банку.
#
# Добових цифр джерело не дає взагалі, тому тут лишається колишнє значення:
# вигадувати добову межу під місячну — це видавати арифметику за дані. На
# практиці місячний ліміт зв'яже раніше, бо він менший.
#
# max_tx_per_day теж лишається колишнім: для невідомого банку даних немає, а
# 15 переказів на добу — не те, що ламало картки. Ламало те, що ці 15 діяли
# і для Izibank, де безпечно 2–3. Це лікує довідник, а не глобальний дефолт.
GLOBAL_DEFAULTS: dict[str, float | int] = {
    "daily_out_max": 150_000.0,
    "daily_in_max": 150_000.0,
    "monthly_out_max": 50_000.0,
    "monthly_in_max": 50_000.0,
    "max_single_tx_out": 29_999.0,
    "max_single_tx_in": 29_999.0,
    "max_tx_per_day": 15,
    "cooldown_hours": 24,
}

# Значення, які лежали захардкоженими до появи довідника.
#
# Потрібні рівно для одного: міграція user_bank_limits відрізняє «користувач
# свідомо поставив 400 000» від «це дефолт, якого ніхто не чіпав». Полів, які
# дорівнюють легасі-дефолту, ніхто не вводив — їх можна занулити, щоб почав
# діяти профіль банку. У новому коді на ці числа не спиратись.
LEGACY_DEFAULTS: dict[str, float | int] = {
    "daily_out_max": 150_000.0,
    "daily_in_max": 150_000.0,
    "monthly_out_max": 400_000.0,
    "monthly_in_max": 400_000.0,
    "max_single_tx_out": 29_999.0,
    "max_single_tx_in": 29_999.0,
    "max_tx_per_day": 15,
    "cooldown_hours": 24,
}


def default_limits_for_bank(bank_name: str | None) -> dict:
    """
    Дефолтні ліміти конкретного банку: глобальні, перекриті довідником.

    Порожнє поле профілю означає «даних немає» і лишає глобальне значення —
    це чесніше за цифру, виведену з сусідніх.
    """
    limits = dict(GLOBAL_DEFAULTS)
    profile = get_bank_profile(bank_name or "")

    # Місячна межа. Беремо рекомендовану, а не максимальну: максимальна — це
    # рівень, вище якого ризик блоку різко зростає, і ставити її за
    # замовчуванням означало б вести користувача рівно до тієї межі.
    monthly = profile.safe_monthly_uah or profile.max_monthly_uah
    if monthly:
        # IN і OUT рахуються окремо, хоча джерело говорить про сукупний
        # оборот картки. Тобто ця пара дефолтів м'якша за довідник. Спільний
        # лічильник обороту — свідомо окрема робота (етап 3 плану), бо він
        # змінює модель, а не константу.
        limits["monthly_out_max"] = float(monthly)
        limits["monthly_in_max"] = float(monthly)

    if profile.single_tx_limit_uah is not None:
        limits["max_single_tx_out"] = float(profile.single_tx_limit_uah)
        limits["max_single_tx_in"] = float(profile.single_tx_limit_uah)

    if profile.safe_tx_per_day is not None:
        limits["max_tx_per_day"] = int(profile.safe_tx_per_day)

    return limits


def merge_limits(bank_name: str | None, *layers: dict | None) -> dict:
    """
    Складає ефективні ліміти: дефолти банку, поверх них — шари користувача.

    Шари йдуть від слабшого до сильнішого (глобальні ліміти банку, далі
    локальний override картки). None-значення в шарі означає «не задано» і
    дефолт не затирає — саме так у БД позначено поле, якого користувач не
    чіпав.
    """
    result = default_limits_for_bank(bank_name)
    for layer in layers:
        if not layer:
            continue
        for field in LIMIT_FIELDS:
            value = layer.get(field)
            if value is not None:
                result[field] = value
    return result


def is_unlimited(value) -> bool:
    """-1 у полі ліміту означає «не обмежувати»."""
    try:
        return float(value) == UNLIMITED
    except (TypeError, ValueError):
        return False


def night_cap_for_bank(bank_name: str | None, now=None) -> float | None:
    """
    Скільки банк дозволяє переказати просто зараз, якщо в нього є нічне вікно.

    Повертає:
      None — обмежень немає (вікна немає або зараз не воно);
      0.0  — уночі перекази заборонені;
      суму — стеля на час вікна (Ощадбанк: 22:00–07:00 не більше 5 000 ₴).

    Матчинг має враховувати час у момент пошуку, а не лише баланси: інакше
    бот пропонує маршрут, який фізично не виконається за 15 хвилин таймера.
    """
    window = get_bank_profile(bank_name or "").night_window
    if not window:
        return None

    import datetime
    hour = (now or datetime.datetime.now()).hour

    if window.from_hour <= window.to_hour:
        inside = window.from_hour <= hour < window.to_hour
    else:  # вікно через північ, напр. 22:00–07:00
        inside = hour >= window.from_hour or hour < window.to_hour

    if not inside:
        return None
    return float(window.max_uah) if window.max_uah is not None else 0.0
