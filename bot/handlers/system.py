# bot/handlers/system.py
# Sniper, orders, experimental features, ads, hybrid trades, intercept.

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
from aiogram.utils.keyboard import InlineKeyboardBuilder

from bot.handlers.core import (
    _is_admin, _db, _bot, _notifier, _account_clients,
    _trade_worker, _single_leg_executor, is_muted, update_stats,
    _generate_dashboard_text, SniperStates, EXPERIMENTAL_FEATURES, _active_repricers, HybridTradeStates, _spread_cache,
)
from bot import keyboards
from bot.keyboards import back_to_main_kb
from config.banks import DEFAULT_BANK_CODES, BANK_NAMES
from config import settings
from config.runtime import runtime_config
from core.utils.tasks import spawn

router = Router()
logger = logging.getLogger(__name__)

@router.message(Command("sniper"))
async def cmd_sniper(message: Message) -> None:
    if not _db:
        return await message.answer("❌ База даних недоступна.")

    rules = await _db.get_sniper_rules(message.from_user.id)
    text = "🎯 <b>Снайпер-правила (Volume Sweeper)</b>\n\nБот повідомить зі звуком, якщо знайде об'ємну угоду з великим спредом, навіть в беззвучному режимі.\n\n"

    if not rules:
        text += "<i>У тебе ще немає правил.</i>"
    else:
        for idx, r in enumerate(rules, start=1):
            text += f"{idx}. {r['exchange']} | {r['direction']} | Спред: >{r['min_spread']}% | Об'єм: >{r['min_volume']}$\n"

    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="➕ Додати правило", callback_data="sniper_add")],
        [InlineKeyboardButton(text="🗑 Очистити правила", callback_data="sniper_clear")],
        [InlineKeyboardButton(text="🔙 Назад", callback_data="system_status")]
    ])
    await message.answer(text, reply_markup=kb)


@router.callback_query(F.data == "sniper_clear")
async def on_sniper_clear(call: CallbackQuery) -> None:
    if _db:
        await _db.update_sniper_rules(call.from_user.id, [])
    await call.message.edit_text("✅ Всі снайпер-правила видалено.")
    await call.answer()


@router.callback_query(F.data == "sniper_add")
async def on_sniper_add(call: CallbackQuery, state: FSMContext) -> None:
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="Binance", callback_data="sn_ex:Binance"),
         InlineKeyboardButton(text="Bybit", callback_data="sn_ex:Bybit")],
        [InlineKeyboardButton(text="OKX", callback_data="sn_ex:OKX"),
         InlineKeyboardButton(text="MEXC", callback_data="sn_ex:MEXC")]
    ])
    await state.set_state(SniperStates.waiting_exchange)
    await call.message.edit_text("🎯 Обери біржу для снайпер-ордера:", reply_markup=kb)
    await call.answer()


@router.callback_query(SniperStates.waiting_exchange, F.data.startswith("sn_ex:"))
async def on_sniper_exchange(call: CallbackQuery, state: FSMContext) -> None:
    exchange = call.data.split(":")[1]
    await state.update_data(exchange=exchange)
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="Твій НАПРЯМОК: Купівля (Ти Taker BUY)", callback_data="sn_dir:BUY")],
        [InlineKeyboardButton(text="Твій НАПРЯМОК: Продаж (Ти Taker SELL)", callback_data="sn_dir:SELL")]
    ])
    await state.set_state(SniperStates.waiting_direction)
    await call.message.edit_text(f"Біржа {exchange}. Який твій напрямок?", reply_markup=kb)
    await call.answer()


@router.callback_query(SniperStates.waiting_direction, F.data.startswith("sn_dir:"))
async def on_sniper_dir(call: CallbackQuery, state: FSMContext) -> None:
    direction = call.data.split(":")[1]
    await state.update_data(direction=direction)
    await state.set_state(SniperStates.waiting_min_spread)
    await call.message.edit_text("Від якого спреду подавати алерт? (напр. 1.5):")
    await call.answer()


