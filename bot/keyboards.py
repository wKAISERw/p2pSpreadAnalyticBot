"""
bot/keyboards.py — Inline клавіатури для UI/UX бота.
"""
from __future__ import annotations

from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton
from aiogram.utils.keyboard import InlineKeyboardBuilder


def main_menu_kb(
    is_scanner_active: bool = False,
    is_muted: bool = False,
    is_admin: bool = False,
) -> InlineKeyboardMarkup:
    """
    Головне меню.
    Адмін бачить кнопку управління ядром сканера.
    Звичайний юзер — кнопку вимкнення своїх алертів.
    """
    builder = InlineKeyboardBuilder()

    # Адмін: керує ядром сканера
    if is_admin:
        if is_scanner_active:
            builder.row(InlineKeyboardButton(text="⏸ Зупинити ядро", callback_data="scanner:stop"))
        else:
            builder.row(InlineKeyboardButton(text="▶️ Запустити ядро", callback_data="scanner:start"))

    builder.row(
        InlineKeyboardButton(text="📊 Статус", callback_data="menu:dashboard"),
        InlineKeyboardButton(text="💰 Баланси", callback_data="menu:balance"),
    )
    builder.row(
        InlineKeyboardButton(text="⚙️ Налаштування", callback_data="menu:settings"),
        InlineKeyboardButton(text="🔑 API Ключі", callback_data="menu:keys"),
    )
    # Пауза алертів (тимчасова через mute або постійна через is_alerts_active)
    if is_muted:
        builder.row(InlineKeyboardButton(text="🔔 Увімкнути алерти", callback_data="mute:off"))
    else:
        builder.row(
            InlineKeyboardButton(text="🔕 Пауза 1г", callback_data="mute:1"),
            InlineKeyboardButton(text="🔕 Пауза 4г", callback_data="mute:4"),
            InlineKeyboardButton(text="🔕 Вимк. назавжди", callback_data="user:alerts:off"),
        )
    builder.row(InlineKeyboardButton(text="ℹ️ Допомога", callback_data="menu:help"))
    return builder.as_markup()

def settings_menu_kb() -> InlineKeyboardMarkup:
    """Особисті налаштування юзера."""
    builder = InlineKeyboardBuilder()
    builder.row(
        InlineKeyboardButton(text="💰 Макс. капітал", callback_data="set:capital"),
        InlineKeyboardButton(text="📦 Мін. сума угоди", callback_data="set:min_amount"),
    )
    builder.row(
        InlineKeyboardButton(text="📉 Мін. спред", callback_data="set:spread"),
        InlineKeyboardButton(text="🏦 Банки", callback_data="set:banks"),
    )
    builder.row(
        InlineKeyboardButton(text="📊 Фільтри мерчантів", callback_data="set:merchant_filters"),
    )
    builder.row(
        InlineKeyboardButton(text="🔔 Увімкнути алерти", callback_data="user:alerts:on"),
    )
    builder.row(
        InlineKeyboardButton(text="🔙 Назад", callback_data="menu:main")
    )
    return builder.as_markup()

def keys_menu_kb(has_keys: bool = False) -> InlineKeyboardMarkup:
    """Меню управління ключами."""
    builder = InlineKeyboardBuilder()
    builder.row(
        InlineKeyboardButton(text="🔌 Підключити біржу", callback_data="keys:connect")
    )
    if has_keys:
        builder.row(
            InlineKeyboardButton(text="❌ Відключити біржу", callback_data="keys:disconnect")
        )
    builder.row(
        InlineKeyboardButton(text="🔙 Назад", callback_data="menu:main")
    )
    return builder.as_markup()

def back_to_main_kb() -> InlineKeyboardMarkup:
    """Універсальна кнопка 'Назад'."""
    builder = InlineKeyboardBuilder()
    builder.button(text="🔙 Назад в меню", callback_data="menu:main")
    return builder.as_markup()


def global_settings_kb(key_labels: dict[str, str] | list[str]) -> InlineKeyboardMarkup:
    """
    Меню глобальних налаштувань.
    key_labels: {key: human_label}  або  [key, key, ...]  (legacy)
    """
    builder = InlineKeyboardBuilder()

    if isinstance(key_labels, dict):
        items = list(key_labels.items())  # [(key, label), ...]
    else:
        items = [(k, k) for k in key_labels]

    for key, label in items:
        builder.button(text=label, callback_data=f"gset:{key}")
    builder.adjust(2)
    builder.row(InlineKeyboardButton(text="🔙 Закрити", callback_data="menu:main"))
    return builder.as_markup()


def back_to_settings_kb() -> InlineKeyboardMarkup:
    """Кнопка повернення до списку налаштувань."""
    builder = InlineKeyboardBuilder()
    builder.button(text="🔙 До налаштувань", callback_data="menu:global_settings")
    return builder.as_markup()

def banks_selection_kb(all_banks: dict[str, str], selected: list[str]) -> InlineKeyboardMarkup:
    """
    Галочки банків — один клік вмикає/вимикає.
    all_banks: {internal_code: human_name}  напр. {"43": "Monobank", "14": "PrivatBank"}
    selected:  список internal_code що активні у юзера
    """
    builder = InlineKeyboardBuilder()
    selected_set = set(selected)
    for code, name in all_banks.items():
        icon = "✅" if code in selected_set else "☐"
        builder.button(text=f"{icon} {name}", callback_data=f"bank:toggle:{code}")
    builder.adjust(2)  # 2 кнопки в ряд
    builder.row(InlineKeyboardButton(text="💾 Зберегти", callback_data="bank:save"))
    builder.row(InlineKeyboardButton(text="🔙 Назад", callback_data="menu:settings"))
    return builder.as_markup()


def back_to_keys_kb() -> InlineKeyboardMarkup:
    """Кнопка повернення до API ключів."""
    builder = InlineKeyboardBuilder()
    builder.button(text="🔙 До ключів", callback_data="menu:keys")
    return builder.as_markup()


def exchange_connect_kb(supported: list[str]) -> InlineKeyboardMarkup:
    """Кнопки вибору біржі для підключення."""
    builder = InlineKeyboardBuilder()
    icons = {"Binance": "🟡", "Bybit": "🟠", "OKX": "⚫", "MEXC": "🔵"}
    for ex in supported:
        builder.button(text=f"{icons.get(ex, '🔌')} {ex}", callback_data=f"connect:{ex}")
    builder.adjust(2)
    builder.row(InlineKeyboardButton(text="🔙 Назад", callback_data="menu:keys"))
    return builder.as_markup()