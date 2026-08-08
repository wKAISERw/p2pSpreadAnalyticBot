"""
bot/keyboards/filters.py — Клавіатури фільтрів, налаштувань, банків.
"""
from __future__ import annotations
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton
from aiogram.utils.keyboard import InlineKeyboardBuilder

from bot.keyboards import SCANNER_MODE_LABELS


def settings_menu_kb(scanner_mode: str = "SPREAD", is_admin: bool = False) -> InlineKeyboardMarkup:
    """Особисті налаштування юзера (контекстне меню за режимом)."""
    builder = InlineKeyboardBuilder()
    builder.row(
        InlineKeyboardButton(text="💰 Макс. капітал", callback_data="set:capital"),
        InlineKeyboardButton(text="📦 Мін. сума угоди", callback_data="set:min_amount"),
    )
    builder.row(
        InlineKeyboardButton(text="📉 Мін. спред", callback_data="set:spread"),
        InlineKeyboardButton(text="🏦 Банки", callback_data="set:banks_menu"),
    )
    builder.row(
        InlineKeyboardButton(text="📊 Фільтри мерчантів", callback_data="set:merchant_filters"),
    )
    builder.row(
        InlineKeyboardButton(text="🔍 Режим сканування", callback_data="set:scanner_mode"),
        InlineKeyboardButton(text="💲 Фільтр ціни", callback_data="set:price_range"),
    )
    # Контекстні кнопки для мейкер-режимів
    if scanner_mode == "MAKER_SELL":
        builder.row(
            InlineKeyboardButton(text="💲 Ціна купівлі (maker)", callback_data="set:maker_buy_price"),
        )
    elif scanner_mode == "MAKER_BUY":
        builder.row(
            InlineKeyboardButton(text="📊 Цільова маржа (maker)", callback_data="set:target_margin"),
        )
    builder.row(
        InlineKeyboardButton(text="🖥 Налаштування виводу", callback_data="set:display_menu"),
    )
    builder.row(
        InlineKeyboardButton(text="🔌 Управління біржами", callback_data="exch:list"),
    )
    # Адмінські глобальні налаштування
    if is_admin:
        builder.row(
            InlineKeyboardButton(text="🛠 Глобальні налаштування (адмін)", callback_data="menu:global_settings"),
        )
    builder.row(
        InlineKeyboardButton(text="🔔 Увімкнути алерти", callback_data="user:alerts:on"),
    )
    builder.row(
        InlineKeyboardButton(text="🔙 Назад", callback_data="menu:main")
    )
    return builder.as_markup()