@router.message(SniperStates.waiting_min_spread)
async def on_sniper_spread(message: Message, state: FSMContext) -> None:
    try:
        val = float(message.text.strip().replace(",", "."))
    except ValueError:
        return await message.answer("❌ Будь ласка, введи число:")
    await state.update_data(min_spread=val)
    await state.set_state(SniperStates.waiting_min_volume)
    await message.answer("Від якого об'єму? (в USDT/еквіваленті):")


@router.message(SniperStates.waiting_min_volume)
async def on_sniper_volume(message: Message, state: FSMContext) -> None:
    try:
        val = float(message.text.strip().replace(",", "."))
    except ValueError:
        return await message.answer("❌ Будь ласка, введи число:")

    data = await state.get_data()
    rule = {
        "exchange": data.get("exchange"),
        "direction": data.get("direction"),
        "min_spread": data.get("min_spread"),
        "min_volume": val
    }

    if _db:
        rules = await _db.get_sniper_rules(message.from_user.id)
        rules.append(rule)
        await _db.update_sniper_rules(message.from_user.id, rules)

    await state.clear()
    await message.answer("✅ Снайпер-правило додано! Переглянути: /sniper")


# =========================================================================
# 📋 КЕРУВАННЯ УГОДАМИ (ORDERS)
# =========================================================================

@router.message(Command("orders"))
async def cmd_orders(message: Message) -> None:
    if not _db:
        return await message.answer("❌ БД не підключена.")

    trades = await _db.get_user_active_trades(message.from_user.id)
    if not trades:
        return await message.answer("📭 У тебе немає активних P2P угод на даний момент.")

    lines = ["📋 <b>Мої активні P2P угоди:</b>\n"]
    for idx, t in enumerate(trades, 1):
        strategy = t.get("session_strategy", "—")
        exchange = t.get("exchange", "—")
        role = t.get("leg", "—")
        status = t.get("status", "—")
        fiat = t.get("fiat_amount", 0.0)

        status_text = status
        if status == "PENDING_PAYMENT":
            status_text = "⏳ Очікує твоєї оплати" if role == "BUY" else "⏳ Чекаємо оплату покупця"
        elif status == "WAITING_COUNTERPARTY" or status == "WAITING_BUYER":
            status_text = "👀 Чекаємо зустрічного мейкера/тейкера"

        lines.append(
            f"{idx}. <b>{exchange} ({strategy})</b> | {role}\n"
            f"   💰 Сума: <code>{fiat:.2f} UAH</code>\n"
            f"   📊 Статус: <i>{status_text}</i>\n"
        )

    await message.answer("\n".join(lines), reply_markup=back_to_main_kb())


# =========================================================================
# 🛠 ЕКСПЕРИМЕНТАЛЬНІ ФУНКЦІЇ (FEATURES)
# =========================================================================

# 1. Головна команда
@router.message(Command("features"))
async def cmd_experimental_features(message: Message):
    text = "🛠 <b>Експериментальні функції Arbix Quantum</b>\n\nОберіть категорію для налаштування інструментів розробки:"
    kb = []
    for cat_id, cat_data in EXPERIMENTAL_FEATURES.items():
        kb.append([InlineKeyboardButton(text=cat_data["title"], callback_data=f"feat:cat:{cat_id}")])

    await message.answer(text, reply_markup=InlineKeyboardMarkup(inline_keyboard=kb))


