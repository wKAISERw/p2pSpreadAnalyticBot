# bot/handlers/filters.py
# Settings, capital/spread/banks, display, merchant filters, scanner mode, price.

from __future__ import annotations
import asyncio
import logging
from typing import TYPE_CHECKING, Optional
from contextlib import suppress
from aiogram.exceptions import TelegramBadRequest
from aiogram import Router, F
from aiogram.filters import Command
from aiogram.types import Message, CallbackQuery, InlineKeyboardMarkup, InlineKeyboardButton
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.utils.keyboard import InlineKeyboardBuilder

from bot import keyboards
from bot.keyboards.common import back_to_main_kb, back_to_settings_kb, back_to_keys_kb, SCANNER_MODE_LABELS
from bot.keyboards.filters import settings_menu_kb, display_settings_kb, global_settings_kb, banks_selection_kb, scanner_mode_kb, price_range_kb
from bot.keyboards.menu import main_menu_kb
from bot.keyboards.exchanges import keys_menu_kb, exchange_connect_kb

from bot.handlers.core import (
    _is_admin, _db, _bot, _notifier, _account_clients,
    _trade_worker, _single_leg_executor, is_muted, update_stats,
    _generate_dashboard_text,
    SETTING_DESCRIPTIONS, _KEY_LABELS, GlobalSettingStates, SettingStates, MerchantFilterStates, PriceRangeStates,
    TakerSellSettingsStates, _active_repricers, TakerBuySettingsStates, _maker_monitor, MakerSettingsStates
)
from config.runtime import runtime_config
from config.banks import DEFAULT_BANK_CODES, BANK_NAMES
from config import settings

router = Router()
logger = logging.getLogger(__name__)

@router.callback_query(F.data.in_(["menu:filters", "menu:settings"]))
async def on_filters_menu(call: CallbackQuery, state: FSMContext) -> None:
    await state.clear()
    
    scanner_mode = "SPREAD"
    is_alerts_active = True
    user_capital = str(settings.working_capital_uah)
    user_min_amount = "без обмежень"
    user_spread = "0.50"
    
    if _db:
        conn = getattr(_db, "db", None) or getattr(_db, "_db", _db)
        async with conn.execute(
            "SELECT working_capital, COALESCE(min_amount_uah, 0.0) as min_amount_uah, min_spread_pct, COALESCE(is_alerts_active, 1) as is_alerts_active, COALESCE(scanner_mode, 'SPREAD') FROM scanner_users WHERE user_id = ?",
            (call.from_user.id,)
        ) as cur:
            row = await cur.fetchone()
        if row:
            user_capital = f"{row[0]:.1f}"
            _min_amt = float(row[1] or 0.0)
            user_min_amount = f"{_min_amt:.0f} ₴" if _min_amt > 0 else "без обмежень"
            user_spread = f"{row[2]:.2f}"
            is_alerts_active = bool(row[3])
            scanner_mode = row[4] or "SPREAD"
            
    text = (
        "🎛 <b>ARBIX QUANTUM | Налаштування фільтрів</b>\n\n"
        "Тут ви можете змінити свої особисті обмеження для спредів та алертів:\n"
        f"├ Капітал: <b>{user_capital} ₴</b>\n"
        f"├ Мін. сума угоди: <b>{user_min_amount}</b>\n"
        f"├ Мін. спред: <b>{user_spread}%</b>\n"
        f"├ Режим сканування: <b>{scanner_mode}</b>\n"
        f"└ Алерти: <b>{'ВКЛЮЧЕНІ 🔔' if is_alerts_active else 'ВИМКНЕНІ 🔕'}</b>\n\n"
        "<i>Оберіть параметр для зміни:</i>"
    )
    with suppress(TelegramBadRequest):
        await call.message.edit_text(
            text,
            reply_markup=keyboards.filters_menu_kb(scanner_mode, is_alerts_active)
        )
    await call.answer()


@router.message(Command("ban"))
async def cmd_ban(message: Message) -> None:
    parts = message.text.split(maxsplit=3)
    if len(parts) < 3:
        return await message.answer(
            "❌ Формат: <code>/ban [exchange] [merchant_id] [причина]</code>\n"
            "Приклад: <code>/ban Binance s42ee507f2dc скам</code>"
        )
    exchange = parts[1].strip()
    merchant_id = parts[2].strip()
    reason = parts[3].strip() if len(parts) > 3 else "Ручний бан"

    if not _db:
        return await message.answer("❌ База даних не ініціалізована")

    await _db.add_to_blacklist(exchange, merchant_id, "Unknown", reason, "manual_cmd")
    await message.answer(
        f"⛔ <b>Заблоковано</b>\n"
        f"Біржа: <code>{exchange}</code>\n"
        f"ID: <code>{merchant_id}</code>\n"
        f"Причина: {reason}"
    )


# ── Пауза алертів ─────────────────────────────────────────────────────────

@router.callback_query(F.data.startswith("mute:"))
async def on_mute(call: CallbackQuery) -> None:
    from bot.handlers import core
    import time
    action = call.data.split(":")[1]
    if action == "off":
        core._mute_until = 0.0
        text, is_active = await _generate_dashboard_text(call.from_user.id)
        with suppress(TelegramBadRequest):
            await call.message.edit_text(text, reply_markup=main_menu_kb(is_active, False, _is_admin(call.from_user.id)))
        await call.answer("🔔 Алерти увімкнено!")
        return

    if action in ("forever", "inf"):
        core._mute_until = time.monotonic() + 365 * 24 * 3600
        text_msg = "🔕 <b>Алерти вимкнено назавжди (глобально)</b>"
        alert_msg = "🔕 Пауза назавжди"
    else:
        try:
            hours = float(action)
            core._mute_until = time.monotonic() + hours * 3600
            text_msg = f"🔕 <b>Алерти вимкнено на {hours:.0f} год</b>"
            alert_msg = f"🔕 Пауза на {hours:.0f} год"
        except ValueError:
            await call.answer("❌ Невірне значення часу", show_alert=True)
            return

    # Якщо адмін — повертаємо його в системне меню, інакше на головну
    kb = keyboards.back_to_system_kb() if _is_admin(call.from_user.id) else back_to_main_kb()

    with suppress(TelegramBadRequest):
        await call.message.edit_text(
            f"{text_msg}\n\nЩоб увімкнути — натисніть кнопку нижче або перезапустіть алерти.",
            reply_markup=kb,
        )
    await call.answer(alert_msg)


# ── Налаштування виводу (display settings menu + toggles) ──────────────────

_DISPLAY_LABELS = {
    "show_ai_terms_summary": "🔘 Вижимка умов (AI)",
    "show_full_terms": "🔘 Повні умови (спойлер)",
    "show_ai_logic": "🔘 Логіка AI (спойлер)",
    "show_bank_details": "🔘 Деталі банків (спойлер)",
    "show_llm_summary": "🔘 Вердикт AI в алерті",
}

_DISPLAY_DESCRIPTIONS = {
    "show_ai_terms_summary": "ШІ генерує коротку вижимку умов мерчанта (ключові вимоги, нюанси). Показується прямо в тілі повідомлення.",
    "show_full_terms": "Сирий текст умов мерчанта ховається під спойлер. Завжди можна розгорнути і прочитати оригінал.",
    "show_ai_logic": "Думки нейромережі (reason) ховаються під розгортання. Якщо вимкнено — лише бейдж (✅/⚡/🚫).",
    "show_bank_details": "Деталі банків, фільтрів та варіантів зв'язки показуються у спойлері.",
    "show_llm_summary": "Повний блок вердикту AI (рекомендація + причина) показується в алерті.",
}


@router.callback_query(F.data == "set:display_menu")
async def on_display_menu(call: CallbackQuery) -> None:
    """Показує меню налаштувань виводу повідомлень."""
    if not _db:
        return await call.answer("БД не підключена", show_alert=True)
    display = await _db.get_user_display_settings(call.message.chat.id)
    with suppress(TelegramBadRequest):
        await call.message.edit_text(
            "🖥 <b>Налаштування виводу повідомлень</b>\n\n"
            "Обери, що показувати в алертах.\n"
            "✅ = увімкнено, ❌ = вимкнено\n\n"
            "<i>Налаштування зберігаються індивідуально.</i>",
            reply_markup=display_settings_kb(display),
        )
    await call.answer()


@router.callback_query(F.data.startswith("disp:toggle:"))
async def cb_unified_display_toggle(call: CallbackQuery):
    """
    Універсальний обробник перемикачів відображення.
    Захищений від Unknown Option та підтримує конфігуратор поодиноких режимів.
    """
    try:
        await call.answer("⚙️ Параметр змінено")
        chat_id = call.message.chat.id
        field = call.data.split(":")[-1]

        # Реєстр полів карткового модуля
        CARD_FIELDS = {"card_output_mode", "enable_smart_spoiler", "card_detail_level", "enable_in_single_modes", "show_balances_breakdown", "show_transfer_tips"}

        if field in CARD_FIELDS:
            current_settings = await _db.get_user_card_settings(chat_id) or {}

            if field == "card_output_mode":
                current_settings["card_output_mode"] = (
                    "reply" if current_settings.get("card_output_mode", "inline") == "inline" else "inline"
                )
            elif field == "enable_smart_spoiler":
                current_settings["enable_smart_spoiler"] = not current_settings.get("enable_smart_spoiler", True)

            elif field == "card_detail_level":
                current_settings["card_detail_level"] = (
                    "compact" if current_settings.get("card_detail_level", "full") == "full" else "full"
                )
            # 🚀 Обробка булевого перемикача для Taker/Maker режимів
            elif field == "enable_in_single_modes":
                current_settings["enable_in_single_modes"] = not current_settings.get("enable_in_single_modes", False)
            elif field == "show_balances_breakdown":
                current_settings["show_balances_breakdown"] = not current_settings.get("show_balances_breakdown", True)
            elif field == "show_transfer_tips":
                current_settings["show_transfer_tips"] = not current_settings.get("show_transfer_tips", True)

            await _db.update_user_card_settings(chat_id, current_settings)

            from bot.keyboards import card_display_settings_kb
            await call.message.edit_reply_markup(reply_markup=card_display_settings_kb(current_settings))

        else:
            # Твоя класична логіка для звичайних display_settings алертів
            current_display = await _db.get_user_display_settings(chat_id) or {}
            
            if field == "alert_cooldown":
                cooldown_levels = [-1.0, 0.0, 0.2, 0.5, 1.0, 2.0]
                current_val = float(current_display.get("alert_cooldown", -1.0))
                try:
                    idx = cooldown_levels.index(current_val)
                    next_val = cooldown_levels[(idx + 1) % len(cooldown_levels)]
                except ValueError:
                    next_val = -1.0
                current_display["alert_cooldown"] = next_val
                
                # Скидаємо кеш нотифікатора при зміні затримки
                if _notifier:
                    _notifier._display_settings_cache.pop(chat_id, None)
            elif field == "group_active_alerts":
                current_display["group_active_alerts"] = not current_display.get("group_active_alerts", True)
                if _notifier:
                    _notifier._display_settings_cache.pop(chat_id, None)
            else:
                current_display[field] = not current_display.get(field, True)
                if _notifier:
                    _notifier._display_settings_cache.pop(chat_id, None)
                
            await _db.update_user_display_settings(chat_id, current_display)

            from bot.keyboards import display_settings_kb
            await call.message.edit_reply_markup(reply_markup=display_settings_kb(current_display))

    except Exception as e:
        logging.getLogger("Commands").error(f"💥 Помилка перемикача виводу: {e}", exc_info=True)


# ── Фільтри мерчантів ─────────────────────────────────────────────────────
_MF_EXCHANGES = ["Bybit", "OKX", "Binance", "MEXC", "Wallet"]
_MF_EX_ICONS = {"Bybit": "🟠", "OKX": "⚫", "Binance": "🟡", "MEXC": "🔵", "Wallet": "💎"}


