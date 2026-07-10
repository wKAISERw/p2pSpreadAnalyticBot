"""
bot/keyboards/cards.py — Клавіатури карткового модуля.
"""
from __future__ import annotations
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton
from aiogram.utils.keyboard import InlineKeyboardBuilder

def card_display_settings_kb(current: dict) -> InlineKeyboardMarkup:
    """
    Категоризоване меню налаштувань відображення карткового модуля.
    """
    builder = InlineKeyboardBuilder()

    # 1. Формат виводу
    mode = current.get("card_output_mode", "inline")
    mode_text = "📥 В одному повідомленні" if mode == "inline" else "🔀 Окремою відповіддю (Reply)"
    builder.row(InlineKeyboardButton(text=f"📦 Вивід карт: {mode_text}", callback_data="disp:toggle:card_output_mode"))

    # 2. Логіка спойлера
    ss = current.get("enable_smart_spoiler", True)
    ss_text = "🧠 Смарт-спойлер (ON)" if ss else "⚪ Звичайний спойлер"
    builder.row(InlineKeyboardButton(text=f"Логіка: {ss_text}", callback_data="disp:toggle:enable_smart_spoiler"))

    # 3. Рівень деталізації
    dl = current.get("card_detail_level", "full")
    dl_text = "📝 Повний (всі ліміти)" if dl == "full" else "⚡ Компактний (суто баланс)"
    builder.row(InlineKeyboardButton(text=f"📊 Деталізація: {dl_text}", callback_data="disp:toggle:card_detail_level"))

    # 🚀 4. НОВИЙ ПУНКТ: Активація модуля для поодиноких режимів Taker/Maker
    esm = current.get("enable_in_single_modes", False) or current.get("enable_in_single_modes") == 1
    esm_text = "🔌 В окремих режимах: ✅ Увімк" if esm else "🔌 В окремих режимах: ❌ Вимк"
    builder.row(InlineKeyboardButton(text=esm_text, callback_data="disp:toggle:enable_in_single_modes"))

    # 5. Новий пункт: Відображення розбивки балансу
    sbb = current.get("show_balances_breakdown", True)
    sbb_text = "📊 Розбивка балансів: ✅ Увімк" if sbb else "📊 Розбивка балансів: ❌ Вимк"
    builder.row(InlineKeyboardButton(text=sbb_text, callback_data="disp:toggle:show_balances_breakdown"))

    # 6. Новий пункт: Поради з переказу
    stt = current.get("show_transfer_tips", True)
    stt_text = "💡 Поради щодо лімітів: ✅ Увімк" if stt else "💡 Поради щодо лімітів: ❌ Вимк"
    builder.row(InlineKeyboardButton(text=stt_text, callback_data="disp:toggle:show_transfer_tips"))

    # 7. Ліміт непрогрітих карт
    ccl = current.get("cold_card_limit", 2000.0)
    ccl_text = f"🌱 Ліміт непрогрітих: {ccl:.0f} ₴" if ccl > 0 else "🌱 Ліміт непрогрітих: Вимкнено"
    builder.row(InlineKeyboardButton(text=ccl_text, callback_data="disp:set:cold_card_limit"))

    builder.row(InlineKeyboardButton(text="⬅️ Назад", callback_data="set:display_menu"))
    return builder.as_markup()