# 2. Callback: Перегляд категорії
@router.callback_query(F.data.startswith("feat:cat:"))
async def cb_features_category(call: CallbackQuery):
    cat_id = call.data.split(":")[-1]
    cat_data = EXPERIMENTAL_FEATURES.get(cat_id)
    if not cat_data: return await call.answer("Категорію не знайдено.")

    text = f"🛠 <b>Категорія: {cat_data['title']}</b>\n\nОберіть функцію для редагування її стану в додатку:"
    kb = []
    for feat_key, feat in cat_data["features"].items():
        status_icon = "🟢" if await _db.get_feature_status(call.from_user.id, feat_key) else "🔴"
        kb.append([InlineKeyboardButton(text=f"{status_icon} {feat['name']}",
                                        callback_data=f"feat:view:{cat_id}:{feat_key}")])

    kb.append([InlineKeyboardButton(text="⬅️ Назад до категорій", callback_data="feat:main")])
    await call.message.edit_text(text, reply_markup=InlineKeyboardMarkup(inline_keyboard=kb))


# 3. Callback: Перегляд конкретної фічі
@router.callback_query(F.data.startswith("feat:view:"))
async def cb_features_view(call: CallbackQuery):
    parts = call.data.split(":")
    cat_id, feat_key = parts[2], parts[3]

    cat_data = EXPERIMENTAL_FEATURES.get(cat_id)
    if not cat_data or feat_key not in cat_data["features"]:
        return await call.answer("Функцію не знайдено.")

    feat = cat_data["features"][feat_key]
    is_active = await _db.get_feature_status(call.from_user.id, feat_key)

    status_label = "🟢 УВІМКНЕНО" if is_active else "🔴 ВИМКНЕНО"
    toggle_label = "🔴 Вимкнути" if is_active else "🟢 Увімкнути"

    text = (
        f"⚙️ <b>{feat['name']}</b>\n\n"
        f"📝 <b>Опис:</b> {feat['desc']}\n\n"
        f"📌 <b>Поточний статус:</b> {status_label}"
    )

    kb = [
        [InlineKeyboardButton(text=toggle_label, callback_data=f"feat:toggle:{cat_id}:{feat_key}")],
        [InlineKeyboardButton(text="⬅️ Назад до списку", callback_data=f"feat:cat:{cat_id}")]
    ]
    await call.message.edit_text(text, reply_markup=InlineKeyboardMarkup(inline_keyboard=kb))


# 4. Callback: Перемикач стану (Toggle)
@router.callback_query(F.data.startswith("feat:toggle:"))
async def cb_features_toggle(call: CallbackQuery):
    parts = call.data.split(":")
    cat_id, feat_key = parts[2], parts[3]

    # Змінюємо статус у БД
    new_state = await _db.toggle_feature_status(call.from_user.id, feat_key)
    await call.answer(f"Status updated: {'Enabled' if new_state else 'Disabled'}")

    # Перерендерюємо картку фічі зі свіжим статусом
    return await cb_features_view(call)


# 5. Callback: Повернення на головне меню фіч
@router.callback_query(F.data == "feat:main")
async def cb_features_main_menu(call: CallbackQuery):
    text = "🛠 <b>Експериментальні функції Arbix Quantum</b>\n\nОберіть категорію для налаштування інструментів розробки:"
    kb = []
    for cat_id, cat_data in EXPERIMENTAL_FEATURES.items():
        kb.append([InlineKeyboardButton(text=cat_data["title"], callback_data=f"feat:cat:{cat_id}")])
    kb.append([InlineKeyboardButton(text="🔙 Назад до системи", callback_data="menu:system")])
    await call.message.edit_text(text, reply_markup=InlineKeyboardMarkup(inline_keyboard=kb))


# =========================================================================
# 📢 КЕРУВАННЯ ОГОЛОШЕННЯМИ (ADS)
# =========================================================================

@router.message(Command("ads"))
async def cmd_ads(message: Message) -> None:
    user_id_str = str(message.from_user.id)
    user_ads = {k: v for k, v in _active_repricers.items() if k.startswith(f"{user_id_str}_")}

    if not user_ads:
        return await message.answer("📭 У тебе немає активних AdRepricer (мейкер-оголошень в авто-оновленні).")

    for key, task in user_ads.items():
        ad_id = key.split("_")[1]
        text = f"📢 <b>Оголошення {ad_id}</b>\nСтатус: {'🛑 Зупинено' if task.done() else '🟢 Оновлюється'}"

        kb = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="❌ Зупинити бота", callback_data=f"stop_ad:{key}")]
        ])
        await message.answer(text, reply_markup=kb)