async def _load_merchant_filters(user_id: int) -> tuple[dict, dict]:
    """Повертає (general_mf, exchange_mf) для юзера."""
    if not _db:
        return {}, {}
    import json
    conn = getattr(_db, "db", None) or getattr(_db, "_db", _db)
    async with conn.execute(
            "SELECT merchant_filters_json, COALESCE(exchange_merchant_filters_json, '{}') as emf FROM scanner_users WHERE user_id=?",
            (user_id,)
    ) as cur:
        row = await cur.fetchone()
    if not row:
        return {}, {}
    mf = json.loads(row[0] or "{}")
    emf = json.loads(row[1] or "{}")
    return mf, emf


@router.callback_query(F.data == "set:merchant_filters")
async def on_set_merchant_filters(call: CallbackQuery) -> None:
    """Показує поточні фільтри мерчанта і пропонує змінити."""
    mf, emf = await _load_merchant_filters(call.from_user.id)

    min_orders = mf.get("min_orders", 0)
    min_rate = mf.get("min_rate", 0.0)
    verified_filter = mf.get("verified_filter", "all")
    min_age = mf.get("min_account_age_days", 0)
    min_pos_rate = mf.get("min_positive_rate", 0.0)
    max_offline = mf.get("max_offline_mins", 0)

    orders_label = f"{min_orders:.0f}" if min_orders else "без обмежень"
    rate_label = f"{min_rate:.0f}%" if min_rate else "без обмежень"
    
    verified_labels = {"all": "Усі", "verified": "Тільки верифіковані", "unverified": "Тільки звичайні"}
    verified_label = verified_labels.get(verified_filter, verified_filter)
    
    age_label = f"{min_age:.0f} дн." if min_age else "без обмежень"
    pos_label = f"{min_pos_rate:.1f}%" if min_pos_rate else "без обмежень"
    offline_label = f"{max_offline:.0f} хв." if max_offline else "без обмежень"

    from aiogram.utils.keyboard import InlineKeyboardBuilder
    builder = InlineKeyboardBuilder()
    builder.button(text=f"📊 Мін. угод: {orders_label}", callback_data="mf:orders")
    builder.button(text=f"⭐ Мін. рейтинг: {rate_label}", callback_data="mf:rate")
    builder.button(text=f"🛡 Статус: {verified_label}", callback_data="mf:verified")
    builder.button(text=f"📅 Вік акаунту: {age_label}", callback_data="mf:age")
    builder.button(text=f"👍 % позитивних: {pos_label}", callback_data="mf:pos_rate")
    builder.button(text=f"⏳ Макс. офлайн: {offline_label}", callback_data="mf:max_offline")
    builder.adjust(1)
    
    # Per-exchange кнопки
    for ex in _MF_EXCHANGES:
        icon = _MF_EX_ICONS.get(ex, "🔌")
        ex_f = emf.get(ex, {})
        if ex_f:
            o = ex_f.get("min_orders", 0)
            r = ex_f.get("min_rate", 0.0)
            v = ex_f.get("verified_filter", "all")
            a = ex_f.get("min_account_age_days", 0)
            p_r = ex_f.get("min_positive_rate", 0.0)
            off = ex_f.get("max_offline_mins", 0)
            
            parts = []
            if o: parts.append(f"≥{o:.0f} угод")
            if r: parts.append(f"≥{r:.0f}%")
            if v != "all":
                parts.append("верифік." if v == "verified" else "звичайні")
            if a:
                parts.append(f"вік≥{a}дн.")
            if p_r:
                parts.append(f"відгуки≥{p_r}%")
            if off:
                parts.append(f"офлайн≤{off}хв")
            
            if parts:
                label = f"{icon} {ex}: {', '.join(parts)}"
            else:
                label = f"{icon} {ex}: загальні"
        else:
            label = f"{icon} {ex}: загальні"
        builder.button(text=label, callback_data=f"mf:exchange:{ex}")
    builder.adjust(1)
    builder.row(InlineKeyboardButton(text="🔙 Назад", callback_data="menu:settings"))

    # Формуємо текст
    lines = [
        "📊 <b>Фільтри мерчанта</b>\n",
        "Алерт прийде ТІЛЬКИ якщо обидва мерчанти (buy і sell) відповідають критеріям.\n",
        f"<b>🌐 Загальні:</b>",
        f"├ Мін. угод: <b>{orders_label}</b>",
        f"├ Мін. рейтинг: <b>{rate_label}</b>",
        f"├ Статус: <b>{verified_label}</b>",
        f"├ Вік акаунту: <b>{age_label}</b>",
        f"├ Позитивні відгуки: <b>{pos_label}</b>",
        f"└ Макс. офлайн: <b>{offline_label}</b>",
    ]
    if emf:
        lines.append("")
        lines.append("<b>📋 Per-exchange (мають перевагу):</b>")
        for ex in _MF_EXCHANGES:
            ef = emf.get(ex, {})
            if ef:
                o = ef.get("min_orders", 0)
                r = ef.get("min_rate", 0.0)
                v = ef.get("verified_filter", "all")
                a = ef.get("min_account_age_days", 0)
                p_r = ef.get("min_positive_rate", 0.0)
                off = ef.get("max_offline_mins", 0)
                
                parts = []
                if o: parts.append(f"угод≥{o:.0f}")
                if r: parts.append(f"рейтинг≥{r:.0f}%")
                if v != "all": parts.append(f"статус={v}")
                if a: parts.append(f"вік≥{a}дн.")
                if p_r: parts.append(f"відгуки≥{p_r}%")
                if off: parts.append(f"офлайн≤{off}хв")
                
                if parts:
                    lines.append(f"  {_MF_EX_ICONS.get(ex, '')} {ex}: {', '.join(parts)}")
    lines.append("\n<i>Per-exchange мають перевагу над загальними.</i>")

    with suppress(TelegramBadRequest):
        await call.message.edit_text("\n".join(lines), reply_markup=builder.as_markup())
    await call.answer()


@router.callback_query(F.data == "mf:orders")
async def on_mf_orders(call: CallbackQuery, state: FSMContext) -> None:
    await state.set_state(MerchantFilterStates.waiting_min_orders)
    with suppress(TelegramBadRequest):
        await call.message.edit_text(
            "📊 <b>Мінімальна кількість угод мерчанта (загальна)</b>\n\n"
            "Введи число. <code>0</code> — вимкнути фільтр.\n"
            "<i>Приклад: 100 — показувати тільки мерчантів з ≥100 угодами</i>",
            reply_markup=back_to_main_kb(),
        )
    await call.answer()


@router.message(MerchantFilterStates.waiting_min_orders)
async def on_mf_orders_input(message: Message, state: FSMContext) -> None:
    try:
        val = int(float(message.text.strip().replace(",", ".")))
        if val < 0:
            raise ValueError("Не може бути від'ємним")
        await _save_merchant_filter(message.from_user.id, "min_orders", val)
        label = f"{val}" if val > 0 else "вимкнено"
        await message.answer(f"✅ Мін. угод: <b>{label}</b>", reply_markup=back_to_main_kb())
    except ValueError as e:
        await message.answer(f"❌ {e}")
    finally:
        await state.clear()


@router.callback_query(F.data == "mf:rate")
async def on_mf_rate(call: CallbackQuery, state: FSMContext) -> None:
    await state.set_state(MerchantFilterStates.waiting_min_rate)
    with suppress(TelegramBadRequest):
        await call.message.edit_text(
            "⭐ <b>Мінімальний рейтинг мерчанта (загальний, %)</b>\n\n"
            "Введи число від 0 до 100. <code>0</code> — вимкнути.\n"
            "<i>Приклад: 95 — тільки мерчанти з рейтингом ≥95%</i>",
            reply_markup=back_to_main_kb(),
        )
    await call.answer()


@router.message(MerchantFilterStates.waiting_min_rate)
async def on_mf_rate_input(message: Message, state: FSMContext) -> None:
    try:
        val = float(message.text.strip().replace(",", "."))
        if not 0 <= val <= 100:
            raise ValueError("Має бути від 0 до 100")
        await _save_merchant_filter(message.from_user.id, "min_rate", val)
        label = f"{val:.0f}%" if val > 0 else "вимкнено"
        await message.answer(f"✅ Мін. рейтинг: <b>{label}</b>", reply_markup=back_to_main_kb())
    except ValueError as e:
        await message.answer(f"❌ {e}")
    finally:
        await state.clear()


@router.callback_query(F.data == "mf:verified")
async def on_mf_verified(call: CallbackQuery) -> None:
    mf, _ = await _load_merchant_filters(call.from_user.id)
    current = mf.get("verified_filter", "all")
    cycle = {"all": "verified", "verified": "unverified", "unverified": "all"}
    next_val = cycle.get(current, "all")
    await _save_merchant_filter(call.from_user.id, "verified_filter", next_val)
    await call.answer(f"Статус змінено на: {next_val}")
    await on_set_merchant_filters(call)


@router.callback_query(F.data == "mf:age")
async def on_mf_age(call: CallbackQuery, state: FSMContext) -> None:
    await state.set_state(MerchantFilterStates.waiting_min_account_age)
    with suppress(TelegramBadRequest):
        await call.message.edit_text(
            "📅 <b>Мінімальний вік акаунту мерчанта (днів)</b>\n\n"
            "Введи число днів (наприклад, <code>30</code>). <code>0</code> — вимкнути фільтр.\n"
            "<i>Працює переважно на Binance.</i>",
            reply_markup=back_to_main_kb(),
        )
    await call.answer()


@router.message(MerchantFilterStates.waiting_min_account_age)
async def on_mf_age_input(message: Message, state: FSMContext) -> None:
    try:
        val = int(float(message.text.strip().replace(",", ".")))
        if val < 0:
            raise ValueError("Не може бути від'ємним")
        await _save_merchant_filter(message.from_user.id, "min_account_age_days", val)
        label = f"{val} дн." if val > 0 else "вимкнено"
        await message.answer(f"✅ Мін. вік акаунту: <b>{label}</b>", reply_markup=back_to_main_kb())
    except ValueError as e:
        await message.answer(f"❌ {e}")
    finally:
        await state.clear()


@router.callback_query(F.data == "mf:pos_rate")
async def on_mf_pos_rate(call: CallbackQuery, state: FSMContext) -> None:
    await state.set_state(MerchantFilterStates.waiting_min_positive_rate)
    with suppress(TelegramBadRequest):
        await call.message.edit_text(
            "👍 <b>Мінімальний % позитивних відгуків мерчанта (%)</b>\n\n"
            "Введи число від 0 до 100. <code>0</code> — вимкнути.\n"
            "<i>Приклад: 98 — тільки мерчанти з ≥98% позитивних відгуків</i>",
            reply_markup=back_to_main_kb(),
        )
    await call.answer()


@router.message(MerchantFilterStates.waiting_min_positive_rate)
async def on_mf_pos_rate_input(message: Message, state: FSMContext) -> None:
    try:
        val = float(message.text.strip().replace(",", "."))
        if not 0 <= val <= 100:
            raise ValueError("Має бути від 0 до 100")
        await _save_merchant_filter(message.from_user.id, "min_positive_rate", val)
        label = f"{val:.1f}%" if val > 0 else "вимкнено"
        await message.answer(f"✅ Мін. % позитивних відгуків: <b>{label}</b>", reply_markup=back_to_main_kb())
    except ValueError as e:
        await message.answer(f"❌ {e}")
    finally:
        await state.clear()


@router.callback_query(F.data == "mf:max_offline")
async def on_mf_max_offline(call: CallbackQuery, state: FSMContext) -> None:
    await state.set_state(MerchantFilterStates.waiting_max_offline)
    with suppress(TelegramBadRequest):
        await call.message.edit_text(
            "⏳ <b>Максимальний час офлайн мерчанта (загальний, хвилин)</b>\n\n"
            "Введи число в хвилинах. <code>0</code> — вимкнути фільтр.\n"
            "<i>Приклад: 5 — не показувати мерчантів, які не в мережі більше 5 хвилин.</i>",
            reply_markup=back_to_main_kb(),
        )
    await call.answer()