def cards_dashboard_kb(cards: list[dict], module_mode: str) -> InlineKeyboardMarkup:
    """Дашборд управління картками."""
    builder = InlineKeyboardBuilder()
    
    # Кнопки карток
    for card in cards:
        icon = "🟢" if card["status"] == "active" else ("❄️" if card["status"] == "frozen_funds" else "🔴")
        drop = " (Дроп)" if not card.get("is_own", 1) else ""
        label = f"{icon} {card['bank_name'].capitalize()} {card['last_four']}{drop} — {card['balance']:.0f} ₴"
        builder.row(InlineKeyboardButton(text=label, callback_data=f"card:view:{card['id']}"))
        
    builder.row(
        InlineKeyboardButton(text="➕ Додати картку", callback_data="card:add_start"),
        InlineKeyboardButton(text="🏦 Ліміти банків", callback_data="menu:bank_limits")
    )
    
    mode_label = "УВІМКНЕНО" if module_mode == "full" else "ВИМКНЕНО"
    builder.row(
        InlineKeyboardButton(text=f"⚙️ Модуль: {mode_label}", callback_data="card:toggle_module"),
        InlineKeyboardButton(text="🖨 Налашт. виводу", callback_data="set:card_display_menu")
    )
    
    builder.row(
        InlineKeyboardButton(text="📊 Звіт по картках", callback_data="menu:report"),
        InlineKeyboardButton(text="🔍 Діагностика", callback_data="card:diagnostics")
    )
    
    builder.row(InlineKeyboardButton(text="🔙 В головне меню", callback_data="menu:main"))
    return builder.as_markup()



def card_banks_kb() -> InlineKeyboardMarkup:
    """Вибір банку при додаванні картки."""
    builder = InlineKeyboardBuilder()
    banks = ["monobank", "privatbank", "pumb", "izibank", "a-bank", "sense"]
    for bank in banks:
        builder.button(text=bank.capitalize(), callback_data=f"card_add:bank:{bank}")
    builder.adjust(2)
    builder.row(InlineKeyboardButton(text="🔙 Скасувати", callback_data="card:cancel"))
    return builder.as_markup()



def card_is_own_kb() -> InlineKeyboardMarkup:
    """Вибір типу картки."""
    builder = InlineKeyboardBuilder()
    builder.row(
        InlineKeyboardButton(text="🙋‍♂️ Власна", callback_data="card_add:is_own:1"),
        InlineKeyboardButton(text="🤝 Дроп (Чужа)", callback_data="card_add:is_own:0")
    )
    builder.row(InlineKeyboardButton(text="🔙 Скасувати", callback_data="card:cancel"))
    return builder.as_markup()



def card_details_kb(card_id: str, status: str, bank_name: str = "", is_warmed_up: bool = False) -> InlineKeyboardMarkup:
    """Управління конкретною карткою."""
    builder = InlineKeyboardBuilder()
    
    builder.row(InlineKeyboardButton(text="🔄 Актуалізувати баланс", callback_data=f"card:update_bal:{card_id}"))
    
    warmth_text = "🔥 Прогріта: Так" if is_warmed_up else "⚪ Прогріта: Ні"
    builder.row(InlineKeyboardButton(text=warmth_text, callback_data=f"card:toggle_warmth:{card_id}"))

    builder.row(
        InlineKeyboardButton(text="🏷 Мітка", callback_data=f"card:edit:label:{card_id}"),
        InlineKeyboardButton(text="📝 Нотатка", callback_data=f"card:edit:note:{card_id}"),
        InlineKeyboardButton(text="👥 Категорія", callback_data=f"card:edit:category:{card_id}"),
    )
    
    if bank_name.lower() == "monobank":
        builder.row(InlineKeyboardButton(text="🔗 Підключити Mono Webhook", callback_data=f"card:mono_setup:{card_id}"))
    
    builder.row(InlineKeyboardButton(text="⚙️ Індивідуальні ліміти", callback_data=f"card:limits:{card_id}"))
    
    if status in ("active", "frozen_funds"):
        toggle_text = "❄️ Заморозити" if status == "active" else "🟢 Розморозити"
        builder.row(InlineKeyboardButton(text=toggle_text, callback_data=f"card:toggle:{card_id}"))
        
    builder.row(InlineKeyboardButton(text="🗑 Видалити картку", callback_data=f"card:delete:{card_id}"))
    builder.row(InlineKeyboardButton(text="🔙 До списку", callback_data="card:dashboard"))
    return builder.as_markup()



