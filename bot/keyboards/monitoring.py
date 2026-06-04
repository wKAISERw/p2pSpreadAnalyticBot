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
    builder.row(InlineKeyboardButton(text="🔙 Назад", callback_data="menu:monitoring"))
    return builder.as_markup()


def stats_source_kb() -> InlineKeyboardMarkup:
    """Перший рівень: Вибір джерела даних."""
    builder = InlineKeyboardBuilder()
    builder.row(InlineKeyboardButton(text="💼 Моя статистика (Реальні угоди)", callback_data="stats:menu:my"))
    builder.row(InlineKeyboardButton(text="📡 Аналітика ринку (Знайдено сканером)", callback_data="stats:menu:scanner"))
    builder.row(InlineKeyboardButton(text="🔙 Назад", callback_data="menu:monitoring"))
    return builder.as_markup()



def stats_metrics_kb(source: str, period: int = 30, mode: str = "ALL") -> InlineKeyboardMarkup:
    """Другий рівень: Вибір метрики (однаковий для обох джерел)."""
    builder = InlineKeyboardBuilder()
    builder.row(
        InlineKeyboardButton(text="📅 По днях", callback_data=f"stats:daily:{source}:{period}:{mode}"),
        InlineKeyboardButton(text="🏦 Топ бірж", callback_data=f"stats:exchanges:{source}:{period}:{mode}")
    )
    builder.row(
        InlineKeyboardButton(text="🔥 Активність по год.", callback_data=f"stats:heatmap:{source}:{period}:{mode}"),
        InlineKeyboardButton(text="🗺 Маршрути", callback_data=f"stats:routes:{source}:{period}:{mode}")
    )
    
    # Фільтри
    period_labels = {1: "1д", 7: "7д", 14: "14д", 30: "30д"}
    period_txt = period_labels.get(period, f"{period}д")
    
    builder.row(
        InlineKeyboardButton(text=f"⏱ Період: {period_txt}", callback_data=f"stats:pick_period:{source}:{period}:{mode}"),
        InlineKeyboardButton(text=f"🎯 Режим: {mode}", callback_data=f"stats:pick_mode:{source}:{period}:{mode}")
    )
    builder.row(InlineKeyboardButton(text="🔙 Назад до вибору", callback_data="stats:main:none")) # Повернення на 1-й рівень
    return builder.as_markup()


def stats_period_kb(source: str, current_period: int, mode: str) -> InlineKeyboardMarkup:
    """Вибір періоду статистики."""
    builder = InlineKeyboardBuilder()
    periods = [1, 7, 14, 30]
    for p in periods:
        label = f"✅ {p} днів" if p == current_period else f"{p} днів"
        builder.row(InlineKeyboardButton(text=label, callback_data=f"stats:change_period:{source}:{p}:{mode}"))
    builder.row(InlineKeyboardButton(text="🔙 Назад", callback_data=f"stats:menu:{source}:{current_period}:{mode}"))
    return builder.as_markup()


def stats_mode_kb(source: str, period: int, current_mode: str) -> InlineKeyboardMarkup:
    """Вибір режиму статистики."""
    builder = InlineKeyboardBuilder()
    modes = ["ALL", "SPREAD", "TAKER_BUY", "TAKER_SELL", "MAKER_BUY", "MAKER_SELL"]
    for m in modes:
        label = f"✅ {m}" if m == current_mode else f"{m}"
        builder.row(InlineKeyboardButton(text=label, callback_data=f"stats:change_mode:{source}:{period}:{m}"))
    builder.row(InlineKeyboardButton(text="🔙 Назад", callback_data=f"stats:menu:{source}:{period}:{current_mode}"))
    return builder.as_markup()


def stats_daily_with_details_kb(source: str, period: int, mode: str, dates: list[str]) -> InlineKeyboardMarkup:
    """Показує звіт по днях + кнопки для перегляду деталей конкретного дня."""
    builder = InlineKeyboardBuilder()
    
    if dates:
        row_buttons = []
        for d in dates:
            short_label = d[5:] if len(d) >= 10 else d
            row_buttons.append(
                InlineKeyboardButton(text=f"🔍 {short_label}", callback_data=f"stats:daydetail:{source}:{period}:{mode}:{d}")
            )
        for i in range(0, len(row_buttons), 2):
            builder.row(*row_buttons[i:i+2])
            
    builder.row(InlineKeyboardButton(text="🔙 Назад", callback_data=f"stats:menu:{source}:{period}:{mode}"))
    return builder.as_markup()


def monitoring_menu_kb(is_admin: bool = False) -> InlineKeyboardMarkup:
    """Підменю Моніторинг зі статусом, статистикою, балансами та сесіями."""
    builder = InlineKeyboardBuilder()
    builder.row(
        InlineKeyboardButton(text="📈 Стан системи", callback_data="menu:status"),
        InlineKeyboardButton(text="📊 Статистика", callback_data="menu:stats"),
    )
    builder.row(
        InlineKeyboardButton(text="💰 Баланси бірж", callback_data="menu:balance"),
        InlineKeyboardButton(text="🔐 Auth-сесії", callback_data="menu:sessions"),
    )
    if is_admin:
        builder.row(
            InlineKeyboardButton(text="📝 Логи / аудит", callback_data="menu:logs"),
            InlineKeyboardButton(text="🏥 Health check", callback_data="menu:health"),
        )
    builder.row(InlineKeyboardButton(text="🔙 В головне меню", callback_data="menu:main"))
    return builder.as_markup()