@router.message(MerchantFilterStates.waiting_max_offline)
async def on_mf_max_offline_input(message: Message, state: FSMContext) -> None:
    try:
        val = int(float(message.text.strip().replace(",", ".")))
        if val < 0:
            raise ValueError("Не може бути від'ємним")
        await _save_merchant_filter(message.from_user.id, "max_offline_mins", val)
        label = f"{val} хв." if val > 0 else "вимкнено"
        await message.answer(f"✅ Макс. офлайн: <b>{label}</b>", reply_markup=back_to_main_kb())
    except ValueError as e:
        await message.answer(f"❌ {e}")
    finally:
        await state.clear()


# ── Per-exchange фільтри мерчанта ──────────────────────────────────────────

@router.callback_query(F.data.startswith("mf:exchange:"))
async def on_mf_exchange(call: CallbackQuery) -> None:
    """Показує фільтри для конкретної біржі."""
    ex_name = call.data.split(":", 2)[2]
    _, emf = await _load_merchant_filters(call.from_user.id)
    ef = emf.get(ex_name, {})

    o = ef.get("min_orders", 0)
    r = ef.get("min_rate", 0.0)
    v = ef.get("verified_filter", "all")
    a = ef.get("min_account_age_days", 0)
    p_r = ef.get("min_positive_rate", 0.0)
    off = ef.get("max_offline_mins", 0)

    o_label = f"{o:.0f}" if o else "загальний"
    r_label = f"{r:.0f}%" if r else "загальний"
    
    verified_labels = {"all": "загальний", "verified": "верифіковані", "unverified": "звичайні"}
    v_label = verified_labels.get(v, v)
    a_label = f"{a:.0f} дн." if a else "загальний"
    p_r_label = f"{p_r:.1f}%" if p_r else "загальний"
    off_label = f"{off:.0f} хв." if off else "загальний"

    icon = _MF_EX_ICONS.get(ex_name, "🔌")

    from aiogram.utils.keyboard import InlineKeyboardBuilder
    builder = InlineKeyboardBuilder()
    builder.button(text=f"📊 Мін. угод: {o_label}", callback_data=f"mf:ex_orders:{ex_name}")
    builder.button(text=f"⭐ Мін. рейтинг: {r_label}", callback_data=f"mf:ex_rate:{ex_name}")
    builder.button(text=f"🛡 Статус: {v_label}", callback_data=f"mf:ex_verified:{ex_name}")
    builder.button(text=f"📅 Вік акаунту: {a_label}", callback_data=f"mf:ex_age:{ex_name}")
    builder.button(text=f"👍 % позитивних: {p_r_label}", callback_data=f"mf:ex_pos_rate:{ex_name}")
    builder.button(text=f"⏳ Макс. офлайн: {off_label}", callback_data=f"mf:ex_max_offline:{ex_name}")
    builder.adjust(1)
    if ef:
        builder.row(InlineKeyboardButton(text="🗑 Скинути (використ. загальні)", callback_data=f"mf:ex_reset:{ex_name}"))
    builder.row(InlineKeyboardButton(text="🔙 До фільтрів", callback_data="set:merchant_filters"))

    with suppress(TelegramBadRequest):
        await call.message.edit_text(
            f"{icon} <b>Фільтри для {ex_name}</b>\n\n"
            f"├ Мін. угод: <b>{o_label}</b>\n"
            f"├ Мін. рейтинг: <b>{r_label}</b>\n"
            f"├ Статус: <b>{v_label}</b>\n"
            f"├ Вік акаунту: <b>{a_label}</b>\n"
            f"├ Позитивні відгуки: <b>{p_r_label}</b>\n"
            f"└ Макс. офлайн: <b>{off_label}</b>\n\n"
            "<i>0 / загальний = використовувати загальний фільтр</i>",
            reply_markup=builder.as_markup(),
        )
    await call.answer()


@router.callback_query(F.data.startswith("mf:ex_orders:"))
async def on_mf_ex_orders(call: CallbackQuery, state: FSMContext) -> None:
    ex_name = call.data.split(":", 2)[2]
    await state.set_state(MerchantFilterStates.waiting_ex_min_orders)
    await state.update_data(mf_exchange=ex_name)
    with suppress(TelegramBadRequest):
        await call.message.edit_text(
            f"📊 <b>Мін. угод для {ex_name}</b>\n\n"
            "Введи число. <code>0</code> — використати загальний.\n"
            "<i>Приклад: 50</i>",
            reply_markup=back_to_main_kb(),
        )
    await call.answer()


@router.message(MerchantFilterStates.waiting_ex_min_orders)
async def on_mf_ex_orders_input(message: Message, state: FSMContext) -> None:
    try:
        val = int(float(message.text.strip().replace(",", ".")))
        if val < 0:
            raise ValueError("Не може бути від'ємним")
        data = await state.get_data()
        ex_name = data.get("mf_exchange", "")
        await _save_exchange_merchant_filter(message.from_user.id, ex_name, "min_orders", val)
        label = f"{val}" if val > 0 else "загальний"
        await message.answer(f"✅ {ex_name} мін. угод: <b>{label}</b>", reply_markup=back_to_main_kb())
    except ValueError as e:
        await message.answer(f"❌ {e}")
    finally:
        await state.clear()


@router.callback_query(F.data.startswith("mf:ex_rate:"))
async def on_mf_ex_rate(call: CallbackQuery, state: FSMContext) -> None:
    ex_name = call.data.split(":", 2)[2]
    await state.set_state(MerchantFilterStates.waiting_ex_min_rate)
    await state.update_data(mf_exchange=ex_name)
    with suppress(TelegramBadRequest):
        await call.message.edit_text(
            f"⭐ <b>Мін. рейтинг для {ex_name} (%)</b>\n\n"
            "Введи число від 0 до 100. <code>0</code> — використати загальний.\n"
            "<i>Приклад: 95</i>",
            reply_markup=back_to_main_kb(),
        )
    await call.answer()


@router.message(MerchantFilterStates.waiting_ex_min_rate)
async def on_mf_ex_rate_input(message: Message, state: FSMContext) -> None:
    try:
        val = float(message.text.strip().replace(",", "."))
        if not 0 <= val <= 100:
            raise ValueError("Має бути від 0 до 100")
        data = await state.get_data()
        ex_name = data.get("mf_exchange", "")
        await _save_exchange_merchant_filter(message.from_user.id, ex_name, "min_rate", val)
        label = f"{val:.0f}%" if val > 0 else "загальний"
        await message.answer(f"✅ {ex_name} мін. рейтинг: <b>{label}</b>", reply_markup=back_to_main_kb())
    except ValueError as e:
        await message.answer(f"❌ {e}")
    finally:
        await state.clear()


@router.callback_query(F.data.startswith("mf:ex_verified:"))
async def on_mf_ex_verified(call: CallbackQuery) -> None:
    ex_name = call.data.split(":", 2)[2]
    _, emf = await _load_merchant_filters(call.from_user.id)
    ef = emf.get(ex_name, {})
    current = ef.get("verified_filter", "all")
    cycle = {"all": "verified", "verified": "unverified", "unverified": "all"}
    next_val = cycle.get(current, "all")
    await _save_exchange_merchant_filter(call.from_user.id, ex_name, "verified_filter", next_val)
    await call.answer(f"{ex_name} статус змінено на: {next_val}")
    # Refresh menu
    call.data = f"mf:exchange:{ex_name}"
    await on_mf_exchange(call)


@router.callback_query(F.data.startswith("mf:ex_age:"))
async def on_mf_ex_age(call: CallbackQuery, state: FSMContext) -> None:
    ex_name = call.data.split(":", 2)[2]
    await state.set_state(MerchantFilterStates.waiting_ex_min_account_age)
    await state.update_data(mf_exchange=ex_name)
    with suppress(TelegramBadRequest):
        await call.message.edit_text(
            f"📅 <b>Мін. вік акаунту для {ex_name} (днів)</b>\n\n"
            "Введи число. <code>0</code> — використати загальний.\n"
            "<i>Приклад: 30</i>",
            reply_markup=back_to_main_kb(),
        )
    await call.answer()


@router.message(MerchantFilterStates.waiting_ex_min_account_age)
async def on_mf_ex_age_input(message: Message, state: FSMContext) -> None:
    try:
        val = int(float(message.text.strip().replace(",", ".")))
        if val < 0:
            raise ValueError("Не може бути від'ємним")
        data = await state.get_data()
        ex_name = data.get("mf_exchange", "")
        await _save_exchange_merchant_filter(message.from_user.id, ex_name, "min_account_age_days", val)
        label = f"{val} дн." if val > 0 else "загальний"
        await message.answer(f"✅ {ex_name} мін. вік акаунту: <b>{label}</b>", reply_markup=back_to_main_kb())
    except ValueError as e:
        await message.answer(f"❌ {e}")
    finally:
        await state.clear()


@router.callback_query(F.data.startswith("mf:ex_pos_rate:"))
async def on_mf_ex_pos_rate(call: CallbackQuery, state: FSMContext) -> None:
    ex_name = call.data.split(":", 2)[2]
    await state.set_state(MerchantFilterStates.waiting_ex_min_positive_rate)
    await state.update_data(mf_exchange=ex_name)
    with suppress(TelegramBadRequest):
        await call.message.edit_text(
            f"👍 <b>Мін. % позитивних відгуків для {ex_name} (%)</b>\n\n"
            "Введи число від 0 до 100. <code>0</code> — використати загальний.\n"
            "<i>Приклад: 98</i>",
            reply_markup=back_to_main_kb(),
        )
    await call.answer()


@router.message(MerchantFilterStates.waiting_ex_min_positive_rate)
async def on_mf_ex_pos_rate_input(message: Message, state: FSMContext) -> None:
    try:
        val = float(message.text.strip().replace(",", "."))
        if not 0 <= val <= 100:
            raise ValueError("Має бути від 0 до 100")
        data = await state.get_data()
        ex_name = data.get("mf_exchange", "")
        await _save_exchange_merchant_filter(message.from_user.id, ex_name, "min_positive_rate", val)
        label = f"{val:.1f}%" if val > 0 else "загальний"
        await message.answer(f"✅ {ex_name} мін. % позитивних відгуків: <b>{label}</b>", reply_markup=back_to_main_kb())
    except ValueError as e:
        await message.answer(f"❌ {e}")
    finally:
        await state.clear()


@router.callback_query(F.data.startswith("mf:ex_max_offline:"))
async def on_mf_ex_max_offline(call: CallbackQuery, state: FSMContext) -> None:
    ex_name = call.data.split(":", 2)[2]
    await state.set_state(MerchantFilterStates.waiting_ex_max_offline)
    await state.update_data(mf_exchange=ex_name)
    with suppress(TelegramBadRequest):
        await call.message.edit_text(
            f"⏳ <b>Макс. час офлайн для {ex_name} (хвилин)</b>\n\n"
            "Введи число. <code>0</code> — використати загальний.\n"
            "<i>Приклад: 3</i>",
            reply_markup=back_to_main_kb(),
        )
    await call.answer()


@router.message(MerchantFilterStates.waiting_ex_max_offline)
async def on_mf_ex_max_offline_input(message: Message, state: FSMContext) -> None:
    try:
        val = int(float(message.text.strip().replace(",", ".")))
        if val < 0:
            raise ValueError("Не може бути від'ємним")
        data = await state.get_data()
        ex_name = data.get("mf_exchange", "")
        await _save_exchange_merchant_filter(message.from_user.id, ex_name, "max_offline_mins", val)
        label = f"{val} хв." if val > 0 else "загальний"
        await message.answer(f"✅ {ex_name} макс. офлайн: <b>{label}</b>", reply_markup=back_to_main_kb())
    except ValueError as e:
        await message.answer(f"❌ {e}")
    finally:
        await state.clear()


