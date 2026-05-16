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


def display_settings_kb(current: dict) -> InlineKeyboardMarkup:
    """
    Меню налаштувань виводу повідомлень (per-user).
    current: dict з bool-ключами show_ai_terms_summary, show_full_terms, show_ai_logic, show_bank_details, show_llm_summary
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
    builder.row(InlineKeyboardButton(text="🔙 Назад", callback_data="menu:settings"))
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


def back_to_keys_kb() -> InlineKeyboardMarkup:
    """Кнопка повернення до API ключів."""
    builder = InlineKeyboardBuilder()
    builder.button(text="🔙 До ключів", callback_data="menu:keys")
    return builder.as_markup()


def exchange_connect_kb(supported: list[str]) -> InlineKeyboardMarkup:
    """Кнопки вибору біржі для підключення."""
    builder = InlineKeyboardBuilder()
    icons = {"Binance": "🟡", "Bybit": "🟠", "OKX": "⚫", "MEXC": "🔵", "Wallet": "💎"}
    for ex in supported:
        builder.button(text=f"{icons.get(ex, '🔌')} {ex}", callback_data=f"connect:{ex}")
    builder.adjust(2)
    builder.row(InlineKeyboardButton(text="🔙 Назад", callback_data="menu:keys"))
    return builder.as_markup()


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


def exchange_down_kb(exchange_name: str) -> InlineKeyboardMarkup:
    """Клавіатура при падінні біржі — cooldown або вимкнути."""
    builder = InlineKeyboardBuilder()
    builder.row(
        InlineKeyboardButton(
            text="⏱ Cooldown (обрати час)",
            callback_data=f"exch:cooldown_pick:{exchange_name}",
        ),
    )
    builder.row(
        InlineKeyboardButton(
            text="🔴 Вимкнути повністю",
            callback_data=f"exch:disable:{exchange_name}",
        ),
    )
    builder.row(
        InlineKeyboardButton(
            text="✅ Поки нічого не робити",
            callback_data="exch:ignore",
        ),
    )
    return builder.as_markup()


def exchange_cooldown_kb(exchange_name: str) -> InlineKeyboardMarkup:
    """Вибір тривалості cooldown."""
    builder = InlineKeyboardBuilder()
    hours = [1, 2, 3, 4, 6, 8, 12, 24, 48, 168]
    labels = {
        1: "1г", 2: "2г", 3: "3г", 4: "4г", 6: "6г",
        8: "8г", 12: "12г", 24: "1 день", 48: "2 дні", 168: "1 тиждень",
    }
    for h in hours:
        builder.button(
            text=labels.get(h, f"{h}г"),
            callback_data=f"exch:cooldown:{exchange_name}:{h}",
        )
    builder.adjust(5)  # 5 кнопок в ряд
    builder.row(
        InlineKeyboardButton(text="⌨️ Ввести вручну (годин)", callback_data=f"exch:cooldown_custom:{exchange_name}"),
    )
    builder.row(
        InlineKeyboardButton(text="🔙 Назад", callback_data=f"exch:back_to_down:{exchange_name}"),
    )
    return builder.as_markup()


def exchanges_status_kb(statuses: list[dict]) -> InlineKeyboardMarkup:
    """Клавіатура зі списком всіх бірж і їхнім станом."""
    builder = InlineKeyboardBuilder()
    for st in statuses:
        name = st["name"]
        icon = EXCHANGE_ICONS.get(name, "🔌")
        if st["enabled"]:
            label = f"🟢 {icon} {name}"
            builder.button(text=label, callback_data=f"exch:toggle_menu:{name}")
        else:
            remaining = st.get("cooldown_remaining_h", 0)
            if remaining > 0:
                label = f"⏱ {icon} {name} ({remaining:.1f}г)"
            else:
                label = f"🔴 {icon} {name}"
            builder.button(text=label, callback_data=f"exch:toggle_menu:{name}")
    builder.adjust(2)
    builder.row(InlineKeyboardButton(text="🔙 Назад", callback_data="menu:status"))
    return builder.as_markup()


def exchange_toggle_kb(exchange_name: str, is_enabled: bool, is_cooldown: bool = False) -> InlineKeyboardMarkup:
    """Меню конкретної біржі: ввімкнути/вимкнути/cooldown/health-check."""
    builder = InlineKeyboardBuilder()
    icon = EXCHANGE_ICONS.get(exchange_name, "🔌")

    if is_enabled:
        builder.row(
            InlineKeyboardButton(
                text=f"⏱ Cooldown {icon} {exchange_name}",
                callback_data=f"exch:cooldown_pick:{exchange_name}",
            ),
        )
        builder.row(
            InlineKeyboardButton(
                text=f"🔴 Вимкнути {icon} {exchange_name}",
                callback_data=f"exch:disable:{exchange_name}",
            ),
        )
    else:
        builder.row(
            InlineKeyboardButton(
                text=f"🟢 Увімкнути {icon} {exchange_name}",
                callback_data=f"exch:enable:{exchange_name}",
            ),
        )
        if is_cooldown:
            builder.row(
                InlineKeyboardButton(
                    text=f"🔍 Перевірити і ввімкнути",
                    callback_data=f"exch:healthcheck:{exchange_name}",
                ),
            )

    builder.row(InlineKeyboardButton(text="🔙 До бірж", callback_data="exch:list"))
    return builder.as_markup()


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


def scanner_mode_kb(current_mode: str = "SPREAD") -> InlineKeyboardMarkup:
    """Клавіатура вибору режиму сканера."""
    builder = InlineKeyboardBuilder()
    for mode, label in SCANNER_MODE_LABELS.items():
        icon = "✅ " if mode == current_mode else ""
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

def create_ad_exchange_kb() -> InlineKeyboardMarkup:
    """Вибір біржі для створення оголошення."""
    builder = InlineKeyboardBuilder()
    # Наразі create_maker_ad реалізовано тільки для Bybit
    exchanges = [
        ("🟠 Bybit", "ad:ex:Bybit"),
        ("🟡 Binance (скоро)", "ad:ex:_unsupported"),
        ("⚫ OKX (скоро)", "ad:ex:_unsupported"),
    ]
    for label, cb in exchanges:
        builder.row(InlineKeyboardButton(text=label, callback_data=cb))
    builder.row(InlineKeyboardButton(text="🔙 Назад", callback_data="menu:main"))
    return builder.as_markup()


def create_ad_side_kb() -> InlineKeyboardMarkup:
    """Вибір сторони оголошення."""
    builder = InlineKeyboardBuilder()
    builder.row(
        InlineKeyboardButton(text="🛒 Купівля (BUY)", callback_data="ad:side:BUY"),
        InlineKeyboardButton(text="💸 Продаж (SELL)", callback_data="ad:side:SELL"),
    )
    builder.row(InlineKeyboardButton(text="🔙 Назад", callback_data="ad:create"))
    return builder.as_markup()


def create_ad_confirm_kb() -> InlineKeyboardMarkup:
    """Підтвердження створення оголошення."""
    builder = InlineKeyboardBuilder()
    builder.row(
        InlineKeyboardButton(text="✅ Створити", callback_data="ad:confirm"),
        InlineKeyboardButton(text="❌ Скасувати", callback_data="ad:cancel"),
    )
    return builder.as_markup()

def create_ad_banks_kb(all_banks: dict[str, str], selected: list[str]) -> InlineKeyboardMarkup:
    """Вибір банків для оголошення."""
    builder = InlineKeyboardBuilder()
    selected_set = set(selected)
    for code, name in all_banks.items():
        icon = "✅" if code in selected_set else "☐"
        builder.button(text=f"{icon} {name}", callback_data=f"ad:bank:{code}")
    builder.adjust(2)
    builder.row(InlineKeyboardButton(text="✅ Далі →", callback_data="ad:banks_done"))
    builder.row(InlineKeyboardButton(text="🔙 Скасувати", callback_data="ad:cancel"))
    return builder.as_markup()

# ═══════════════════════════════════════════════════════════════════════════════
# Card Management UI
# ═══════════════════════════════════════════════════════════════════════════════

def cards_dashboard_kb(cards: list[dict], module_mode: str) -> InlineKeyboardMarkup:
    """Дашборд управління картками."""
    builder = InlineKeyboardBuilder()
    
    # Кнопки карток
    for card in cards:
        icon = "🟢" if card["status"] == "active" else ("❄️" if card["status"] == "frozen_funds" else "🔴")
        drop = " (Дроп)" if not card.get("is_own", 1) else ""
        label = f"{icon} {card['bank_name']} {card['last_four']}{drop} — {card['balance']:.0f} ₴"
        builder.row(InlineKeyboardButton(text=label, callback_data=f"card:view:{card['id']}"))
        
    builder.row(
        InlineKeyboardButton(text="➕ Додати картку", callback_data="card:add_start"),
        InlineKeyboardButton(text="⚙️ Ліміти банків", callback_data="menu:bank_limits")
    )
    
    mode_label = "УВІМКНЕНО" if module_mode == "full" else "ВИМКНЕНО"
    builder.row(InlineKeyboardButton(text=f"⚙️ Модуль: {mode_label}", callback_data="card:toggle_module"))
    
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

def card_details_kb(card_id: str, status: str, bank_name: str = "") -> InlineKeyboardMarkup:
    """Управління конкретною карткою."""
    builder = InlineKeyboardBuilder()
    
    builder.row(InlineKeyboardButton(text="🔄 Актуалізувати баланс", callback_data=f"card:update_bal:{card_id}"))
    builder.row(
        InlineKeyboardButton(text="🏷 Мітка", callback_data=f"card:edit:label:{card_id}"),
        InlineKeyboardButton(text="📝 Нотатка", callback_data=f"card:edit:note:{card_id}"),
        InlineKeyboardButton(text="👥 Категорія", callback_data=f"card:edit:category:{card_id}"),
    )
    
    if bank_name.lower() == "monobank":
        builder.row(InlineKeyboardButton(text="🔗 Підключити Mono Webhook", callback_data=f"card:mono_setup:{card_id}"))
    
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
        if isinstance(val, float):
            val = f"{val:.0f}"
        builder.row(InlineKeyboardButton(
            text=f"{label}: {val}",
            callback_data=f"limits:field:{bank}:{field}"
        ))
    builder.row(InlineKeyboardButton(text="🔙 Назад", callback_data="limits:back"))
    return builder.as_markup()
