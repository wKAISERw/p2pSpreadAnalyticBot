# bot/keyboards/byok.py
"""
Клавіатури розділу «Свої ключі до AI».

Екран мусить відповідати на три питання і не більше: що підключено, як це
витрачається, і що зробити, якщо не працює. Усе інше — привід поставити ще
одне налаштування, якого ніхто не читає.

Ключ на екрані завжди замаскований. Не тому, що він чужий, а тому, що
показаний цілком він залишиться в історії чату назавжди.
"""
from __future__ import annotations

from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup
from aiogram.utils.keyboard import InlineKeyboardBuilder

from core.storage.llm_keys_repo import (
    MODE_ALWAYS, MODE_OFF, MODE_ONDEMAND, MODE_TITLES, PROVIDER_TITLES, PROVIDERS,
)

MODE_ORDER = (MODE_OFF, MODE_ONDEMAND, MODE_ALWAYS)


def byok_main_kb(keys: list[dict], mode: str) -> InlineKeyboardMarkup:
    b = InlineKeyboardBuilder()
    connected = {k["provider"] for k in keys}

    for provider in PROVIDERS:
        title = PROVIDER_TITLES[provider]
        if provider in connected:
            row = next(k for k in keys if k["provider"] == provider)
            # Стан ключа видно одразу: «підключено» і «працює» — різні речі,
            # і плутати їх означає дізнатись про зламаний ключ найпізніше.
            if not row["readable"]:
                mark = "⚠️"
            elif row["last_error"]:
                mark = "❗"
            else:
                mark = "✅"
            b.row(InlineKeyboardButton(
                text=f"{mark} {title}: {row['masked']}",
                callback_data=f"byok:key:{provider}",
            ))
        else:
            b.row(InlineKeyboardButton(
                text=f"➕ Підключити {title}",
                callback_data=f"byok:add:{provider}",
            ))

    if connected:
        b.row(InlineKeyboardButton(
            text=f"⚙️ Використовувати: {MODE_TITLES.get(mode, mode)}",
            callback_data="byok:mode",
        ))
    b.row(InlineKeyboardButton(text="🔙 Назад", callback_data="risk:main"))
    return b.as_markup()


def byok_mode_kb(current: str) -> InlineKeyboardMarkup:
    b = InlineKeyboardBuilder()
    for mode in MODE_ORDER:
        mark = "✅ " if mode == current else ""
        b.row(InlineKeyboardButton(
            text=f"{mark}{MODE_TITLES[mode]}", callback_data=f"byok:mode:{mode}",
        ))
    b.row(InlineKeyboardButton(text="🔙 Назад", callback_data="byok:main"))
    return b.as_markup()


def byok_key_kb(provider: str) -> InlineKeyboardMarkup:
    b = InlineKeyboardBuilder()
    b.row(InlineKeyboardButton(text="♻️ Замінити ключ", callback_data=f"byok:add:{provider}"))
    b.row(InlineKeyboardButton(text="🗑 Видалити", callback_data=f"byok:del:{provider}"))
    b.row(InlineKeyboardButton(text="🔙 Назад", callback_data="byok:main"))
    return b.as_markup()