@router.callback_query(F.data.startswith("mf:ex_reset:"))
async def on_mf_ex_reset(call: CallbackQuery) -> None:
    """Скидає per-exchange фільтри — буде використовувати загальні."""
    ex_name = call.data.split(":", 2)[2]
    if not _db:
        return await call.answer("❌ БД не підключена", show_alert=True)
    import json
    conn = getattr(_db, "db", None) or getattr(_db, "_db", _db)
    async with conn.execute(
            "SELECT exchange_merchant_filters_json FROM scanner_users WHERE user_id=?", (call.from_user.id,)
    ) as cur:
        row = await cur.fetchone()
    emf = json.loads((row[0] if row else None) or "{}")
    emf.pop(ex_name, None)
    await conn.execute(
        "UPDATE scanner_users SET exchange_merchant_filters_json = ? WHERE user_id = ?",
        (json.dumps(emf), call.from_user.id),
    )
    await conn.commit()
    await call.answer(f"✅ {ex_name} — скинуто на загальні", show_alert=True)
    # Повертаємось до фільтрів
    return await on_set_merchant_filters(call)


async def _save_merchant_filter(user_id: int, key: str, value) -> None:
    """Зберігає один ключ в merchant_filters_json без перезапису інших."""
    if not _db:
        return
    import json
    conn = getattr(_db, "db", None) or getattr(_db, "_db", _db)
    async with conn.execute(
            "SELECT merchant_filters_json FROM scanner_users WHERE user_id=?", (user_id,)
    ) as cur:
        row = await cur.fetchone()
    mf = json.loads((row[0] if row else None) or "{}")
    if value == 0 or value == 0.0 or value == "all":
        mf.pop(key, None)  # 0 / all = вимкнути фільтр
    else:
        mf[key] = value
    await conn.execute(
        "UPDATE scanner_users SET merchant_filters_json = ? WHERE user_id = ?",
        (json.dumps(mf), user_id),
    )
    await conn.commit()


async def _save_exchange_merchant_filter(user_id: int, exchange: str, key: str, value) -> None:
    """Зберігає per-exchange фільтр в exchange_merchant_filters_json."""
    if not _db:
        return
    import json
    conn = getattr(_db, "db", None) or getattr(_db, "_db", _db)
    async with conn.execute(
            "SELECT exchange_merchant_filters_json FROM scanner_users WHERE user_id=?", (user_id,)
    ) as cur:
        row = await cur.fetchone()
    emf = json.loads((row[0] if row else None) or "{}")
    if exchange not in emf:
        emf[exchange] = {}
    if value == 0 or value == 0.0 or value == "all":
        emf[exchange].pop(key, None)
        if not emf[exchange]:
            emf.pop(exchange, None)
    else:
        emf[exchange][key] = value
    await conn.execute(
        "UPDATE scanner_users SET exchange_merchant_filters_json = ? WHERE user_id = ?",
        (json.dumps(emf), user_id),
    )
    await conn.commit()


# =========================================================================
# 🎯 РЕЖИМ СКАНУВАННЯ (SPREAD / TAKER_BUY / TAKER_SELL)
# =========================================================================

@router.callback_query(F.data == "set:scanner_mode")
async def on_scanner_mode_menu(call: CallbackQuery) -> None:
    """Показує меню вибору режиму сканера."""
    current_mode = "SPREAD"
    if _db:
        users = await _db.get_active_users()
        for u in users:
            if u["user_id"] == call.from_user.id:
                current_mode = u.get("scanner_mode", "SPREAD")
                break
    from bot.keyboards import scanner_mode_kb
    with suppress(TelegramBadRequest):
        await call.message.edit_text(
            "🎯 <b>Режим сканування</b>\n\n"
            "• <b>SPREAD</b> — класичний, шукає зв'язки Купівля→Продаж з маржею\n"
            "• <b>TAKER BUY</b> — шукає найвигідніші sell-ордери для швидкої покупки\n"
            "• <b>TAKER SELL</b> — шукає найвигідніші buy-ордери для швидкого продажу\n"
            "• <b>MAKER BUY</b> — аналіз ринку + підказка оптимальної ціни купівлі\n"
            "• <b>MAKER SELL</b> — розрахунок мін. ціни продажу за ціною купівлі\n\n"
            f"Поточний: <b>{current_mode}</b>",
            reply_markup=scanner_mode_kb(current_mode),
        )
    await call.answer()


# ═══════════════════════════════════════════════════════════════════════════════
# 🔧 TAKER FSM — Helper Functions
# ═══════════════════════════════════════════════════════════════════════════════

async def _set_scanner_mode_db(user_id: int, mode: str) -> None:
    """Атомарно записує режим і вмикає сканер. Викликати ТІЛЬКИ після підтвердження."""
    if not _db:
        return
    conn = getattr(_db, "db", None) or getattr(_db, "_db", _db)
    await conn.execute(
        "UPDATE scanner_users SET scanner_mode = ?, is_alerts_active = 1 WHERE user_id = ?",
        (mode, user_id),
    )
    await conn.commit()
    logger.info("✅ Mode set → user=%s mode=%s", user_id, mode)


async def _save_taker_sell_db(user_id: int, d: dict, roi: dict) -> None:
    if not _db:
        return
    conn = getattr(_db, "_db", None) or getattr(_db, "db", _db)
    strategy = d.get("price_strategy", "roi")
    # min_sell_price — або ROI-розрахунок, або ручний ввід
    if strategy == "roi":
        min_price = roi.get("min_sell_price", 0.0)
    elif strategy == "range":
        min_price = d.get("price_from", 0.0)
    else:
        min_price = d.get("price_input", 0.0)
    price_to = d.get("price_to", 0.0)  # для range

    await conn.execute(
        """UPDATE scanner_users
           SET scanner_mode              = 'TAKER_SELL',
               is_alerts_active          = 1,
               taker_sell_amount         = ?,
               taker_sell_price          = ?,
               taker_sell_exchange       = ?,
               taker_sell_profit         = ?,
               taker_sell_min_price      = ?,
               taker_sell_price_strategy = ?,
               taker_sell_price_to       = ?,
               taker_sell_speed          = ?
           WHERE user_id = ?""",
        (
            d["amount"],
            d.get("buy_price", 0.0),
            d.get("exchange", ""),
            d.get("profit", 0.0) / 100.0,
            min_price,
            strategy,
            price_to,
            d.get("speed", "ANY"),
            user_id,
        ),
    )
    await conn.commit()


async def _save_taker_buy_db(user_id: int, d: dict) -> None:
    if not _db:
        return
    conn = getattr(_db, "_db", None) or getattr(_db, "db", _db)
    banks_csv = ",".join(str(c) for c in d.get("banks", []))
    await conn.execute(
        """UPDATE scanner_users
           SET scanner_mode             = 'TAKER_BUY',
               is_alerts_active         = 1,
               taker_buy_amount         = ?,
               taker_buy_price_strategy = ?,
               taker_buy_price_from     = ?,
               taker_buy_max_price      = ?,
               taker_buy_limit_min      = ?,
               taker_buy_limit_max      = ?,
               taker_buy_speed          = ?,
               buy_bank_codes           = ?


           WHERE user_id = ?""",
        (
            d["amount"],
            d.get("price_strategy", "any"),
            d.get("price_from", 0.0),
            d.get("price_to", 0.0),  # max_price або exact/range-max
            d.get("limit_min", 0.0),
            d.get("limit_max", 0.0),
            d.get("speed", "ANY"),
            banks_csv,
            user_id,
        ),
    )
    await conn.commit()


async def _update_taker_sell_param_db(user_id: int, key: str, value) -> None:
    if not _db:
        return
    conn = getattr(_db, "_db", None) or getattr(_db, "db", _db)
    
    # Load current values
    async with conn.execute(
        """SELECT taker_sell_amount, taker_sell_price, taker_sell_exchange,
                  taker_sell_profit, taker_sell_min_price, taker_sell_price_strategy,
                  taker_sell_price_to, taker_sell_speed
           FROM scanner_users WHERE user_id = ?""",
        (user_id,)
    ) as cur:
        row = await cur.fetchone()
        
    if not row:
        return
        
    d = {
        "amount": float(row[0] or 0.0),
        "buy_price": float(row[1] or 0.0),
        "exchange": row[2] or "",
        "profit": float(row[3] or 0.0) * 100.0,
        "min_sell_price": float(row[4] or 0.0),
        "price_strategy": row[5] or "roi",
        "price_to": float(row[6] or 0.0),
        "speed": row[7] or "ANY",
    }
    
    # Update the modified key
    if key == "amount":
        d["amount"] = float(value)
    elif key == "buy_price":
        d["buy_price"] = float(value)
    elif key == "exchange":
        d["exchange"] = str(value)
    elif key == "speed":
        d["speed"] = str(value)
    elif key == "profit":
        d["profit"] = float(value)
    elif key == "price_strategy":
        d["price_strategy"] = str(value)
        d["min_sell_price"] = 0.0
        d["price_to"] = 0.0
    elif key == "price_input":
        d["min_sell_price"] = float(value)
    elif key == "price_range":
        d["min_sell_price"] = float(value[0])
        d["price_to"] = float(value[1])
        
    # Recalculate ROI / min_sell_price if using roi strategy
    if d["price_strategy"] == "roi" and d["amount"] > 0 and d["buy_price"] > 0:
        network_fee, _ = _get_network_fee(d["exchange"])
        roi = _calc_roi(d["amount"], d["buy_price"], d["profit"], network_fee)
        d["min_sell_price"] = roi["min_sell_price"]
        
    # Save back to DB
    await conn.execute(
        """UPDATE scanner_users
           SET taker_sell_amount         = ?,
               taker_sell_price          = ?,
               taker_sell_exchange       = ?,
               taker_sell_profit         = ?,
               taker_sell_min_price      = ?,
               taker_sell_price_strategy = ?,
               taker_sell_price_to       = ?,
               taker_sell_speed          = ?
           WHERE user_id = ?""",
        (
            d["amount"],
            d["buy_price"],
            d["exchange"],
            d["profit"] / 100.0,
            d["min_sell_price"],
            d["price_strategy"],
            d["price_to"],
            d["speed"],
            user_id
        )
    )
    await conn.commit()


async def _update_taker_buy_param_db(user_id: int, key: str, value) -> None:
    if not _db:
        return
    conn = getattr(_db, "_db", None) or getattr(_db, "db", _db)
    
    # Load current values
    async with conn.execute(
        """SELECT taker_buy_amount, taker_buy_price_strategy, taker_buy_price_from,
                  taker_buy_max_price, taker_buy_limit_min, taker_buy_limit_max,
                  taker_buy_speed, buy_bank_codes
           FROM scanner_users WHERE user_id = ?""",
        (user_id,)
    ) as cur:
        row = await cur.fetchone()
        
    if not row:
        return
        
    d = {
        "amount": float(row[0] or 0.0),
        "price_strategy": row[1] or "any",
        "price_from": float(row[2] or 0.0),
        "max_price": float(row[3] or 0.0),
        "limit_min": float(row[4] or 0.0),
        "limit_max": float(row[5] or 0.0),
        "speed": row[6] or "ANY",
        "banks": (row[7] or "").split(",") if row[7] else [],
    }
    
    # Update the modified key
    if key == "amount":
        d["amount"] = float(value)
    elif key == "price_strategy":
        d["price_strategy"] = str(value)
        if d["price_strategy"] == "any":
            d["price_from"] = 0.0
            d["max_price"] = 0.0
    elif key == "price_from":
        d["price_from"] = float(value)
    elif key == "price_to":
        d["max_price"] = float(value)
    elif key == "limits":
        d["limit_min"] = float(value[0])
        d["limit_max"] = float(value[1])
    elif key == "banks":
        d["banks"] = list(value)
    elif key == "speed":
        d["speed"] = str(value)
        
    banks_csv = ",".join(str(c) for c in d["banks"] if c)
    
    # Save back to DB
    await conn.execute(
        """UPDATE scanner_users
           SET taker_buy_amount         = ?,
               taker_buy_price_strategy = ?,
               taker_buy_price_from     = ?,
               taker_buy_max_price      = ?,
               taker_buy_limit_min      = ?,
               taker_buy_limit_max      = ?,
               taker_buy_speed          = ?,
               buy_bank_codes           = ?
           WHERE user_id = ?""",
        (
            d["amount"],
            d["price_strategy"],
            d["price_from"],
            d["max_price"],
            d["limit_min"],
            d["limit_max"],
            d["speed"],
            banks_csv,
            user_id
        )
    )
    await conn.commit()