def card_category_kb(card_id: str) -> InlineKeyboardMarkup:
    """Вибір категорії картки."""
    builder = InlineKeyboardBuilder()
    categories = [
        ("🙋 Власна", "self"),
        ("👪 Родич", "relative"),
        ("🤝 Друг", "friend"),
        ("💼 Дроп", "drop"),
    ]
    for label, value in categories:
        builder.button(text=label, callback_data=f"card:set_cat:{card_id}:{value}")
    builder.adjust(2)
    builder.row(InlineKeyboardButton(text="🔙 Назад", callback_data=f"card:view:{card_id}"))
    return builder.as_markup()



def report_period_kb() -> InlineKeyboardMarkup:
    """Вибір періоду для звіту по картках."""
    builder = InlineKeyboardBuilder()
    builder.row(
        InlineKeyboardButton(text="📅 За 24 години", callback_data="report:period:24"),
        InlineKeyboardButton(text="📅 За 7 днів", callback_data="report:period:168"),
    )
    builder.row(
        InlineKeyboardButton(text="📅 За 30 днів", callback_data="report:period:720"),
    )
    builder.row(InlineKeyboardButton(text="🔙 Скасувати", callback_data="menu:main"))
    return builder.as_markup()



def bank_limits_bank_kb() -> InlineKeyboardMarkup:
    """Вибір банку для налаштування лімітів."""
    builder = InlineKeyboardBuilder()
    banks = ["monobank", "privatbank", "pumb", "izibank", "a-bank", "sense"]
    for bank in banks:
        builder.button(text=bank.capitalize(), callback_data=f"limits:bank:{bank}")
    builder.adjust(2)
    builder.row(InlineKeyboardButton(text="🔙 Скасувати", callback_data="menu:main"))
    return builder.as_markup()

LIMIT_FIELD_LABELS = {
    "daily_out_max": "📤 Денний OUT макс",
    "daily_in_max": "📥 Денний IN макс",
    "monthly_out_max": "📤 Місячний OUT макс",
    "monthly_in_max": "📥 Місячний IN макс",
    "max_single_tx_out": "📤 Макс 1 TX OUT",
    "max_single_tx_in": "📥 Макс 1 TX IN",
    "max_tx_per_day": "🔄 Макс TX/день",
    "cooldown_hours": "⏳ Cooldown (годин)",
}



def bank_limits_fields_kb(bank: str, current: dict) -> InlineKeyboardMarkup:
    """Показує поточні ліміти банку з кнопками для редагування."""
    builder = InlineKeyboardBuilder()
    for field, label in LIMIT_FIELD_LABELS.items():
        val = current.get(field, "—")
        if val == -1 or val == -1.0:
            val_str = "♾️ Ігнорувати"
        elif isinstance(val, float):
            val_str = f"{val:.0f}"
        else:
            val_str = str(val)
        builder.row(InlineKeyboardButton(
            text=f"{label}: {val_str}",
            callback_data=f"limits:field:{bank}:{field}"
        ))
    builder.row(InlineKeyboardButton(text="🔙 Назад", callback_data="limits:back"))
    return builder.as_markup()



def card_limits_fields_kb(card_id: str, effective_limits: dict, is_custom: bool) -> InlineKeyboardMarkup:
    """Показує ефективні ліміти конкретної картки з можливістю редагування."""
    builder = InlineKeyboardBuilder()
    
    # Toggle: custom vs global
    if is_custom:
        toggle_text = "🟢 Режим: Локальні (натисніть для Глобальних)"
    else:
        toggle_text = "⚪ Режим: Глобальні (натисніть для Локальних)"
    builder.row(InlineKeyboardButton(text=toggle_text, callback_data=f"card:limits:toggle:{card_id}"))
    
    for field, label in LIMIT_FIELD_LABELS.items():
        val = effective_limits.get(field, "—")
        if val == -1 or val == -1.0:
            val_str = "♾️ Ігнорувати"
        elif isinstance(val, float):
            val_str = f"{val:.0f}"
        else:
            val_str = str(val)
        builder.row(InlineKeyboardButton(
            text=f"{label}: {val_str}",
            callback_data=f"clf:{card_id}:{field}"
        ))
    builder.row(InlineKeyboardButton(text="🔙 Назад до картки", callback_data=f"card:view:{card_id}"))
    return builder.as_markup()