@router.callback_query(F.data.startswith("stop_ad:"))
async def on_stop_ad(call: CallbackQuery) -> None:
    key = call.data.split(":")[1]
    task = _active_repricers.pop(key, None)
    if task:
        if not task.done():
            task.cancel()
        await call.message.edit_text(
            call.message.html_text + "\n\n✅ <i>Репрайсер успішно зупинено. Оголошення залишається на біржі, але більше не оновлюється.</i>",
            reply_markup=None)
    else:
        await call.answer("Завдання не знайдено або вже зупинено.", show_alert=True)
        await call.message.edit_text(call.message.html_text + "\n\n❌ <i>Завдання вже було зупинено.</i>",
                                     reply_markup=None)


# =========================================================================
# 🔄 ГІБРИДНІ МАРШРУТИ (T→M / M→T)
# =========================================================================

@router.callback_query(F.data.startswith("trade:tm:"))
async def on_trade_tm(call: CallbackQuery, state: FSMContext) -> None:
    if not _trade_worker:
        return await call.answer("❌ TradeWorker не підключено!", show_alert=True)

    cache_key = call.data.split(":", 2)[2]
    spread_data = _spread_cache.get(cache_key)
    if not spread_data:
        return await call.answer("❌ Зв'язка застаріла (>5 хв).", show_alert=True)

    alert, _ts = spread_data
    buy_leg = {
        "exchange": alert.buy_exchange, "ad_id": getattr(alert, "buy_ad_id", ""),
        "price": alert.buy_price, "merchant_id": getattr(alert, "buy_merchant_id", ""),
        "min_limit": getattr(alert, "buy_min_limit", 0),
        "max_limit": getattr(alert, "buy_max_limit", 0),
    }

    min_usdt = buy_leg["min_limit"] / buy_leg["price"] if buy_leg["price"] > 0 else 0
    max_usdt = buy_leg["max_limit"] / buy_leg["price"] if buy_leg["price"] > 0 else 0

    await state.update_data(
        tm_buy_leg=buy_leg,
        tm_sell_exchange=alert.sell_exchange,
        tm_min_usdt=min_usdt, tm_max_usdt=max_usdt
    )
    await state.set_state(HybridTradeStates.waiting_tm_amount)

    text = (
        f"🚨 <b>УВАГА: Авто T→M трейд</b>\n\n"
        f"<i>Бот одразу купить крипту як Taker, після чого автоматично виставить твоє Maker SELL оголошення.</i>\n\n"
        f"Ліміти продавця: {min_usdt:.1f} – {max_usdt:.1f} USDT\n"
        f"Введи об'єм угоди (USDT) або натисни /cancel для відміни:"
    )
    await call.message.answer(text)
    await call.answer()


@router.message(HybridTradeStates.waiting_tm_amount)
async def process_tm_amount(message: Message, state: FSMContext) -> None:
    try:
        amount_usdt = float(message.text.strip())
    except ValueError:
        return await message.answer("❌ Некоректний формат числа.")

    data = await state.get_data()
    min_u = data.get("tm_min_usdt", 0)
    max_u = data.get("tm_max_usdt", 0)

    if amount_usdt < min_u or (max_u > 0 and amount_usdt > max_u):
        return await message.answer(f"❌ Сума не відповідає лімітам {min_u:.1f} - {max_u:.1f} USDT.")

    await state.clear()
    await message.answer("⏳ Запускаю гібридний маршрут T→M...\nОчікуй сповіщення про створення угоди.")

    spawn(
        _trade_worker.execute_tm_route(
            buy_leg=data["tm_buy_leg"],
            sell_exchange=data["tm_sell_exchange"],
            amount_usdt=amount_usdt,
            owner_user_id=message.from_user.id,
        ),
        f"tm-route-{message.from_user.id}",
        logger_=logger,
    )