def _tbuy_price_strategy_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🌐 Будь-яка ціна", callback_data="tbuy_ps:any")],
        [InlineKeyboardButton(text="⬇️ Не дорожче ніж...", callback_data="tbuy_ps:max")],
        [InlineKeyboardButton(text="↔️ Ціновий діапазон", callback_data="tbuy_ps:range")],
        [InlineKeyboardButton(text="🎯 Точно по ціні (Снайпер)", callback_data="tbuy_ps:exact")],
    ])


def _tsell_price_strategy_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🧮 ROI Калькулятор", callback_data="tsell_ps:roi")],
        [InlineKeyboardButton(text="⬆️ Не дешевше ніж...", callback_data="tsell_ps:min")],
        [InlineKeyboardButton(text="↔️ Ціновий діапазон", callback_data="tsell_ps:range")],
        [InlineKeyboardButton(text="🎯 Точно по ціні (Снайпер)", callback_data="tsell_ps:exact")],
    ])


def _calc_roi(amount: float, buy_price: float, profit_pct: float, network_fee: float) -> dict:
    """ROI Калькулятор для Taker Sell."""
    invest_uah = amount * buy_price
    target_fiat = invest_uah * (1.0 + profit_pct / 100.0)
    usable_volume = max(amount - network_fee, 0.001)
    min_sell_price = target_fiat / usable_volume
    net_profit_uah = target_fiat - invest_uah
    return {
        "invest_uah": round(invest_uah, 2),
        "target_fiat": round(target_fiat, 2),
        "network_fee": network_fee,
        "usable_volume": round(usable_volume, 4),
        "min_sell_price": round(min_sell_price, 4),
        "net_profit_uah": round(net_profit_uah, 2),
    }


def _get_network_fee(exchange: str) -> tuple[float, str]:
    """Повертає (fee_usdt, network_name) для найдешевшої мережі біржі."""
    if exchange == "INTERNAL":
        return 0.0, "внутрішній переказ"
    try:
        from core.engine.network_fee_engine import EXCHANGE_NETS, NETWORK_FEES
        nets = EXCHANGE_NETS.get(exchange, ["TRC20"])
        best = min(nets, key=lambda n: NETWORK_FEES.get(n, 999.0))
        return NETWORK_FEES.get(best, 1.0), best
    except Exception as e:
        logger.warning("NetworkFee fallback: %s", e)
        return 1.0, "TRC20 (fallback)"


async def _get_current_market_rate(db, amount_usdt: float) -> float:
    """Отримує середній курс USDT/UAH з БД, відфільтрований за об'ємом (щоб прибрати пил/мікро-ордери)."""
    if not db:
        return 41.50
    conn = getattr(db, "db", None) or getattr(db, "_db", db)
    try:
        import time
        now = time.time()
        since = now - 1800.0  # останні 30 хвилин
        async with conn.execute(
            "SELECT price, min_limit, max_limit FROM merchant_snapshots WHERE side = 'sell' AND recorded_at > ? ORDER BY recorded_at DESC LIMIT 100",
            (since,)
        ) as cur:
            rows = await cur.fetchall()
            
        if not rows:
            async with conn.execute(
                "SELECT price, min_limit, max_limit FROM merchant_snapshots WHERE side = 'sell' ORDER BY recorded_at DESC LIMIT 50"
            ) as cur:
                rows = await cur.fetchall()
                
        if rows:
            matching = []
            for r in rows:
                p = float(r["price"])
                min_lim = float(r["min_limit"])
                max_lim = float(r["max_limit"])
                fiat_val = amount_usdt * p
                if min_lim <= fiat_val <= max_lim:
                    matching.append(p)
            if matching:
                return sum(matching) / len(matching)
            # fallback: середнє з топ-5 останніх
            return sum(float(r["price"]) for r in rows[:5]) / min(len(rows), 5)
    except Exception as e:
        logger.warning(f"Error getting market rate: {e}")
    return 41.50


def _get_taker_sell_preset(user_row: dict | None) -> dict | None:
    if not user_row:
        return None
    amount = float(user_row.get("taker_sell_amount", 0))
    price = float(user_row.get("taker_sell_price", 0))
    profit = float(user_row.get("taker_sell_profit", 0))
    if amount > 0 and price > 0:
        return {
            "amount": amount,
            "buy_price": price,
            "profit": round(profit * 100, 2),
            "exchange": user_row.get("taker_sell_exchange", "—"),
            "min_sell_price": float(user_row.get("taker_sell_min_price", 0)),
            "speed": user_row.get("taker_sell_speed", "ANY"),
            "price_strategy": user_row.get("taker_sell_price_strategy", "roi"),
            "price_to": float(user_row.get("taker_sell_price_to", 0)),
        }
    return None


def _get_taker_buy_preset(user_row: dict | None) -> dict | None:
    if not user_row:
        return None
    amount = float(user_row.get("taker_buy_amount", 0))
    if amount > 0:
        return {
            "amount": amount,
            "max_price": float(user_row.get("taker_buy_max_price", 0)),
            "limit_min": float(user_row.get("taker_buy_limit_min", 0)),
            "limit_max": float(user_row.get("taker_buy_limit_max", 0)),
            "speed": user_row.get("taker_buy_speed", "ANY"),
            "price_strategy": user_row.get("taker_buy_price_strategy", "any"),  # ← додати
            "price_from": float(user_row.get("taker_buy_price_from", 0)),  # ← додати
        }
    return None


def _taker_preset_kb(mode: str) -> InlineKeyboardMarkup:
    if mode == "TAKER_SELL":
        return InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="🚀 Швидкий старт", callback_data="taker_qs:TAKER_SELL")],
            [InlineKeyboardButton(text="⚙️ Редагувати параметри", callback_data="tsell_edit_menu")],
            [InlineKeyboardButton(text="🔄 Налаштувати повністю заново", callback_data="taker_manual:TAKER_SELL")],
            [InlineKeyboardButton(text="🔙 Назад", callback_data="set:scanner_mode")]
        ])
    elif mode == "TAKER_BUY":
        return InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="🚀 Швидкий старт", callback_data="taker_qs:TAKER_BUY")],
            [InlineKeyboardButton(text="⚙️ Редагувати параметри", callback_data="tbuy_edit_menu")],
            [InlineKeyboardButton(text="🔄 Налаштувати повністю заново", callback_data="taker_manual:TAKER_BUY")],
            [InlineKeyboardButton(text="🔙 Назад", callback_data="set:scanner_mode")]
        ])
    return InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text="🚀 Швидкий старт", callback_data=f"taker_qs:{mode}"),
        InlineKeyboardButton(text="⚙️ Налаштувати вручну", callback_data=f"taker_manual:{mode}"),
    ]])


def _sell_final_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🚀 Запустити", callback_data="tsell:launch")],
        [InlineKeyboardButton(text="💾 Зберегти пресет і Запустити", callback_data="tsell:save_and_launch")],
        [InlineKeyboardButton(text="❌ Скасувати", callback_data="menu:main")],
    ])


def _buy_final_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🚀 Запустити", callback_data="tbuy:launch")],
        [InlineKeyboardButton(text="💾 Зберегти пресет і Запустити", callback_data="tbuy:save_and_launch")],
        [InlineKeyboardButton(text="❌ Скасувати", callback_data="menu:main")],
    ])


def _tbuy_banks_kb(selected: list) -> InlineKeyboardMarkup:
    selected_set = set(str(s) for s in selected)
    buttons, row = [], []
    for code, name in BANK_NAMES.items():
        icon = "✅" if str(code) in selected_set else "☐"
        row.append(InlineKeyboardButton(text=f"{icon} {name}", callback_data=f"tbuy_bank:{code}"))
        if len(row) == 2:
            buttons.append(row)
            row = []
    if row:
        buttons.append(row)
    buttons.append([InlineKeyboardButton(text="✅ Підтвердити вибір", callback_data="tbuy_bank:done")])
    return InlineKeyboardMarkup(inline_keyboard=buttons)


def _sell_preset_text(p: dict, current_rate: float = 0.0) -> str:
    strategy_labels = {
        "roi": "🧮 ROI авто",
        "min": "⬆️ Мінімальна",
        "range": "↔️ Діапазон",
        "exact": "🎯 Точна",
    }
    strategy = p.get("price_strategy", "roi")
    strat_line = f"  • Стратегія: <b>{strategy_labels.get(strategy, strategy)}</b>\n"

    mp_line = f"  • Мін. ціна: <b>{p['min_sell_price']:.4f} ₴</b>\n" if p.get("min_sell_price", 0) > 0 else ""
    speed = "⚡ FAST" if p.get("speed") == "FAST" else "🐢 ANY"
    
    amount = p["amount"]
    buy_price = p["buy_price"]
    invested_uah = amount * buy_price
    
    roi_lines = ""
    if buy_price > 0 and amount > 0:
        roi_lines += f"  • 💸 Вкладено: <b>{invested_uah:.0f} ₴</b>\n"
        if current_rate > 0:
            current_val = amount * current_rate
            current_profit = current_val - invested_uah
            current_roi = (current_profit / invested_uah) * 100 if invested_uah > 0 else 0.0
            sign = "+" if current_profit >= 0 else ""
            roi_lines += f"  • 📈 Поточна вартість: <b>{current_val:.0f} ₴</b> (Профіт: <b>{sign}{current_profit:.0f} ₴</b>, <b>{sign}{current_roi:.1f}%</b> за курсом {current_rate:.2f})\n"
            
        target_price = float(p.get("min_sell_price", 0.0))
        price_to = float(p.get("price_to", 0.0))
        if strategy == "range" and price_to > 0:
            target_price = price_to
            
        if target_price > 0:
            target_val = amount * target_price
            target_profit = target_val - invested_uah
            target_roi = (target_profit / invested_uah) * 100 if invested_uah > 0 else 0.0
            sign = "+" if target_profit >= 0 else ""
            label_desc = "Цільовий вихід" if strategy == "exact" else "Цільовий вихід (макс)" if strategy == "range" else "Цільовий вихід (мін)"
            roi_lines += f"  • 🎯 {label_desc}: <b>{target_val:.0f} ₴</b> (Профіт: <b>{sign}{target_profit:.0f} ₴</b>, <b>{sign}{target_roi:.1f}%</b> за ціною {target_price:.2f})\n"

    return (
        f"💾 <b>TAKER SELL — збережені налаштування</b>\n\n"
        f"  • Об'єм: <b>{amount:.1f} USDT</b>\n"
        f"  • Купівля: <b>{buy_price:.4f} ₴</b>\n"
        f"  • Біржа: <b>{p['exchange']}</b>\n"
        f"  • Прибуток: <b>{p['profit']:.2f}%</b>\n"
        f"{strat_line}{mp_line}"
        f"  • Швидкість: {speed}\n"
        f"{roi_lines}\n"
        f"Що робимо?"
    )


def _buy_preset_text(p: dict) -> str:
    strategy_labels = {
        "any": "🔓 Будь-яка",
        "max": "⬇️ Макс. ціна",
        "range": "↔️ Діапазон",
        "exact": "🎯 Точна",
    }
    strategy = p.get("price_strategy", "any")
    strat_line = f"  • Стратегія: <b>{strategy_labels.get(strategy, strategy)}</b>\n"

    # range — показуємо price_from теж
    if strategy == "range" and p.get("price_from", 0) > 0:
        strat_line += f"  • Від: <b>{p['price_from']:.4f} ₴</b>\n"

    mp_line = f"  • До: <b>{p['max_price']:.4f} ₴</b>\n" if p.get("max_price", 0) > 0 else ""
    lim_line = (f"  • Ліміти: <b>{p['limit_min']:.0f}–{p['limit_max']:.0f} ₴</b>\n"
                if p.get("limit_min", 0) > 0 or p.get("limit_max", 0) > 0 else "")
    speed = "⚡ FAST" if p.get("speed") == "FAST" else "🐢 ANY"
    return (
        f"💾 <b>TAKER BUY — збережені налаштування</b>\n\n"
        f"  • Об'єм: <b>{p['amount']:.1f} USDT</b>\n"
        f"{strat_line}{mp_line}{lim_line}"
        f"  • Швидкість: {speed}\n\n"
        f"Що робимо?"
    )


