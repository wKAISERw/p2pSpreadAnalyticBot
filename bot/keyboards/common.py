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



