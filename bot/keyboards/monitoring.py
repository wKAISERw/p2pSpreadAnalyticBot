"""
bot/keyboards/monitoring.py — Клавіатури статистики та моніторингу.
"""
from __future__ import annotations
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton
from aiogram.utils.keyboard import InlineKeyboardBuilder

def stats_overview_kb() -> InlineKeyboardMarkup:
    """Keyboard для загальної статистики з drill-down кнопками."""
    builder = InlineKeyboardBuilder()
    builder.row(
        InlineKeyboardButton(text="📡 Пропозиції сканера", callback_data="stats:proposals"),
        InlineKeyboardButton(text="🗺 Маршрути", callback_data="stats:routes"),
    )
    builder.row(
        InlineKeyboardButton(text="📅 По днях", callback_data="stats:daily"),
        InlineKeyboardButton(text="🏛 Біржі", callback_data="stats:exchanges"),
    )
    builder.row(
        InlineKeyboardButton(text="🏦 Банки", callback_data="stats:banks"),
        InlineKeyboardButton(text="🕐 Тепл. карта", callback_data="stats:heatmap"),
    )
    builder.row(
        InlineKeyboardButton(text="📅 Будні/Вихідні", callback_data="stats:weekly"),
        InlineKeyboardButton(text="📋 Історія угод", callback_data="stats:history"),
    )
    builder.row(InlineKeyboardButton(text="🔙 Назад", callback_data="menu:main"))
    return builder.as_markup()


def stats_source_kb() -> InlineKeyboardMarkup:
    """Перший рівень: Вибір джерела даних."""
    builder = InlineKeyboardBuilder()
    builder.row(InlineKeyboardButton(text="💼 Моя статистика (Реальні угоди)", callback_data="stats:menu:my"))
    builder.row(InlineKeyboardButton(text="📡 Аналітика ринку (Знайдено сканером)", callback_data="stats:menu:scanner"))
    builder.row(InlineKeyboardButton(text="🔙 В головне меню", callback_data="menu:main"))
    return builder.as_markup()



def stats_metrics_kb(source: str) -> InlineKeyboardMarkup:
    """Другий рівень: Вибір метрики (однаковий для обох джерел)."""
    builder = InlineKeyboardBuilder()
    builder.row(
        InlineKeyboardButton(text="📅 По днях", callback_data=f"stats:daily:{source}"),
        InlineKeyboardButton(text="🏦 Топ бірж", callback_data=f"stats:exchanges:{source}")
    )
    builder.row(
        InlineKeyboardButton(text="🔥 Теплова карта", callback_data=f"stats:heatmap:{source}"),
        InlineKeyboardButton(text="🗺 Маршрути", callback_data=f"stats:routes:{source}")
    )
    builder.row(InlineKeyboardButton(text="🔙 Назад до вибору", callback_data="stats:main:none")) # Повернення на 1-й рівень
    return builder.as_markup()