def display_settings_kb(current: dict) -> InlineKeyboardMarkup:
    """
    Меню налаштувань виводу повідомлень (per-user).
    current: dict з ключами відображення, alert_cooldown, group_active_alerts
    """
    builder = InlineKeyboardBuilder()

    def _icon(key: str) -> str:
        return "✅" if current.get(key, True) else "❌"

    builder.row(InlineKeyboardButton(
        text=f"{_icon('show_ai_terms_summary')} Вижимка умов (AI)",
        callback_data="disp:toggle:show_ai_terms_summary",
    ))
    builder.row(InlineKeyboardButton(
        text=f"{_icon('show_full_terms')} Повні умови (спойлер)",
        callback_data="disp:toggle:show_full_terms",
    ))
    builder.row(InlineKeyboardButton(
        text=f"{_icon('show_ai_logic')} Логіка AI (спойлер)",
        callback_data="disp:toggle:show_ai_logic",
    ))
    builder.row(InlineKeyboardButton(
        text=f"{_icon('show_bank_details')} Деталі банків (спойлер)",
        callback_data="disp:toggle:show_bank_details",
    ))
    builder.row(InlineKeyboardButton(
        text=f"{_icon('show_llm_summary')} Вердикт AI в алерті",
        callback_data="disp:toggle:show_llm_summary",
    ))

    # Фільтрація ФОП/ТОВ
    fop_val = current.get("filter_fop_tov", "hide")
    fop_label = "Приховати 🚫" if fop_val == "hide" else ("Попередити ⚠️" if fop_val == "warn" else "Ігнорувати 🟢")
    builder.row(InlineKeyboardButton(
        text=f"🏢 ФОП / ТОВ: {fop_label}",
        callback_data="disp:toggle:filter_fop_tov",
    ))

    # Фільтрація Банка/Сейф
    banka_val = current.get("filter_banka_jar", "hide")
    banka_label = "Приховати 🚫" if banka_val == "hide" else ("Попередити ⚠️" if banka_val == "warn" else "Ігнорувати 🟢")
    builder.row(InlineKeyboardButton(
        text=f"🍯 Банка / Сейф: {banka_label}",
        callback_data="disp:toggle:filter_banka_jar",
    ))

    # Налаштування затримки
    cooldown_val = current.get("alert_cooldown", -1.0)
    if cooldown_val < 0.0:
        cooldown_label = "АВТО ⚡️"
    elif cooldown_val == 0.0:
        cooldown_label = "ВИМК 🚫"
    else:
        cooldown_label = f"{cooldown_val} сек"
        
    builder.row(InlineKeyboardButton(
        text=f"⏱ Затримка алертів: {cooldown_label}",
        callback_data="disp:toggle:alert_cooldown",
    ))
    
    # Кнопка переходу в меню тонкого налаштування авто-затримки
    if cooldown_val < 0.0:
        builder.row(InlineKeyboardButton(
            text="⚙️ Налаштувати авто-затримку →",
            callback_data="set:auto_cooldown_menu",
        ))

    # Налаштування групування active
    group_active = current.get("group_active_alerts", True)
    group_active_icon = "✅" if group_active else "❌"
    builder.row(InlineKeyboardButton(
        text=f"{group_active_icon} Групувати /active в 1 повідомлення",
        callback_data="disp:toggle:group_active_alerts",
    ))

    # Налаштування групування сканера
    group_scanner = current.get("group_scanner_alerts", True)
    group_scanner_icon = "✅" if group_scanner else "❌"
    builder.row(InlineKeyboardButton(
        text=f"{group_scanner_icon} Групувати алерти в 1 повідомлення",
        callback_data="disp:toggle:group_scanner_alerts",
    ))

    # Налаштування профілю CryptoBot (Chat vs Webapp)
    cb_links_val = current.get("cryptobot_profile_mode", "chat")
    cb_links_label = "Чат 💬" if cb_links_val == "chat" else "Mini App 📱"
    builder.row(InlineKeyboardButton(
        text=f"🤖 Профіль CryptoBot: {cb_links_label}",
        callback_data="disp:toggle:cryptobot_profile_mode",
    ))

    builder.row(InlineKeyboardButton(
        text="💳 Налаштування карткового модуля →",
        callback_data="set:card_display_menu",
    ))
    builder.row(InlineKeyboardButton(text="🔙 Назад", callback_data="menu:settings"))
    return builder.as_markup()


def auto_cooldown_settings_kb(config: dict) -> InlineKeyboardMarkup:
    """Генерує меню тонкого налаштування авто-затримки."""
    builder = InlineKeyboardBuilder()
    
    window = config.get("window_seconds", 5.0)
    tiers = config.get("tiers", [])
    
    builder.row(InlineKeyboardButton(
        text=f"⏱ Вікно аналізу: {window} сек",
        callback_data="auto_cd:set_window",
    ))
    
    # Відображаємо кнопки для кожного порогу (threshold) та його затримки (delay)
    # tiers: [0: t1 (0.0s), 1: t2 (0.3s), 2: t3 (0.8s), 3: t4 (1.5s)]
    t1 = tiers[0].get("threshold", 10) if len(tiers) > 0 else 10
    t2 = tiers[1].get("threshold", 15) if len(tiers) > 1 else 15
    t3 = tiers[2].get("threshold", 20) if len(tiers) > 2 else 20
    max_delay = tiers[3].get("delay", 1.5) if len(tiers) > 3 else 1.5
    
    builder.row(
        InlineKeyboardButton(text=f"📊 Поріг 1 (0.0s): < {t1} пов.", callback_data="auto_cd:set_t1"),
        InlineKeyboardButton(text=f"📊 Поріг 2 (0.3s): < {t2} пов.", callback_data="auto_cd:set_t2"),
    )
    builder.row(
        InlineKeyboardButton(text=f"📊 Поріг 3 (0.8s): < {t3} пов.", callback_data="auto_cd:set_t3"),
        InlineKeyboardButton(text=f"⏳ Макс затримка: {max_delay} сек", callback_data="auto_cd:set_max_delay"),
    )
    
    builder.row(InlineKeyboardButton(text="🔙 Назад", callback_data="set:display_menu"))
    return builder.as_markup()