def _sell_roi_text(d: dict, roi: dict, current_rate: float = 0.0) -> str:
    speed_text = "⚡ Важлива (лише великі ордери)" if d.get("speed") == "FAST" else "🐢 Не важлива"
    
    amount = d["amount"]
    buy_price = d["buy_price"]
    invest_uah = amount * buy_price
    
    actual_line = ""
    if current_rate > 0:
        actual_uah = amount * current_rate
        diff_uah = actual_uah - invest_uah
        diff_sign = "+" if diff_uah >= 0 else ""
        diff_pct = (diff_uah / invest_uah) * 100 if invest_uah > 0 else 0.0
        actual_line = f"📈 Вартість по факту: <b>{actual_uah:.2f} ₴</b> ({diff_sign}{diff_uah:.2f} ₴, {diff_sign}{diff_pct:.1f}% за курсом {current_rate:.2f})\n"

    return (
        "🧮 <b>ROI КАЛЬКУЛЯТОР | Результат:</b>\n\n"
        f"📦 Продаю: <b>{amount:.1f} USDT</b>\n"
        f"💲 Ціна входу: <b>{buy_price:.4f} ₴</b>\n"
        f"📤 Біржа відправки: <b>{d.get('exchange', '—')}</b>\n"
        f"🌐 Network Fee: <b>{roi['network_fee']:.2f} USDT</b> ({d.get('network_name', '')})\n"
        f"⚡ Корисний об'єм: <b>{roi['usable_volume']:.2f} USDT</b>\n\n"
        f"💸 Вкладено: <b>{roi['invest_uah']:.2f} ₴</b>\n"
        f"{actual_line}"
        f"🎯 Цільовий виторг: <b>{roi['target_fiat']:.2f} ₴</b>\n"
        f"💰 Чистий профіт: <b>+{roi['net_profit_uah']:.2f} ₴</b>\n\n"
        f"🔒 <b>Мін. ціна продажу: {roi['min_sell_price']:.4f} ₴</b>\n"
        f"<i>Жорсткий фільтр — ордери нижче цієї ціни ігноруються</i>\n\n"
        f"⏱ Швидкість: {speed_text}"
    )


def _buy_confirm_text(d: dict) -> str:
    speed_text = "⚡ Важлива (ордер ≥ мій об'єм)" if d.get("speed") == "FAST" else "🐢 Не важлива"
    bank_names = [BANK_NAMES.get(str(c), str(c)) for c in d.get("banks", [])]
    banks_str = ", ".join(bank_names) if bank_names else "не обрано"
    mp_line = f"\n├ 💰 Макс. ціна входу: <b>{d['max_price']:.2f} ₴</b>" if d.get("max_price", 0) > 0 else ""
    lim_line = (
        f"\n├ 📏 Ліміти ордерів: <b>{d.get('limit_min', 0):.0f}–{d.get('limit_max', 0):.0f} ₴</b>"
        if d.get("limit_min", 0) > 0 or d.get("limit_max", 0) > 0 else ""
    )
    return (
        "🛒 <b>TAKER BUY | Підтвердження:</b>\n\n"
        f"├ 📦 Об'єм: <b>{d['amount']:.1f} USDT</b>"
        f"{mp_line}{lim_line}"
        f"\n├ 🏦 Банки: <b>{banks_str}</b>"
        f"\n└ ⏱ Швидкість: {speed_text}\n\n"
        "🚀 Запустити сканер?"
    )


async def _start_taker_sell_fsm(call: CallbackQuery, state: FSMContext) -> None:
    await state.clear()
    await state.update_data(pending_mode="TAKER_SELL")
    await state.set_state(TakerSellSettingsStates.waiting_amount_type)
    
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text="💵 USDT (крипта)", callback_data="tsell_type:USDT"),
            InlineKeyboardButton(text="₴ UAH (гривня)", callback_data="tsell_type:UAH"),
        ],
        [
            InlineKeyboardButton(text="🔙 Назад", callback_data="set:scanner_mode"),
        ]
    ])
    
    with suppress(TelegramBadRequest):
        await call.message.edit_text(
            "💸 <b>TAKER SELL — Крок 1/5</b>\n\n"
            "📦 <b>Оберіть спосіб введення об'єму для продажу:</b>\n"
            "<i>Ви можете вказати кількість у USDT або суму в гривнях, яку ви вклали.</i>",
            reply_markup=kb,
        )


async def _start_taker_buy_fsm(call: CallbackQuery, state: FSMContext) -> None:
    await state.clear()
    await state.update_data(pending_mode="TAKER_BUY")
    await state.set_state(TakerBuySettingsStates.waiting_amount)
    with suppress(TelegramBadRequest):
        await call.message.edit_text(
            "🛒 <b>TAKER BUY — Крок 1/5</b>\n\n"
            "📦 <b>Скільки USDT ти хочеш купити?</b>\n"
            "<i>Наприклад: 500</i>",
            reply_markup=back_to_main_kb(),
        )


async def _show_mode_success(call: CallbackQuery, mode: str) -> None:
    from bot.keyboards import scanner_mode_kb
    desc = {
        "SPREAD": "🔄 Класичний пошук зв'язок Купівля→Продаж",
        "TAKER_BUY": "🛒 Тейкер: пошук sell-ордерів для купівлі",
        "TAKER_SELL": "💸 Тейкер: пошук buy-ордерів для продажу",
        "MAKER_BUY": "📥 Мейкер: аналіз ринку + підказки ціни",
        "MAKER_SELL": "📤 Мейкер: розрахунок мін. ціни продажу",
    }
    with suppress(TelegramBadRequest):
        await call.message.edit_text(
            f"✅ Режим змінено на <b>{mode}</b>\n<i>{desc.get(mode, '')}</i>",
            reply_markup=scanner_mode_kb(mode),
        )


# ═══════════════════════════════════════════════════════════════════════════════
# 🎯 MODE ENTRY POINT — on_scanner_mode_set (REFACTORED)
# ═══════════════════════════════════════════════════════════════════════════════

@router.callback_query(F.data.startswith("smode:"))
async def on_scanner_mode_set(call: CallbackQuery, state: FSMContext) -> None:
    """
    ✅ ФІКС Bug 1: НЕ пише режим у БД при вході!
    Для TAKER_BUY / TAKER_SELL — перевіряє пресети, потім FSM.
    Запис у БД відбувається ТІЛЬКИ після натискання 🚀 Запустити.
    """
    mode = call.data.split(":")[1]

    old_mode = "SPREAD"
    user_row = None
    if _db:
        users = await _db.get_active_users()
        user_row = next((u for u in users if u["user_id"] == call.from_user.id), None)
        if user_row:
            old_mode = user_row.get("scanner_mode", "SPREAD")

    # ── Cleanup: зупиняємо мейкер-сервіси при виході з MAKER ──
    if old_mode in ("MAKER_SELL", "MAKER_BUY") and mode not in ("MAKER_SELL", "MAKER_BUY"):
        for key in list(_active_repricers):
            if str(call.from_user.id) in str(key):
                task = _active_repricers.pop(key, None)
                if task and not task.done():
                    task.cancel()
                    logger.info("🛑 AdRepricer зупинено (mode switch): %s", key)
        if _maker_monitor:
            with suppress(Exception):
                _maker_monitor.stop_all()

    # ── MAKER_SELL ──
    if mode == "MAKER_SELL":
        if user_row and float(user_row.get("maker_buy_price", 0)) <= 0:
            await state.set_state(MakerSettingsStates.waiting_buy_price)
            with suppress(TelegramBadRequest):
                await call.message.edit_text(
                    "📤 <b>MAKER SELL | Ціна входу</b>\n\n"
                    "💲 За скільки ти купив USDT? (UAH/USDT)\n"
                    "<i>Наприклад: 41.25</i>",
                    reply_markup=back_to_main_kb(),
                )
            return await call.answer()
        await _set_scanner_mode_db(call.from_user.id, mode)
        await _show_mode_success(call, mode)
        return

    # ── TAKER_SELL — Smart Presets ──
    if mode == "TAKER_SELL":
        preset = _get_taker_sell_preset(user_row)
        if preset:
            await state.update_data(pending_mode="TAKER_SELL")
            current_rate = await _get_current_market_rate(_db, preset["amount"])
            with suppress(TelegramBadRequest):
                await call.message.edit_text(
                    _sell_preset_text(preset, current_rate),
                    reply_markup=_taker_preset_kb("TAKER_SELL"),
                )
            return await call.answer()
        await _start_taker_sell_fsm(call, state)
        return await call.answer()

    # ── TAKER_BUY — Smart Presets ──
    if mode == "TAKER_BUY":
        preset = _get_taker_buy_preset(user_row)
        if preset:
            await state.update_data(pending_mode="TAKER_BUY")
            with suppress(TelegramBadRequest):
                await call.message.edit_text(
                    _buy_preset_text(preset),
                    reply_markup=_taker_preset_kb("TAKER_BUY"),
                )
            return await call.answer()
        await _start_taker_buy_fsm(call, state)
        return await call.answer()

    # ── SPREAD / MAKER_BUY / решта ──
    await _set_scanner_mode_db(call.from_user.id, mode)
    await _show_mode_success(call, mode)
    await call.answer(f"✅ {mode}")


# ── Smart Presets: Швидкий старт / Вручну ──────────────────────────────────

@router.callback_query(F.data.startswith("taker_qs:"))
async def on_taker_quick_start(call: CallbackQuery, state: FSMContext) -> None:
    """Швидкий старт — параметри вже в БД, просто активуємо режим."""
    mode = call.data.split(":")[1]
    await state.clear()
    if not _db:
        return await call.answer("❌ БД не підключена", show_alert=True)

    users = await _db.get_active_users()
    user_row = next((u for u in users if u["user_id"] == call.from_user.id), None)
    from bot.keyboards import scanner_mode_kb

    if mode == "TAKER_SELL":
        preset = _get_taker_sell_preset(user_row)
        if not preset:
            return await call.answer("❌ Пресет не знайдено", show_alert=True)
        await _set_scanner_mode_db(call.from_user.id, "TAKER_SELL")
        with suppress(TelegramBadRequest):
            await call.message.edit_text(
                "🚀 <b>TAKER SELL запущено!</b>\n\n"
                f"🔒 Мін. ціна продажу: <b>{preset.get('min_sell_price', 0):.4f} ₴</b>\n"
                f"📦 Об'єм: <b>{preset['amount']:.1f} USDT</b>\n\n"
                "<i>Алерти надходять як тільки з'являються ордери вище мін. ціни.</i>",
                reply_markup=scanner_mode_kb("TAKER_SELL"),
            )
    elif mode == "TAKER_BUY":
        preset = _get_taker_buy_preset(user_row)
        if not preset:
            return await call.answer("❌ Пресет не знайдено", show_alert=True)
        await _set_scanner_mode_db(call.from_user.id, "TAKER_BUY")
        with suppress(TelegramBadRequest):
            await call.message.edit_text(
                "🚀 <b>TAKER BUY запущено!</b>\n\n"
                f"📦 Шукаю ордери для купівлі <b>{preset['amount']:.1f} USDT</b>\n"
                + (f"💰 Макс. ціна: <b>{preset['max_price']:.2f} ₴</b>\n" if preset.get("max_price", 0) > 0 else "")
                + "\n<i>Алерти надходять одразу.</i>",
                reply_markup=scanner_mode_kb("TAKER_BUY"),
            )
    await call.answer("🚀 Запущено!")


