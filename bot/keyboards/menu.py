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
    Відображає 5 основних розділів та кнопку допомоги.
    """
    builder = InlineKeyboardBuilder()

    builder.row(
        InlineKeyboardButton(text="📡 Біржі", callback_data="menu:exchanges"),
        InlineKeyboardButton(text="🎛 Фільтри", callback_data="menu:filters"),
    )
    builder.row(
        InlineKeyboardButton(text="💳 Картки", callback_data="menu:cards"),
        InlineKeyboardButton(text="📊 Моніторинг", callback_data="menu:monitoring"),
    )
    builder.row(
        InlineKeyboardButton(text="⚙️ Система", callback_data="menu:system"),
    )
    builder.row(InlineKeyboardButton(text="ℹ️ Допомога", callback_data="menu:help"))
    return builder.as_markup()


def system_menu_kb(is_scanner_active: bool = False, is_muted: bool = False, is_admin: bool = True) -> InlineKeyboardMarkup:
    """Системне меню (керування ядром, паузи, антифрод, юзери, дебаг)."""
    builder = InlineKeyboardBuilder()

    # 1. Керування ядром сканера (hiding for non-admins)
    if is_admin:
        if is_scanner_active:
            builder.row(InlineKeyboardButton(text="⏸ Зупинити ядро", callback_data="scanner:stop"))
        else:
            builder.row(InlineKeyboardButton(text="▶️ Запустити ядро", callback_data="scanner:start"))

    # 2. Керування паузами алертів
    if is_muted:
        builder.row(InlineKeyboardButton(text="🔔 Увімкнути алерти", callback_data="mute:off"))
    else:
        builder.row(
            InlineKeyboardButton(text="🔕 Пауза 1г", callback_data="mute:1"),
            InlineKeyboardButton(text="🔕 Пауза 4г", callback_data="mute:4"),
        )
        builder.row(InlineKeyboardButton(text="🔕 Вимк. назавжди (глобально)", callback_data="mute:forever"))

    # 3. Налаштування та фічі
    if is_admin:
        builder.row(
            InlineKeyboardButton(text="🛡️ Антифрод", callback_data="menu:global_settings"),
            InlineKeyboardButton(text="🧪 Експерим. функції", callback_data="feat:main"),
        )
    else:
        builder.row(
            InlineKeyboardButton(text="🧪 Експерим. функції", callback_data="feat:main"),
        )

    # 4. Користувачі та Дебаг
    if is_admin:
        builder.row(
            InlineKeyboardButton(text="👥 Управління юзерами", callback_data="sys:users"),
            InlineKeyboardButton(text="🔄 Debug / перезапуск", callback_data="sys:debug"),
        )

    builder.row(InlineKeyboardButton(text="🔙 В головне меню", callback_data="menu:main"))
    return builder.as_markup()


