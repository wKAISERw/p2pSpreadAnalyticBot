"""
bot/keyboards/common.py — Спільні кнопки навігації (Назад).
"""
from __future__ import annotations
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton
from aiogram.utils.keyboard import InlineKeyboardBuilder

def back_to_main_kb() -> InlineKeyboardMarkup:
    """Універсальна кнопка 'Назад'."""
    builder = InlineKeyboardBuilder()
    builder.button(text="🔙 Назад в меню", callback_data="menu:main")
    return builder.as_markup()




def back_to_settings_kb() -> InlineKeyboardMarkup:
    """Кнопка повернення до списку налаштувань."""
    builder = InlineKeyboardBuilder()
    builder.button(text="🔙 До налаштувань", callback_data="menu:global_settings")
    return builder.as_markup()



def back_to_keys_kb() -> InlineKeyboardMarkup:
    """Кнопка повернення до API ключів."""
    builder = InlineKeyboardBuilder()
    builder.button(text="🔙 До ключів", callback_data="menu:keys")
    return builder.as_markup()




def back_to_stats_kb() -> InlineKeyboardMarkup:
    """Кнопка повернення до зведення статистики."""
    builder = InlineKeyboardBuilder()
    builder.button(text="🔙 До статистики", callback_data="menu:stats")
    builder.button(text="🏠 Меню", callback_data="menu:main")
    builder.adjust(2)
    return builder.as_markup()


# ═══════════════════════════════════════════════════════════════════════════════
# Exchange Management — управління доступністю бірж
# ═══════════════════════════════════════════════════════════════════════════════

EXCHANGE_ICONS = {
    "Bybit": "🟠", "OKX": "⚫", "Wallet": "💎",
    "Binance": "🟡", "MEXC": "🔵",
}




def back_to_status_kb() -> InlineKeyboardMarkup:
    """Кнопка повернення до статусу."""
    builder = InlineKeyboardBuilder()
    builder.button(text="🔙 До статусу", callback_data="menu:status")
    return builder.as_markup()


# ═══════════════════════════════════════════════════════════════════════════════
# Scanner Mode — вибір режиму сканування
# ═══════════════════════════════════════════════════════════════════════════════

SCANNER_MODE_LABELS = {
    "SPREAD":      "🔄 Спред (зв'язки buy+sell)",
    "TAKER_BUY":   "🛒 Тейкер: тільки купівля",
    "TAKER_SELL":  "💸 Тейкер: тільки продаж",
    "MAKER_BUY":   "📥 Мейкер: купівля (своє оголошення)",
    "MAKER_SELL":  "📤 Мейкер: продаж (своє оголошення)",
}


def back_to_monitoring_kb() -> InlineKeyboardMarkup:
    """Кнопка повернення до меню моніторингу."""
    builder = InlineKeyboardBuilder()
    builder.button(text="🔙 До моніторингу", callback_data="menu:monitoring")
    builder.button(text="🏠 Головне меню", callback_data="menu:main")
    builder.adjust(2)
    return builder.as_markup()


def back_to_system_kb() -> InlineKeyboardMarkup:
    """Кнопка повернення до системного меню."""
    builder = InlineKeyboardBuilder()
    builder.button(text="🔙 До системи", callback_data="menu:system")
    builder.button(text="🏠 Головне меню", callback_data="menu:main")
    builder.adjust(2)
    return builder.as_markup()


def back_to_exchanges_kb() -> InlineKeyboardMarkup:
    """Кнопка повернення до меню бірж."""
    builder = InlineKeyboardBuilder()
    builder.button(text="🔙 До бірж", callback_data="menu:exchanges")
    builder.button(text="🏠 Головне меню", callback_data="menu:main")
    builder.adjust(2)
    return builder.as_markup()


def back_to_balance_kb() -> InlineKeyboardMarkup:
    """Кнопка повернення з балансів (до бірж або моніторингу)."""
    builder = InlineKeyboardBuilder()
    builder.button(text="🔙 До бірж", callback_data="menu:exchanges")
    builder.button(text="🔙 До моніторингу", callback_data="menu:monitoring")
    builder.button(text="🏠 Головне меню", callback_data="menu:main")
    builder.adjust(2)
    return builder.as_markup()


def back_to_sessions_kb() -> InlineKeyboardMarkup:
    """Кнопка повернення з сесій (до бірж або моніторингу)."""
    builder = InlineKeyboardBuilder()
    builder.button(text="🔙 До бірж", callback_data="menu:exchanges")
    builder.button(text="🔙 До моніторингу", callback_data="menu:monitoring")
    builder.button(text="🏠 Головне меню", callback_data="menu:main")
    builder.adjust(2)
    return builder.as_markup()