# =========================================================================
# 📱 РУЧНЕ ПЕРЕХОПЛЕННЯ СЕСІЙ (Bookmarklet)
# =========================================================================

@router.callback_query(F.data.startswith("intercept:"))
async def on_intercept_session(call: CallbackQuery, state: FSMContext) -> None:
    exchange = call.data.split(":")[1]
    user_id = call.from_user.id

    # Визначаємо хост. Використовуємо public_url з .env, або override з БД, або локалку.
    domain = getattr(settings, "public_url", None) or "http://192.168.1.100:8000"
    domain = runtime_config.get("api_domain", domain)

    # Ендпоінт закритий API-ключем. Букмарклет не може слати кастомні
    # заголовки крізь редагування закладки, тому ключ іде query-параметром.
    from urllib.parse import quote
    _api_key = getattr(settings, "api_key", "") or ""
    key_qs = f"?api_key={quote(_api_key, safe='')}" if _api_key else ""

    js_code = f"""javascript:(function(){{
    let c = document.cookie;
    fetch('{domain}/api/v1/session/receive{key_qs}', {{
        method: 'POST',
        headers: {{'Content-Type': 'application/json'}},
        body: JSON.stringify({{
            exchange: '{exchange}',
            user_id: {user_id},
            cookies_str: c
        }})
    }}).then(r => alert('Сесія {exchange} успішно передана боту!'))
      .catch(e => alert('Помилка: ' + e));
}})();"""

    # Видаляємо зайві пробіли (Bookmarklet має бути 1 рядком)
    js_code_inline = "".join(line.strip() for line in js_code.splitlines())

    text = (
        f"📲 <b>Оновлення сесії {exchange} через телефон (Bookmarklet)</b>\n\n"
        f"<b>Що це таке?</b>\n"
        f"Bookmarklet — це скрипт, який копіює Cookies авторизованого мобільного браузера "
        f"та безпечно надсилає їх у твій бот для автоматичного аналізу відгуків мерчантів.\n\n"
        f"📋 <b>Покрокова інструкція для ТЕЛЕФОНУ:</b>\n\n"
        f"1️⃣ <b>Скопіюй код скрипта</b> нижче (просто натисни на нього).\n"
        f"2️⃣ <b>Створи будь-яку закладку</b> в браузері (наприклад, Safari чи Chrome) на будь-який сайт (навіть Google.com).\n"
        f"3️⃣ <b>Відредагуй закладку</b>: назви її <code>Arbix</code>, а в полі URL/адреси повністю видали стару адресу і <b>встав скопійований код скрипта</b>.\n"
        f"4️⃣ <b>Увійди на біржу</b>: відкрий нову вкладку, зайди на сайт P2P {exchange} та переконайся, що ти успішно залогінений у свій акаунт.\n"
        f"5️⃣ <b>Запусти скрипт</b>:\n"
        f"   • Натисни на <b>адресний рядок</b> браузера.\n"
        f"   • Почни вводити слово <code>Arbix</code>.\n"
        f"   • У списку пропозицій (підказках пошуку) з'явиться наша закладка <b>Arbix</b> з іконкою зірочки/закладки.\n"
        f"   • <b>Натисни на неї в підказках!</b>\n\n"
        f"🎉 Браузер виконає скрипт, зловить твої Cookies і подовжить сесію в боті! На екрані з'явиться віконце з підтвердженням.\n\n"
        f"🖥 <i>Код скрипта (натисни щоб скопіювати):</i>\n\n"
        f"<code>{js_code_inline}</code>"
    )

    await call.message.answer(text)
    await call.answer()


@router.message(Command("intercept"))
async def cmd_intercept(message: Message) -> None:
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🟡 Binance", callback_data="intercept:Binance")],
        [InlineKeyboardButton(text="⚫ Bybit", callback_data="intercept:Bybit")],
        [InlineKeyboardButton(text="⚪ OKX", callback_data="intercept:OKX")]
    ])
    await message.answer("📲 Вибери біржу для якої згенерувати скрипт-закладку (Bookmarklet):", reply_markup=kb)