@router.callback_query(F.data.startswith("taker_manual:"))
async def on_taker_manual(call: CallbackQuery, state: FSMContext) -> None:
    """Ручне налаштування — запускаємо FSM."""
    mode = call.data.split(":")[1]
    if mode == "TAKER_SELL":
        await _start_taker_sell_fsm(call, state)
    elif mode == "TAKER_BUY":
        await _start_taker_buy_fsm(call, state)
    await call.answer()


# =========================================================================
# 💰 ФІЛЬТР ЦІНИ (Price Range для тейкерів)
# =========================================================================

@router.callback_query(F.data == "set:price_range")
async def on_price_range_menu(call: CallbackQuery) -> None:
    from bot.keyboards import price_range_kb
    with suppress(TelegramBadRequest):
        await call.message.edit_text(
            "💰 <b>Фільтр ціни (UAH/USDT)</b>\n\n"
            "Обмежує ордери, які показує тейкер-режим.\n"
            "Оберіть тип фільтра:",
            reply_markup=price_range_kb(),
        )
    await call.answer()


@router.callback_query(F.data == "prange:off")
async def on_price_range_off(call: CallbackQuery) -> None:
    if _db:
        conn = getattr(_db, "db", None) or getattr(_db, "_db", _db)
        await conn.execute(
            "UPDATE scanner_users SET price_range_json = '{}' WHERE user_id = ?",
            (call.from_user.id,),
        )
        await conn.commit()
    await call.answer("✅ Фільтр ціни вимкнено", show_alert=True)
    with suppress(TelegramBadRequest):
        await call.message.edit_text("✅ Фільтр ціни вимкнено.", reply_markup=back_to_main_kb())


@router.callback_query(F.data == "prange:range")
async def on_price_range_range(call: CallbackQuery, state: FSMContext) -> None:
    await state.update_data(price_range_mode="range")
    await state.set_state(PriceRangeStates.waiting_range_min)
    with suppress(TelegramBadRequest):
        await call.message.edit_text(
            "📏 <b>Діапазон ціни</b>\n\nВведи <b>мінімальну</b> ціну (UAH):",
            reply_markup=back_to_main_kb(),
        )
    await call.answer()


@router.message(PriceRangeStates.waiting_range_min)
async def on_price_range_min_input(message: Message, state: FSMContext) -> None:
    try:
        val = float(message.text.strip().replace(",", "."))
        if val <= 0:
            raise ValueError
    except ValueError:
        return await message.answer("❌ Введи додатне число.")
    await state.update_data(price_min=val)
    await state.set_state(PriceRangeStates.waiting_range_max)
    await message.answer(f"✅ Мін: <b>{val:.2f}</b> ₴\n\nТепер введи <b>максимальну</b> ціну:")


@router.message(PriceRangeStates.waiting_range_max)
async def on_price_range_max_input(message: Message, state: FSMContext) -> None:
    try:
        val = float(message.text.strip().replace(",", "."))
        if val <= 0:
            raise ValueError
    except ValueError:
        return await message.answer("❌ Введи додатне число.")
    data = await state.get_data()
    price_min = data.get("price_min", 0)
    if val <= price_min:
        return await message.answer(f"❌ Максимум ({val}) повинен бути більше мінімального ({price_min}).")
    await state.clear()
    import json
    pr = json.dumps({"mode": "range", "min": price_min, "max": val})
    if _db:
        conn = getattr(_db, "db", None) or getattr(_db, "_db", _db)
        await conn.execute(
            "UPDATE scanner_users SET price_range_json = ? WHERE user_id = ?",
            (pr, message.from_user.id),
        )
        await conn.commit()
    await message.answer(
        f"✅ Фільтр ціни: <b>{price_min:.2f} — {val:.2f}</b> ₴",
        reply_markup=back_to_main_kb(),
    )


@router.callback_query(F.data.in_({"prange:exact", "prange:max", "prange:min"}))
async def on_price_range_single(call: CallbackQuery, state: FSMContext) -> None:
    mode = call.data.split(":")[1]
    labels = {"exact": "Точна ціна", "max": "Максимальна ціна", "min": "Мінімальна ціна"}
    await state.update_data(price_range_mode=mode)
    await state.set_state(PriceRangeStates.waiting_value)
    with suppress(TelegramBadRequest):
        await call.message.edit_text(
            f"🎯 <b>{labels[mode]}</b>\n\nВведи ціну (UAH):",
            reply_markup=back_to_main_kb(),
        )
    await call.answer()


@router.message(PriceRangeStates.waiting_value)
async def on_price_range_value_input(message: Message, state: FSMContext) -> None:
    try:
        val = float(message.text.strip().replace(",", "."))
        if val <= 0:
            raise ValueError
    except ValueError:
        return await message.answer("❌ Введи додатне число.")
    data = await state.get_data()
    mode = data.get("price_range_mode", "exact")
    await state.clear()
    import json
    pr = json.dumps({"mode": mode, "value": val})
    if _db:
        conn = getattr(_db, "db", None) or getattr(_db, "_db", _db)
        await conn.execute(
            "UPDATE scanner_users SET price_range_json = ? WHERE user_id = ?",
            (pr, message.from_user.id),
        )
        await conn.commit()
    labels = {"exact": f"≈{val:.2f}", "max": f"≤{val:.2f}", "min": f"≥{val:.2f}"}
    await message.answer(
        f"✅ Фільтр ціни: <b>{labels[mode]}</b> ₴",
        reply_markup=back_to_main_kb(),
    )


# =========================================================================
# 📝 СТВОРЕННЯ P2P ОГОЛОШЕННЯ (Create Ad Wizard)
# =========================================================================

@router.callback_query(F.data.startswith("fb:"))
async def on_feedback(call: CallbackQuery):
    if not _db:
        return await call.answer("База даних не підключена", show_alert=True)

    try:
        parts = call.data.split(":")
        if len(parts) != 4:
            return await call.answer("Помилка формату кнопок")

        _, exchange, mid, action = parts

        reason_map = {
            "triangle": "🚫 ТРЕТІ ОСОБИ (Ручний Blacklist)",
            "receipt": "🧾 СКАМ З ЧЕКОМ (Ручний Blacklist)",
            "chat": "📲 ТЯГНЕ В ТГ (Ручний Blacklist)",
            "fincrime": "🏴‍☠️ ФІНМОН/СХЕМА (Ручний Blacklist)"
        }

        if action not in reason_map:
            return await call.answer("Невідома дія")

        reason = reason_map[action]

        # Записуємо в глобальний Blacklist
        await _db.add_to_blacklist(exchange, mid, "Unknown", reason, "manual_tg")
        await call.answer(f"✅ Успіх! Заблоковано: {reason}", show_alert=True)

        # 📊 Feedback Loop: записуємо лічильники для аналізу пропущених ризиків
        # Ці дані дозволяють відстежувати, які категорії ризику найчастіше пропускає AI
        try:
            counter_key = f"feedback_loop_{action}"
            total_key = "feedback_loop_total"
            current_count = int(runtime_config.get(counter_key, "0") or "0")
            total_count = int(runtime_config.get(total_key, "0") or "0")
            await runtime_config.set(counter_key, str(current_count + 1))
            await runtime_config.set(total_key, str(total_count + 1))
            logger.info(
                "📊 Feedback loop: user=%d action=%s exchange=%s merchant=%s (total=%d %s=%d)",
                call.from_user.id, action, exchange, mid[:12],
                total_count + 1, action, current_count + 1,
            )
            # Prometheus counter
            from core.analytics.metrics import feedback_blacklist_total
            feedback_blacklist_total.labels(action=action).inc()
        except Exception as _fb_err:
            logger.debug("Feedback loop counter error: %s", _fb_err)

        # ── Feedback Loop Weights Adjustment ─────────────────────────────────
        try:
            conn = getattr(_db, "db", None) or getattr(_db, "_db", _db)
            
            # Read active signals from DB for this merchant
            regex_active = False
            llm_active = False
            behavior_active = False
            identity_active = False
            
            async with conn.execute(
                "SELECT verdict, risk_score, reason, risk_type FROM merchant_verdict WHERE exchange=? AND merchant_id=?",
                (exchange, mid)
            ) as cur:
                verdict_row = await cur.fetchone()
                
            if verdict_row:
                verdict_dict = dict(verdict_row)
                if (verdict_dict.get("risk_score") or 0) > 0:
                    regex_active = True
                
                v_str = str(verdict_dict.get("verdict") or "").upper()
                if v_str in ("SUSPICIOUS", "WARN", "NEEDS_LLM"):
                    llm_active = True
                    
                risk_t = str(verdict_dict.get("risk_type") or "").upper()
                reason_t = str(verdict_dict.get("reason") or "").lower()
                if risk_t == "BOT_API" or "bot" in reason_t or "behavior" in reason_t:
                    behavior_active = True
                if risk_t == "TRIANGLE" or "twin" in reason_t or "identity" in reason_t:
                    identity_active = True

            reviews_pct_active = False
            reviews_text_active = False
            
            async with conn.execute(
                "SELECT negative, bad_texts_json FROM merchant_reviews WHERE exchange=? AND merchant_id=?",
                (exchange, mid)
            ) as cur:
                reviews_row = await cur.fetchone()
                
            if reviews_row:
                rev_dict = dict(reviews_row)
                if (rev_dict.get("negative") or 0) > 0:
                    reviews_pct_active = True
                
                bad_txt = rev_dict.get("bad_texts_json")
                if bad_txt:
                    import json
                    try:
                        bad_arr = json.loads(bad_txt)
                        if bad_arr:
                            reviews_text_active = True
                    except Exception:
                        pass

            async with conn.execute(
                "SELECT min_limit, max_limit FROM merchant_snapshots WHERE exchange=? AND merchant_id=?",
                (exchange, mid)
            ) as cur:
                snapshots = await cur.fetchall()
            if snapshots:
                if any(s[0] == s[1] and s[0] > 0 for s in snapshots):
                    behavior_active = True

            # Deltas
            delta_weights = {
                "W_REGEX": 0.02 if regex_active else 0.0,
                "W_BEHAVIOR": 0.02 if behavior_active else 0.0,
                "W_REVIEWS_PCT": 0.02 if reviews_pct_active else 0.0,
                "W_REVIEWS_TEXT": 0.02 if reviews_text_active else 0.0,
                "W_LLM": 0.02 if llm_active else 0.0,
                "W_IDENTITY": 0.02 if identity_active else 0.0
            }
            
            # Fallback deltas if no active signals were found
            if sum(delta_weights.values()) == 0.0:
                if action == "triangle":
                    delta_weights["W_IDENTITY"] = 0.02
                    delta_weights["W_REGEX"] = 0.01
                elif action == "receipt":
                    delta_weights["W_REVIEWS_TEXT"] = 0.02
                    delta_weights["W_REGEX"] = 0.01
                elif action == "chat":
                    delta_weights["W_REGEX"] = 0.02
                elif action == "fincrime":
                    delta_weights["W_LLM"] = 0.02
                    delta_weights["W_BEHAVIOR"] = 0.01

            # Get current weights from runtime config or CompositeScorer defaults
            from core.engine.risk_engine import CompositeScorer
            current_w = {
                "W_REGEX": float(runtime_config.get("W_REGEX") or CompositeScorer.W_REGEX),
                "W_BEHAVIOR": float(runtime_config.get("W_BEHAVIOR") or CompositeScorer.W_BEHAVIOR),
                "W_REVIEWS_PCT": float(runtime_config.get("W_REVIEWS_PCT") or CompositeScorer.W_REVIEWS_PCT),
                "W_REVIEWS_TEXT": float(runtime_config.get("W_REVIEWS_TEXT") or CompositeScorer.W_REVIEWS_TEXT),
                "W_LLM": float(runtime_config.get("W_LLM") or CompositeScorer.W_LLM),
                "W_IDENTITY": float(runtime_config.get("W_IDENTITY") or CompositeScorer.W_IDENTITY),
            }
            
            # Apply delta
            raw_new = {k: current_w[k] + delta_weights[k] for k in current_w}
            
            # Iterative clamping and normalization solver
            bounds = {
                "W_REGEX": (0.05, 0.45),
                "W_BEHAVIOR": (0.05, 0.35),
                "W_REVIEWS_PCT": (0.05, 0.25),
                "W_REVIEWS_TEXT": (0.05, 0.20),
                "W_LLM": (0.05, 0.35),
                "W_IDENTITY": (0.05, 0.25)
            }
            
            w = raw_new.copy()
            for _ in range(15):
                tot = sum(w.values())
                if tot == 0:
                    w = {k: 1.0/6.0 for k in w}
                    break
                w = {k: v / tot for k, v in w.items()}
                
                clamped = {}
                free = {}
                for k, v in w.items():
                    low, high = bounds[k]
                    if v < low:
                        clamped[k] = low
                    elif v > high:
                        clamped[k] = high
                    else:
                        free[k] = v
                if not clamped:
                    break
                
                clamped_sum = sum(clamped.values())
                free_sum = sum(free.values())
                if free_sum > 0:
                    rem = 1.0 - clamped_sum
                    w = {}
                    for k in clamped:
                        w[k] = clamped[k]
                    for k in free:
                        w[k] = free[k] * (rem / free_sum)
                else:
                    break
                    
            tot = sum(w.values())
            optimized_w = {k: round(v / tot, 4) for k, v in w.items()}
            
            # Save via runtime_config
            for key, val in optimized_w.items():
                await runtime_config.set(key, str(val))
                
            # Immediately reload in CompositeScorer
            await CompositeScorer.load_weights(_db)
            
            logger.info("📊 Feedback loop weights adjusted: %s", optimized_w)
        except Exception as _w_err:
            logger.warning("Feedback loop weight adjustment error: %s", _w_err, exc_info=True)

        # Перекреслюємо повідомлення, щоб візуально закрити тікет
        old_text = call.message.html_text or "Ордер"
        new_text = f"🚨 <b>МЕРЧАНТ ЗАБЛОКОВАНИЙ (Blacklist)!</b>\nПричина: {reason}\nБіржа: {exchange}\n\n<del>{old_text[:3000]}</del>"

        # Прибираємо кнопки
        await call.message.edit_text(new_text, reply_markup=None)

    except Exception as e:
        logger.error("Помилка обробки кнопки: %s", e)
        await call.answer("Помилка БД при блокуванні", show_alert=True)


