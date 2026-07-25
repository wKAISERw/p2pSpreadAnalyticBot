"""
bot/keyboards/exchanges.py — Клавіатури керування біржами та API.
"""
from __future__ import annotations
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton
from aiogram.utils.keyboard import InlineKeyboardBuilder

from bot.keyboards import EXCHANGE_ICONS


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
        InlineKeyboardButton(text="🔙 Назад", callback_data="menu:exchanges")
    )
    return builder.as_markup()




def exchange_connect_kb(supported: list[str]) -> InlineKeyboardMarkup:
    """Кнопки вибору біржі для підключення."""
    builder = InlineKeyboardBuilder()
    icons = {"Binance": "🟡", "Bybit": "🟠", "OKX": "⚫", "MEXC": "🔵", "Wallet": "💎", "BingX": "❇️"}
    for ex in supported:
        builder.button(text=f"{icons.get(ex, '🔌')} {ex}", callback_data=f"connect:{ex}")
    builder.adjust(2)
    builder.row(InlineKeyboardButton(text="🔙 Назад", callback_data="menu:keys"))
    return builder.as_markup()




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
    builder.row(InlineKeyboardButton(text="🔙 Назад", callback_data="menu:exchanges"))
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


def exchanges_menu_kb(statuses: list[dict]) -> InlineKeyboardMarkup:
    """Підменю Біржі з кнопками балансів, ключів, сесій та створення оголошення."""
    builder = InlineKeyboardBuilder()

    # 1. Кнопки швидкого перегляду стану / переходу до керування кожною біржею
    for st in statuses:
        name = st["name"]
        icon = EXCHANGE_ICONS.get(name, "🔌")
        if st["enabled"]:
            label = f"🟢 {icon} {name}"
        else:
            remaining = st.get("cooldown_remaining_h", 0)
            if remaining > 0:
                label = f"⏱ {icon} {name} ({remaining:.1f}г)"
            else:
                label = f"🔴 {icon} {name}"
        builder.button(text=label, callback_data=f"exch:toggle_menu:{name}")
    builder.adjust(2)

    # 2. Додаткові кнопки
    builder.row(
        InlineKeyboardButton(text="💰 Баланси", callback_data="menu:balance"),
        InlineKeyboardButton(text="🔑 API Ключі", callback_data="menu:keys"),
    )
    builder.row(
        InlineKeyboardButton(text="🔐 Сесії", callback_data="menu:sessions"),
        InlineKeyboardButton(text="📣 Створити оголошення", callback_data="ad:create"),
    )
    builder.row(InlineKeyboardButton(text="🔙 В головне меню", callback_data="menu:main"))
    return builder.as_markup()