@router.callback_query(F.data == "boot_ignore_sessions")
async def on_boot_ignore_sessions(call: CallbackQuery) -> None:
    # Запускаємо сканер навіть без сесій
    await runtime_config.set("is_scanner_active", "true")
    # Відключаємо перевірку при наступному рестарті до ручного вмикання
    await runtime_config.set("require_sessions", "false")

    await call.message.edit_text(
        "🏃‍♂️ <b>Запуск без сесій</b>\n\n"
        "Сканер активовано! Збираємо мерчантів без глибокого аналізу репутації.\n"
        "<i>Ти можеш оновити сесії пізніше через налаштування.</i>",
        reply_markup=None
    )
    await call.answer("Сканер запущено!")


@router.callback_query(F.data.startswith("trade:mt:"))
async def on_trade_mt(call: CallbackQuery, state: FSMContext) -> None:
    if not _trade_worker:
        return await call.answer("❌ TradeWorker не підключено!", show_alert=True)

    cache_key = call.data.split(":", 2)[2]
    spread_data = _spread_cache.get(cache_key)
    if not spread_data:
        return await call.answer("❌ Зв'язка застаріла (>5 хв).", show_alert=True)

    alert, _ts = spread_data
    sell_leg = {
        "exchange": alert.sell_exchange, "ad_id": getattr(alert, "sell_ad_id", ""),
        "price": alert.sell_price, "merchant_id": getattr(alert, "sell_merchant_id", ""),
        "min_limit": getattr(alert, "sell_min_limit", 0),
        "max_limit": getattr(alert, "sell_max_limit", 0),
    }

    min_usdt = sell_leg["min_limit"] / sell_leg["price"] if sell_leg["price"] > 0 else 0
    max_usdt = sell_leg["max_limit"] / sell_leg["price"] if sell_leg["price"] > 0 else 0

    await state.update_data(
        mt_sell_leg=sell_leg,
        mt_buy_exchange=alert.buy_exchange,
        mt_buy_price=alert.buy_price,
        mt_min_usdt=min_usdt, mt_max_usdt=max_usdt
    )
    await state.set_state(HybridTradeStates.waiting_mt_amount)

    text = (
        f"🚨 <b>УВАГА: Авто M→T трейд</b>\n\n"
        f"<i>Бот виставить Maker BUY оголошення по {alert.buy_price} UAH. Коли тобі продадуть крипту, бот миттєво зіллє її по Taker-ордеру!</i>\n\n"
        f"Місткість Taker покупця: {max_usdt:.1f} USDT\n"
        f"Введи об'єм твоєї купівлі (USDT) або натисни /cancel для відміни:"
    )
    await call.message.answer(text)
    await call.answer()


@router.message(HybridTradeStates.waiting_mt_amount)
async def process_mt_amount(message: Message, state: FSMContext) -> None:
    try:
        amount_usdt = float(message.text.strip())
    except ValueError:
        return await message.answer("❌ Некоректний формат числа.")

    data = await state.get_data()
    max_u = data.get("mt_max_usdt", 0)

    if max_u > 0 and amount_usdt > max_u:
        return await message.answer(f"❌ Ти не зможеш злити стільки крипти, Taker приймає максимум {max_u:.1f} USDT.")

    await state.clear()
    await message.answer("⏳ Запускаю гібридний маршрут M→T...\nОголошення скоро з'явиться у стакані.")

    spawn(
        _trade_worker.execute_mt_route(
            buy_exchange=data["mt_buy_exchange"],
            buy_price=data["mt_buy_price"],
            sell_leg=data["mt_sell_leg"],
            amount_usdt=amount_usdt,
            owner_user_id=message.from_user.id,
        ),
        f"mt-route-{message.from_user.id}",
        logger_=logger,
    )


