"""
bot/keyboards/menu.py — Головне меню.
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
        InlineKeyboardButton(text="📊 Статус", callback_data="menu:status"),
        InlineKeyboardButton(text="💰 Баланси", callback_data="menu:balance"),
    )
    builder.row(
        InlineKeyboardButton(text="📈 Статистика", callback_data="menu:stats"),
        InlineKeyboardButton(text="🩺 Сесії", callback_data="menu:sessions"),
    )
    builder.row(
        InlineKeyboardButton(text="⚙️ Налаштування", callback_data="menu:settings"),
        InlineKeyboardButton(text="🔑 API Ключі", callback_data="menu:keys"),
    )
    builder.row(
        InlineKeyboardButton(text="🔌 Біржі", callback_data="exch:list"),
        InlineKeyboardButton(text="📢 Створити оголошення", callback_data="ad:create"),
    )
    builder.row(
        InlineKeyboardButton(text="💳 Мої картки", callback_data="menu:cards"),
        InlineKeyboardButton(text="📊 Звіт по картках", callback_data="menu:report"),
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