def global_settings_kb(settings_dict: dict) -> InlineKeyboardMarkup:
    """
    Генерує інтерактивну клавіатуру Глобальних Налаштувань (Global Settings UI).
    Додано вкладку експериментальних функцій та навігацію.
    """
    builder = InlineKeyboardBuilder()

    # 1. Зчитуємо поточні стани з конфігу (твоя існуюча логіка)
    min_spread = settings_dict.get("min_spread_pct", 0.5)
    safety_buffer = settings_dict.get("safety_buffer_pct", 0.3)
    max_alerts = settings_dict.get("max_alerts_per_cycle", 4)
    require_sessions = settings_dict.get("require_sessions", "true") == "true"
    show_spread_logs = settings_dict.get("show_spread_logs", "true") == "true"

    # Конвертуємо стан сесій у красивий візуальний бейдж
    session_status = "🟢 Валідувати" if require_sessions else "⚪ Ігнорувати"
    spread_logs_status = "🟢 Показувати" if show_spread_logs else "⚪ Приховувати"

    # 2. Будуємо сітку кнопок (як на твоєму скріншоті UI)
    builder.row(InlineKeyboardButton(text=f"📉 Мін. Спред: {min_spread}%", callback_data="gset:edit:min_spread"))
    builder.row(
        InlineKeyboardButton(text=f"🛡️ Буфер безпеки: {safety_buffer}%", callback_data="gset:edit:safety_buffer"))
    builder.row(InlineKeyboardButton(text=f"📦 Макс. алертів/цикл: {max_alerts}", callback_data="gset:edit:max_alerts"))
    builder.row(
        InlineKeyboardButton(text=f"🩺 Стан сесій: {session_status}", callback_data="gset:toggle:require_sessions"))
    builder.row(
        InlineKeyboardButton(text=f"📋 Логи спредів: {spread_logs_status}", callback_data="gset:toggle:show_spread_logs"))

    # 🚀 ДОДАЄМО НАШУ НОВУ ВКЛАДКУ ЕКСПЕРИМЕНТАЛЬНИХ ФІЧ
    builder.row(InlineKeyboardButton(
        text="🛠️ Експериментальні функції",
        callback_data="feat:main"  # Цей callback веде на категорійне меню, яке ми написали
    ))

    # Навігаційна кнопка повернення на головну сторінку бота
    builder.row(InlineKeyboardButton(text="⬅️ Назад в Головне Меню", callback_data="menu:main"))

    return builder.as_markup()



def banks_selection_kb(all_banks: dict[str, str], selected: list[str], side: str = "general") -> InlineKeyboardMarkup:
    """
    Галочки банків — один клік вмикає/вимикає.
    all_banks: {internal_code: human_name}  напр. {"43": "Monobank", "14": "PrivatBank"}
    selected:  список internal_code що активні у юзера
    side:      "general" | "buy" | "sell"
    """
    builder = InlineKeyboardBuilder()
    selected_set = set(selected)
    for code, name in all_banks.items():
        icon = "✅" if code in selected_set else "☐"
        builder.button(text=f"{icon} {name}", callback_data=f"bank:toggle:{side}:{code}")
    builder.adjust(2)  # 2 кнопки в ряд
    builder.row(InlineKeyboardButton(text="💾 Зберегти", callback_data=f"bank:save:{side}"))
    builder.row(InlineKeyboardButton(text="🔙 Назад", callback_data="set:banks_menu"))
    return builder.as_markup()