# ═══════════════════════════════════════════════════════════════════════════════
# SYSTEM ADMINISTRATION AND DIAGNOSTICS HANDLERS (Phase 3 UX Redesign)
# ═══════════════════════════════════════════════════════════════════════════════

@router.callback_query(F.data == "menu:system")
async def cb_system_menu(call: CallbackQuery, state: FSMContext) -> None:
    await state.clear()
    is_admin = _is_admin(call.from_user.id)
    is_active = runtime_config.get("is_scanner_active", "false") == "true"
    
    # We check if the bot is currently muted
    from bot.handlers.core import is_muted
    muted = is_muted()
    
    if is_admin:
        text = (
            "⚙️ <b>ARBIX QUANTUM | Адміністрування системи</b>\n\n"
            "Панель управління ядром сканера, лімітами, експериментальними "
            "функціями, користувачами та системним перезапуском.\n\n"
            f"├ Стан ядра: <b>{'АКТИВНИЙ 🟢' if is_active else 'ЗУПИНЕНИЙ 🔴'}</b>\n"
            f"└ Пауза сповіщень: <b>{'АКТИВНА 🔕' if muted else 'НЕАКТИВНА 🔔'}</b>\n\n"
            "<i>Оберіть потрібну дію:</i>"
        )
    else:
        text = (
            "⚙️ <b>ARBIX QUANTUM | Налаштування системи</b>\n\n"
            "Панель налаштування паузи сповіщень та експериментальних функцій.\n\n"
            f"└ Пауза сповіщень: <b>{'АКТИВНА 🔕' if muted else 'НЕАКТИВНА 🔔'}</b>\n\n"
            "<i>Оберіть потрібну дію:</i>"
        )
        
    with suppress(TelegramBadRequest):
        await call.message.edit_text(
            text,
            reply_markup=keyboards.system_menu_kb(is_scanner_active=is_active, is_muted=muted, is_admin=is_admin)
        )
    await call.answer()


@router.callback_query(F.data == "sys:users")
@router.callback_query(F.data.startswith("sys:users:toggle:"))
async def cb_sys_users(call: CallbackQuery):
    if not _is_admin(call.from_user.id):
        return await call.answer("⛔ Доступ дозволено тільки адміну", show_alert=True)
        
    conn = getattr(_db, "db", None) or getattr(_db, "_db", _db)
    
    # Check if this is a toggle action
    parts = call.data.split(":")
    if len(parts) > 3 and parts[2] == "toggle":
        target_user_id = int(parts[3])
        # Get current status
        async with conn.execute("SELECT is_active FROM scanner_users WHERE user_id = ?", (target_user_id,)) as cur:
            row = await cur.fetchone()
        if row:
            new_active = 0 if row[0] else 1
            await conn.execute("UPDATE scanner_users SET is_active = ? WHERE user_id = ?", (new_active, target_user_id))
            await conn.commit()
            await call.answer(f"Статус користувача {target_user_id} змінено на {'активний' if new_active else 'неактивний'}!")
            
    # Load all users
    async with conn.execute("SELECT user_id, telegram_chat_id, is_active, scanner_mode FROM scanner_users") as cur:
        rows = await cur.fetchall()
        
    text_lines = ["👥 <b>Управління користувачами</b>\n", f"Всього в базі: <b>{len(rows)}</b>\n"]
    builder = InlineKeyboardBuilder()
    
    for row in rows:
        uid = row["user_id"]
        chat_id = row["telegram_chat_id"]
        is_act = row["is_active"]
        mode = row["scanner_mode"]
        
        status_icon = "🟢" if is_act else "🔴"
        text_lines.append(f"• 👤 <b>{uid}</b> (Chat: {chat_id})\n  └ Режим: <code>{mode}</code> | Статус: {status_icon}")
        
        # Add toggle button for each user
        btn_label = f"Toggle {uid} ({'🔴' if is_act else '🟢'})"
        builder.row(InlineKeyboardButton(text=btn_label, callback_data=f"sys:users:toggle:{uid}"))
        
    builder.row(InlineKeyboardButton(text="🔙 Назад до системи", callback_data="menu:system"))
    
    with suppress(TelegramBadRequest):
        await call.message.edit_text("\n".join(text_lines), reply_markup=builder.as_markup())
    await call.answer()