# ── Auto Cooldown FSM Handlers ──

class AutoCooldownStates(StatesGroup):
    waiting_for_window = State()
    waiting_for_tier1 = State()
    waiting_for_tier2 = State()
    waiting_for_tier3 = State()
    waiting_for_max_delay = State()


@router.callback_query(F.data == "set:auto_cooldown_menu")
async def on_auto_cooldown_menu(call: CallbackQuery, state: FSMContext) -> None:
    """Показує меню налаштування авто-затримки."""
    await state.clear()
    if not _db:
        return await call.answer("БД не підключена", show_alert=True)
    display = await _db.get_user_display_settings(call.message.chat.id)
    config = display.get("auto_cooldown_json") or {}
    
    from bot.keyboards.filters import auto_cooldown_settings_kb
    with suppress(TelegramBadRequest):
        await call.message.edit_text(
            "⏱ <b>Налаштування адаптивної авто-затримки</b>\n\n"
            "Тут ви можете налаштувати чутливість авто-затримки повідомлень.\n"
            "Бот аналізує к-ть повідомлень за вказане вікно та застосовує кд:\n"
            f"├ Вікно аналізу: <b>{config.get('window_seconds', 5.0)} сек</b>\n"
            f"├ Поріг 1 (кд 0.0s): < <b>{config.get('tiers', [{}])[0].get('threshold', 10) if config.get('tiers') else 10} пов.</b>\n"
            f"├ Поріг 2 (кд 0.3s): < <b>{config.get('tiers', [{}, {}])[1].get('threshold', 15) if len(config.get('tiers', [])) > 1 else 15} пов.</b>\n"
            f"├ Поріг 3 (кд 0.8s): < <b>{config.get('tiers', [{}, {}, {}])[2].get('threshold', 20) if len(config.get('tiers', [])) > 2 else 20} пов.</b>\n"
            f"└ Максимальний кд: <b>{config.get('tiers', [{}, {}, {}, {}])[3].get('delay', 1.5) if len(config.get('tiers', [])) > 3 else 1.5} сек</b>\n\n"
            "<i>Оберіть параметр для зміни:</i>",
            reply_markup=auto_cooldown_settings_kb(config),
        )
    await call.answer()


async def _get_auto_cooldown_config(chat_id: int) -> dict:
    display = await _db.get_user_display_settings(chat_id)
    return display.get("auto_cooldown_json") or {
        "window_seconds": 5.0,
        "tiers": [
            {"threshold": 10, "delay": 0.0},
            {"threshold": 15, "delay": 0.3},
            {"threshold": 20, "delay": 0.8},
            {"threshold": 9999, "delay": 1.5}
        ]
    }


@router.callback_query(F.data == "auto_cd:set_window")
async def on_set_window_click(call: CallbackQuery, state: FSMContext) -> None:
    await state.set_state(AutoCooldownStates.waiting_for_window)
    await call.message.answer(
        "⏱ <b>Вікно аналізу авто-затримки</b>\n\n"
        "Введіть розмір вікна аналізу в секундах (наприклад: <code>5.0</code>):",
        reply_markup=back_to_main_kb(),
    )
    await call.answer()


@router.message(AutoCooldownStates.waiting_for_window)
async def on_window_input(message: Message, state: FSMContext) -> None:
    try:
        val = float(message.text.strip().replace(",", "."))
        if val <= 0:
            raise ValueError
    except ValueError:
        return await message.answer("❌ Введіть додатне число.")
    
    config = await _get_auto_cooldown_config(message.chat.id)
    config["window_seconds"] = val
    await _db.update_user_auto_cooldown_json(message.chat.id, config)
    
    # Скидаємо кеш нотифікатора
    if _notifier:
        _notifier._display_settings_cache.pop(message.chat.id, None)

    await state.clear()
    await message.answer(
        f"✅ Вікно аналізу змінено на: <b>{val} сек</b>\n\nПовертаюсь до меню...",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="🔙 До меню авто-затримки", callback_data="set:auto_cooldown_menu")]])
    )


@router.callback_query(F.data == "auto_cd:set_t1")
async def on_set_t1_click(call: CallbackQuery, state: FSMContext) -> None:
    await state.set_state(AutoCooldownStates.waiting_for_tier1)
    await call.message.answer(
        "📊 <b>Поріг 1 (кд 0.0s)</b>\n\n"
        "Введіть кількість повідомлень за вікно, при якій затримка буде відсутня (0.0s). Наприклад, <code>10</code>:",
        reply_markup=back_to_main_kb(),
    )
    await call.answer()


@router.message(AutoCooldownStates.waiting_for_tier1)
async def on_t1_input(message: Message, state: FSMContext) -> None:
    try:
        val = int(message.text.strip())
        if val < 1:
            raise ValueError
    except ValueError:
        return await message.answer("❌ Введіть ціле число більше нуля.")
    
    config = await _get_auto_cooldown_config(message.chat.id)
    config["tiers"][0]["threshold"] = val
    await _db.update_user_auto_cooldown_json(message.chat.id, config)
    
    if _notifier:
        _notifier._display_settings_cache.pop(message.chat.id, None)

    await state.clear()
    await message.answer(
        f"✅ Поріг 1 змінено на: <b>{val} пов.</b>\n\nПовертаюсь до меню...",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="🔙 До меню авто-затримки", callback_data="set:auto_cooldown_menu")]])
    )


@router.callback_query(F.data == "auto_cd:set_t2")
async def on_set_t2_click(call: CallbackQuery, state: FSMContext) -> None:
    await state.set_state(AutoCooldownStates.waiting_for_tier2)
    await call.message.answer(
        "📊 <b>Поріг 2 (кд 0.3s)</b>\n\n"
        "Введіть кількість повідомлень за вікно, при якій затримка буде до 0.3s. Наприклад, <code>15</code>:",
        reply_markup=back_to_main_kb(),
    )
    await call.answer()


@router.message(AutoCooldownStates.waiting_for_tier2)
async def on_t2_input(message: Message, state: FSMContext) -> None:
    try:
        val = int(message.text.strip())
        if val < 1:
            raise ValueError
    except ValueError:
        return await message.answer("❌ Введіть ціле число більше нуля.")
    
    config = await _get_auto_cooldown_config(message.chat.id)
    while len(config["tiers"]) < 2:
        config["tiers"].append({"threshold": 15, "delay": 0.3})
    config["tiers"][1]["threshold"] = val
    await _db.update_user_auto_cooldown_json(message.chat.id, config)
    
    if _notifier:
        _notifier._display_settings_cache.pop(message.chat.id, None)

    await state.clear()
    await message.answer(
        f"✅ Поріг 2 змінено на: <b>{val} пов.</b>\n\nПовертаюсь до меню...",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="🔙 До меню авто-затримки", callback_data="set:auto_cooldown_menu")]])
    )


@router.callback_query(F.data == "auto_cd:set_t3")
async def on_set_t3_click(call: CallbackQuery, state: FSMContext) -> None:
    await state.set_state(AutoCooldownStates.waiting_for_tier3)
    await call.message.answer(
        "📊 <b>Поріг 3 (кд 0.8s)</b>\n\n"
        "Введіть кількість повідомлень за вікно, при якій затримка буде до 0.8s. Наприклад, <code>20</code>:",
        reply_markup=back_to_main_kb(),
    )
    await call.answer()


@router.message(AutoCooldownStates.waiting_for_tier3)
async def on_t3_input(message: Message, state: FSMContext) -> None:
    try:
        val = int(message.text.strip())
        if val < 1:
            raise ValueError
    except ValueError:
        return await message.answer("❌ Введіть ціле число більше нуля.")
    
    config = await _get_auto_cooldown_config(message.chat.id)
    while len(config["tiers"]) < 3:
        config["tiers"].append({"threshold": 20, "delay": 0.8})
    config["tiers"][2]["threshold"] = val
    await _db.update_user_auto_cooldown_json(message.chat.id, config)
    
    if _notifier:
        _notifier._display_settings_cache.pop(message.chat.id, None)

    await state.clear()
    await message.answer(
        f"✅ Поріг 3 змінено на: <b>{val} пов.</b>\n\nПовертаюсь до меню...",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="🔙 До меню авто-затримки", callback_data="set:auto_cooldown_menu")]])
    )


@router.callback_query(F.data == "auto_cd:set_max_delay")
async def on_set_max_delay_click(call: CallbackQuery, state: FSMContext) -> None:
    await state.set_state(AutoCooldownStates.waiting_for_max_delay)
    await call.message.answer(
        "⏳ <b>Максимальна затримка</b>\n\n"
        "Введіть максимальну затримку в секундах, яка застосовується при перевищенні порогу 3. Наприклад, <code>1.5</code>:",
        reply_markup=back_to_main_kb(),
    )
    await call.answer()


@router.message(AutoCooldownStates.waiting_for_max_delay)
async def on_max_delay_input(message: Message, state: FSMContext) -> None:
    try:
        val = float(message.text.strip().replace(",", "."))
        if val < 0:
            raise ValueError
    except ValueError:
        return await message.answer("❌ Введіть додатне число або 0.")
    
    config = await _get_auto_cooldown_config(message.chat.id)
    while len(config["tiers"]) < 4:
        config["tiers"].append({"threshold": 9999, "delay": 1.5})
    config["tiers"][3]["delay"] = val
    await _db.update_user_auto_cooldown_json(message.chat.id, config)
    
    if _notifier:
        _notifier._display_settings_cache.pop(message.chat.id, None)

    await state.clear()
    await message.answer(
        f"✅ Максимальну затримку змінено на: <b>{val} сек</b>\n\nПовертаюсь до меню...",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="🔙 До меню авто-затримки", callback_data="set:auto_cooldown_menu")]])
    )