def scanner_mode_kb(active_modes: str | list[str] = "SPREAD") -> InlineKeyboardMarkup:
    """
    Вибір режимів сканера. Кожен рядок — перемикач, режимів може бути кілька.

    Раніше вибір був одиничним: щоб ловити і купівлю, і продаж, доводилось
    перемикатися туди-сюди, і половину часу друга сторона не сканувалась
    узагалі. Приймає і рядок (сумісність зі старими викликами), і список.
    """
    if isinstance(active_modes, str):
        active = {m.strip().upper() for m in active_modes.split(",") if m.strip()}
    else:
        active = {str(m).upper() for m in active_modes}

    builder = InlineKeyboardBuilder()
    for mode, label in SCANNER_MODE_LABELS.items():
        icon = "✅ " if mode in active else "▫️ "
        builder.row(InlineKeyboardButton(
            text=f"{icon}{label}",
            callback_data=f"smode:{mode}",
        ))
    builder.row(InlineKeyboardButton(text="🔙 Назад", callback_data="menu:settings"))
    return builder.as_markup()


# ═══════════════════════════════════════════════════════════════════════════════
# Price Range — фільтр ціни для тейкера
# ═══════════════════════════════════════════════════════════════════════════════



def price_range_kb() -> InlineKeyboardMarkup:
    """Клавіатура вибору типу фільтра ціни."""
    builder = InlineKeyboardBuilder()
    builder.row(InlineKeyboardButton(text="📏 Діапазон (від — до)", callback_data="prange:range"))
    builder.row(InlineKeyboardButton(text="🎯 Точна ціна (±0.01)", callback_data="prange:exact"))
    builder.row(InlineKeyboardButton(text="⬇️ Макс. ціна (не більше)", callback_data="prange:max"))
    builder.row(InlineKeyboardButton(text="⬆️ Мін. ціна (не менше)", callback_data="prange:min"))
    builder.row(InlineKeyboardButton(text="🚫 Вимкнути фільтр", callback_data="prange:off"))
    builder.row(InlineKeyboardButton(text="🔙 Назад", callback_data="menu:settings"))
    return builder.as_markup()


# ═══════════════════════════════════════════════════════════════════════════════
# Create Ad — створення P2P оголошення
# ═══════════════════════════════════════════════════════════════════════════════


def filters_menu_kb(scanner_mode: str = "SPREAD", is_alerts_active: bool = True) -> InlineKeyboardMarkup:
    """Підменю Фільтри з кнопками налаштування капіталу, спреду, режимів та відображення."""
    builder = InlineKeyboardBuilder()
    builder.row(
        InlineKeyboardButton(text="💰 Капітал", callback_data="set:capital"),
        InlineKeyboardButton(text="📦 Мін. сума", callback_data="set:min_amount"),
    )
    builder.row(
        InlineKeyboardButton(text="📉 Мін. спред", callback_data="set:spread"),
        InlineKeyboardButton(text="🏦 Банки", callback_data="set:banks_menu"),
    )
    builder.row(
        InlineKeyboardButton(text="🏪 Фільтри мерчантів", callback_data="set:merchant_filters"),
    )
    builder.row(
        InlineKeyboardButton(text="🔍 Режим сканування", callback_data="set:scanner_mode"),
        InlineKeyboardButton(text="💲 Фільтр ціни", callback_data="set:price_range"),
    )

    # Контекстні кнопки для мейкер-режимів
    if scanner_mode == "MAKER_SELL":
        builder.row(
            InlineKeyboardButton(text="💲 Ціна купівлі (maker)", callback_data="set:maker_buy_price"),
        )
    elif scanner_mode == "MAKER_BUY":
        builder.row(
            InlineKeyboardButton(text="📊 Цільова маржа (maker)", callback_data="set:target_margin"),
        )

    builder.row(
        InlineKeyboardButton(text="🖥 Налаштування виводу", callback_data="set:display_menu"),
    )

    # Динамічний перемикач алертів
    if is_alerts_active:
        builder.row(InlineKeyboardButton(text="🔕 Вимкнути алерти", callback_data="user:alerts:off"))
    else:
        builder.row(InlineKeyboardButton(text="🔔 Увімкнути алерти", callback_data="user:alerts:on"))

    builder.row(InlineKeyboardButton(text="🔙 В головне меню", callback_data="menu:main"))
    return builder.as_markup()