@router.callback_query(F.data == "sys:debug")
@router.callback_query(F.data == "sys:clear_caches")
async def cb_sys_debug(call: CallbackQuery):
    if not _is_admin(call.from_user.id):
        return await call.answer("⛔ Доступ дозволено тільки адміну", show_alert=True)
        
    # Check cache clear action
    if call.data == "sys:clear_caches":
        # Clear caches in notifier / card_notifier
        from bot.handlers.core import _single_leg_cache, _spread_cache, _taker_order_cache
        from bot.card_notifier import _card_matching_cache
        _single_leg_cache.clear()
        _spread_cache.clear()
        _taker_order_cache.clear()
        _card_matching_cache.clear()
        await call.answer("🧹 Всі системні кеші успішно очищено!", show_alert=True)
        
    import sys
    import os
    import time
    
    mem_mb = 0.0
    cpu_pct = 0.0
    uptime_str = "Невідомо"
    try:
        import psutil
        process = psutil.Process(os.getpid())
        mem_mb = process.memory_info().rss / (1024 * 1024)
        cpu_pct = process.cpu_percent(interval=None)
        create_time = process.create_time()
        uptime_sec = time.time() - create_time
        days = int(uptime_sec // 86400)
        hours = int((uptime_sec % 86400) // 3600)
        minutes = int((uptime_sec % 3600) // 60)
        uptime_str = f"{days}д {hours}г {minutes}хв"
    except Exception:
        pass
        
    active_tasks = len(asyncio.all_tasks())
    is_active = runtime_config.get("is_scanner_active", "false") == "true"
    status_text = "🟢 АКТИВНИЙ" if is_active else "🔴 ЗУПИНЕНИЙ"
    
    text = (
        f"🔄 <b>Системна діагностика & Debug</b>\n\n"
        f"├ Стан ядра: <b>{status_text}</b>\n"
        f"├ Uptime процесу: <b>{uptime_str}</b>\n"
        f"├ Споживання пам'яті: <b>{mem_mb:.1f} MB</b>\n"
        f"├ CPU: <b>{cpu_pct:.1f}%</b>\n"
        f"├ Активних тасків: <b>{active_tasks}</b>\n"
        f"└ Python version: <code>{sys.version.split()[0]}</code>\n\n"
        f"⚠️ <i>Натискання «Перезапуск» завершить роботу поточного процесу. Бот перезапуститься автоматично, якщо налаштовано PM2/Docker/systemd.</i>"
    )
    
    builder = InlineKeyboardBuilder()
    builder.row(InlineKeyboardButton(text="💀 Перезапустити процес", callback_data="sys:restart_process"))
    builder.row(InlineKeyboardButton(text="🧹 Очистити кеші", callback_data="sys:clear_caches"))
    builder.row(InlineKeyboardButton(text="🔙 Назад до системи", callback_data="menu:system"))
    
    with suppress(TelegramBadRequest):
        await call.message.edit_text(text, reply_markup=builder.as_markup())
    await call.answer()


@router.callback_query(F.data == "sys:restart_process")
async def cb_sys_restart_process(call: CallbackQuery):
    if not _is_admin(call.from_user.id):
        return await call.answer("⛔ Доступ дозволено тільки адміну", show_alert=True)
        
    await call.message.edit_text("💀 <b>Процес завершує роботу...</b>\n\nБот вимикається для перезавантаження.")
    await call.answer("Перезапуск процесу...", show_alert=True)
    
    # Wait for message to be sent
    await asyncio.sleep(1.0)
    import sys
    sys.exit(0)

