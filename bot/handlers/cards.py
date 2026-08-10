# bot/handlers/cards.py
# Cards dashboard, add/edit/delete, limits, mono, card match, reports.

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

from bot import keyboards
from bot.handlers.core import (
    _is_admin, _db, _bot, _notifier, _account_clients,
    _trade_worker, _single_leg_executor, is_muted, update_stats,
    _generate_dashboard_text,
    CardAddStates, CardEditStates, CardUpdateStates, MonoStates, BankLimitStates, CardLimitStates,
    SettingStates
)
from config.banks import (
    DEFAULT_BANK_CODES, BANK_NAMES, DEFAULT_BANK_PROFILE, get_bank_profile,
)
from config.card_limits import (
    FEATURE_INTER_BANK, LIMIT_FIELDS, SPLIT_INTER_BANK, SPLIT_INTRA_BANK,
    available_split_modes, merge_limits,
)
from config import settings

router = Router()
logger = logging.getLogger(__name__)

@router.message(Command("cards"))
async def cmd_cards_dashboard(message: Message) -> None:
    if not _db:
        return await message.answer("❌ БД не підключена.")
    await _show_cards_dashboard(message.from_user.id, message)


@router.message(Command("checkup"))
async def cmd_checkup(message: Message) -> None:
    """
    Перевірка налаштувань тейкер-режимів — до того, як чекати алертів.

    Онбординг у цьому проєкті означав «увімкни й дивись, чи прийде»: усі
    перевірки жили всередині циклу сканера, а коли ордер не проходив, він
    просто зникав. Тут ті самі перевірки, але наперед і з поясненням.
    """
    if not _db:
        return await message.answer("❌ БД не підключена.")

    from core.engine.readiness import check_taker_readiness, render_checks
    from core.engine.scanner_helpers import _user_modes

    user = await _db.get_user_by_id(message.from_user.id)
    if not user:
        return await message.answer("❌ Не вдалося завантажити ваші налаштування.")

    modes = [m for m in _user_modes(user) if m in ("TAKER_BUY", "TAKER_SELL")]
    if not modes:
        return await message.answer(
            "🩺 <b>Перевірка налаштувань</b>\n\n"
            "Жоден тейкер-режим не увімкнено — перевіряти нічого.\n"
            "<i>Увімкнути можна через /mode.</i>"
        )

    blocks = []
    for mode in modes:
        checks = await check_taker_readiness(_db, user, mode)
        if checks:
            blocks.append(render_checks(checks, title=f"🩺 {mode}"))
        else:
            blocks.append(f"🩺 <b>{mode}</b>\n✅ Усе сходиться — перешкод не видно.")

    # Банки, які біржі віддають, а реєстр не знає. Це не про налаштування
    # користувача, а про повноту `config/banks.py` — але наслідок той самий:
    # частина ордерів не має шансів пройти, і причина не видна нізвідки.
    from core.engine import bank_discovery

    unknown = bank_discovery.render()
    if unknown:
        blocks.append(unknown)

    await message.answer("\n\n".join(blocks))


@router.message(Command("card_rejections"))
async def cmd_card_rejections(message: Message) -> None:
    """
    Чому картковий модуль відкидав ордери цього тижня.

    Раніше відкинутий ордер зникав без сліду, і «сканер нічого не знаходить»
    не відрізнялось від «ринку немає». Тут видно, що саме заважає — і чи є
    сенс у складних маршрутах, чи все впирається в одну просту причину.
    """
    if not _db:
        return await message.answer("❌ БД не підключена.")

    stats = await _db.get_rejection_stats(message.from_user.id, days=7)
    if not stats["total"] and not stats.get("observed_total"):
        return await message.answer(
            "📊 <b>Причини відмов за тиждень</b>\n\n"
            "Записів немає — картковий модуль нічого не відкидав "
            "(або сканер ще не працював у тейкер-режимі)."
        )

    from core.engine.rejection_codes import label

    def _row(row: dict, with_share: bool) -> str:
        head = f"• <b>{label(row['code'])}</b> — {row['hits']}"
        if with_share:
            head += f" ({row['share_pct']:.0f}%)"
        if row["avg_shortfall"]:
            avg = f"{row['avg_shortfall']:,.0f}".replace(",", " ")
            head += f"\n   └ у середньому бракувало {avg} ₴"
        if row["banks"]:
            head += f"\n   └ банки: {', '.join(row['banks'][:4])}"
        return head

    lines = [f"📊 <b>Причини відмов за тиждень</b>", f"<i>з {stats['since']}</i>"]

    if stats["total"]:
        lines.append(f"\n🚫 <b>Відхилено ордерів: {stats['total']}</b>")
        lines.extend(_row(r, True) for r in stats["codes"])

    # Спостереження — окремою секцією, бо це не відмови: ордер прийшов, але
    # меншим. Саме ці записи й показують, чого коштує стеля одного банку.
    observations = stats.get("observations") or []
    if observations:
        lines.append(
            f"\n📉 <b>Пройшли, але меншим обсягом: {stats['observed_total']}</b>"
        )
        lines.extend(_row(r, False) for r in observations)
        lines.append(
            "\n<i>Це не відмови — ці ордери ви отримали. Але саме тут стеля "
            "одного банку коштує обсягу: рядок «бракувало» показує, скільки "
            "втрачено через те, що гроші лежать у різних банках.</i>"
        )

    await message.answer("\n".join(lines))


async def _show_cards_dashboard(user_id: int, message_or_call) -> None:
    settings = await _db.get_user_card_settings(user_id)
    if not settings:
        conn = getattr(_db, "db", None) or getattr(_db, "_db", _db)
        await conn.execute("INSERT INTO user_card_settings (user_id) VALUES (?)", (user_id,))
        await conn.commit()
        settings = {"card_module_mode": "off"}

    cards = await _db.get_cards(user_id)

    text = (
        "💳 <b>Управління картками</b>\n\n"
        "Тут ви можете додати свої банківські картки або картки дропів для автоматичного спліту ордерів і контролю лімітів.\n\n"
        f"У вас додано карток: <b>{len(cards)}</b>\n"
    )

    kb = keyboards.cards_dashboard_kb(cards, settings.get("card_module_mode", "off"))

    if isinstance(message_or_call, Message):
        await message_or_call.answer(text, reply_markup=kb)
    else:
        await message_or_call.message.edit_text(text, reply_markup=kb)
        await message_or_call.answer()


@router.callback_query(F.data == "card:dashboard")
async def cb_cards_dashboard(call: CallbackQuery) -> None:
    await _show_cards_dashboard(call.from_user.id, call)


@router.callback_query(F.data == "menu:cards")
async def cb_menu_cards(call: CallbackQuery) -> None:
    await _show_cards_dashboard(call.from_user.id, call)


@router.callback_query(F.data == "menu:report")
async def cb_menu_report(call: CallbackQuery) -> None:
    await call.message.edit_text(
        "📊 <b>Звіт по картках</b>\n\nОберіть період для генерації звіту:",
        reply_markup=keyboards.report_period_kb()
    )
    await call.answer()


@router.callback_query(F.data == "card:diagnostics")
async def cb_card_diagnostics(call: CallbackQuery):
    if not _db:
        return await call.answer("❌ БД не підключена.", show_alert=True)
        
    user_id = call.from_user.id
    cards = await _db.get_cards(user_id)
    card_settings = await _db.get_user_card_settings(user_id)
    cold_card_limit = float(card_settings.get("cold_card_limit", 2000.0)) if card_settings else 2000.0
    
    total = len(cards)
    own = sum(1 for c in cards if c.get("is_own", 1))
    drops = total - own
    active = sum(1 for c in cards if c.get("status") == "active")
    frozen = sum(1 for c in cards if c.get("status") == "frozen_funds")
    
    import time
    cooldown = sum(1 for c in cards if c.get("cooldown_until", 0) > time.time())
    
    warnings = []
    
    for c in cards:
        card_id = c["id"]
        last_four = c["last_four"]
        bank = c["bank_name"].capitalize()
        label = c["label"]
        bal = c["balance"]
        
        if bal <= 0:
            warnings.append(f"⚠️ <b>{bank} *{last_four}</b> ({label}): Баланс рівний 0 ₴. Буде пропущено в BUY.")
            
        if c.get("status") == "active":
            is_warm = bool(c.get("is_warmed_up", 0))
            if not is_warm and cold_card_limit > 0:
                tx_count_total, last_tx_ts = await _db.get_card_warmth_stats(card_id)
                warmup_limit = _db.get_card_warmup_limit(c, tx_count_total, last_tx_ts, time.time(), cold_card_limit)
                if warmup_limit is not None:
                    warnings.append(f"🌱 <b>{bank} *{last_four}</b> ({label}): Непрогріта картка. Внесок в капітал обмежений до <b>{warmup_limit:,.0f} ₴</b> (транзакцій: {tx_count_total}).")

        if c["status"] == "frozen_funds":
            warnings.append(f"❄️ <b>{bank} *{last_four}</b> ({label}): Заморожена. Кошти не використовуються.")
            
        if c.get("cooldown_until", 0) > time.time():
            remaining_mins = int((c["cooldown_until"] - time.time()) / 60)
            warnings.append(f"⏳ <b>{bank} *{last_four}</b> ({label}): Кулдаун ще {remaining_mins} хв.")
            
        if c["bank_name"].lower() == "monobank":
            mono_settings = await _db.get_card_mono_settings(card_id)
            if not mono_settings or not mono_settings.get("webhook_secret"):
                warnings.append(f"🐈 <b>{bank} *{last_four}</b> ({label}): Не налаштовано Webhook. Авто-підтвердження вимкнено.")
            elif not mono_settings.get("mono_account_id"):
                warnings.append(f"⚠️ <b>{bank} *{last_four}</b> ({label}): Webhook підключено, але не прив'язано до рахунку в API.")
                
        limits = await _db.get_card_effective_limits(card_id, user_id, c["bank_name"])
        daily_in_max = limits["daily_in_max"]
        daily_out_max = limits["daily_out_max"]
        
        used_in = await _db.get_rolling_used(card_id, "in", 24)
        used_out = await _db.get_rolling_used(card_id, "out", 24)
        
        if used_in >= daily_in_max * 0.8:
            pct = (used_in / daily_in_max) * 100
            warnings.append(f"🚨 <b>{bank} *{last_four}</b> ({label}): Використано {pct:.0f}% добового ліміту IN ({used_in:.0f}/{daily_in_max:.0f} ₴).")
        if used_out >= daily_out_max * 0.8:
            pct = (used_out / daily_out_max) * 100
            warnings.append(f"🚨 <b>{bank} *{last_four}</b> ({label}): Використано {pct:.0f}% добового ліміту OUT ({used_out:.0f}/{daily_out_max:.0f} ₴).")

    from bot.card_notifier import _card_matching_cache
    cache_sessions = len(_card_matching_cache)
    
    warnings_text = "\n".join(warnings) if warnings else "🟢 <b>Проблем або зауважень не виявлено.</b>"
    
    text = (
        "🔍 <b>ARBIX QUANTUM | Діагностика карток</b>\n\n"
        f"📊 <b>Загальна статистика:</b>\n"
        f"├ Всього карток: <b>{total}</b> (Власних: <b>{own}</b>, Дропів: <b>{drops}</b>)\n"
        f"├ Активних: <b>{active}</b> 🟢\n"
        f"├ Заморожених: <b>{frozen}</b> ❄️\n"
        f"└ В кулдауні: <b>{cooldown}</b> ⏳\n\n"
        f"🧠 <b>Кеш автопідбору карт:</b>\n"
        f"└ Активних сесій підбору: <b>{cache_sessions}</b>\n\n"
        f"⚠️ <b>Проблеми та попередження:</b>\n"
        f"{warnings_text}"
    )
    
    builder = InlineKeyboardBuilder()
    builder.row(InlineKeyboardButton(text="🔄 Оновити", callback_data="card:diagnostics"))
    builder.row(InlineKeyboardButton(text="🔙 Назад до карток", callback_data="card:dashboard"))
    
    with suppress(TelegramBadRequest):
        await call.message.edit_text(text, reply_markup=builder.as_markup(), parse_mode="HTML")
    await call.answer()


@router.callback_query(F.data == "card:toggle_module")
async def cb_card_toggle_module(call: CallbackQuery) -> None:
    if not _db:
        return await call.answer("❌ БД не підключена.")
    settings = await _db.get_user_card_settings(call.from_user.id)
    new_mode = "full" if settings.get("card_module_mode") == "off" else "off"
    conn = getattr(_db, "db", None) or getattr(_db, "_db", _db)
    await conn.execute("UPDATE user_card_settings SET card_module_mode=? WHERE user_id=?",
                       (new_mode, call.from_user.id))
    await conn.commit()
    await _show_cards_dashboard(call.from_user.id, call)


@router.callback_query(F.data == "card:add_start")
async def cb_card_add_start(call: CallbackQuery, state: FSMContext) -> None:
    await state.clear()
    await call.message.edit_text("Виберіть банк для нової картки:", reply_markup=keyboards.card_banks_kb())
    await state.set_state(CardAddStates.waiting_bank)
    await call.answer()


@router.callback_query(CardAddStates.waiting_bank, F.data.startswith("card_add:bank:"))
async def cb_card_add_bank(call: CallbackQuery, state: FSMContext) -> None:
    bank = call.data.split(":")[2]
    await state.update_data(bank=bank)
    await call.message.edit_text(f"Вибрано банк: <b>{bank.capitalize()}</b>\n\nВведіть повний номер картки (16 цифр):")
    await state.set_state(CardAddStates.waiting_card_number)
    await call.answer()


@router.message(CardAddStates.waiting_card_number)
async def process_card_add_number(message: Message, state: FSMContext) -> None:
    card_number = message.text.strip().replace(" ", "")
    if len(card_number) != 16 or not card_number.isdigit():
        return await message.answer("❌ Номер картки має складатися з 16 цифр. Спробуйте ще раз:")
    await state.update_data(card_number=card_number, last_four=card_number[-4:])
    await message.answer("Введіть поточний баланс картки (грн):")
    await state.set_state(CardAddStates.waiting_balance)


@router.message(CardAddStates.waiting_balance)
async def process_card_add_balance(message: Message, state: FSMContext) -> None:
    try:
        balance = float(message.text.strip().replace(',', '.'))
    except ValueError:
        return await message.answer("❌ Некоректний формат числа. Спробуйте ще раз:")
    await state.update_data(balance=balance)
    await message.answer("Введіть мітку (Label) для картки (напр. 'Власна', 'Дроп Іван'):")
    await state.set_state(CardAddStates.waiting_label)


@router.message(CardAddStates.waiting_label)
async def process_card_add_label(message: Message, state: FSMContext) -> None:
    label = message.text.strip()
    await state.update_data(label=label)
    await message.answer("Ця картка належить вам (Власна) чи дропу?", reply_markup=keyboards.card_is_own_kb())
    await state.set_state(CardAddStates.waiting_is_own)


@router.callback_query(CardAddStates.waiting_is_own, F.data.startswith("card_add:is_own:"))
async def cb_card_add_is_own(call: CallbackQuery, state: FSMContext) -> None:
    is_own = int(call.data.split(":")[2])
    data = await state.get_data()

    import uuid
    card_id = str(uuid.uuid4())
    card_data = {
        "id": card_id,
        "owner_id": call.fromuser.id if hasattr(call, 'fromuser') else call.from_user.id,
        "bank_name": data["bank"],
        "card_number": data["card_number"],
        "last_four": data["last_four"],
        "label": data["label"],
        "is_own": is_own,
        "balance": data["balance"],
        "status": "active"
    }

    if _db:
        await _db.add_card(card_data)

        # Note: We no longer auto-map Mono here using a global token.
        # User must set up webhook per card.
        pass

    await state.clear()
    await call.message.edit_text("✅ Картку успішно додано!")
    await _show_cards_dashboard(call.from_user.id, call.message)
    await call.answer()


@router.callback_query(F.data == "card:cancel")
async def cb_card_cancel(call: CallbackQuery, state: FSMContext) -> None:
    await state.clear()
    await _show_cards_dashboard(call.from_user.id, call)


@router.callback_query(F.data.startswith("card:view:"))
async def cb_card_view(call: CallbackQuery) -> None:
    card_id = call.data.split(":")[2]
    cards = await _db.get_cards(call.from_user.id)
    card = next((c for c in cards if c["id"] == card_id), None)
    if not card:
        return await call.answer("❌ Картку не знайдено", show_alert=True)

    import time
    used_daily_in = await _db.get_rolling_used(card_id, "in", 24)
    used_daily_out = await _db.get_rolling_used(card_id, "out", 24)
    tx_count = await _db.get_card_transactions_count(card_id, 24)

    limits = await _db.get_card_effective_limits(card_id, call.from_user.id, card['bank_name'])
    limit_daily_in = limits["daily_in_max"]
    limit_daily_out = limits["daily_out_max"]

    rem_in = max(0, limit_daily_in - used_daily_in)
    rem_out = max(0, limit_daily_out - used_daily_out)

    is_custom = card.get("is_custom_limits", 0)
    limits_label = "🟢 Локальні" if is_custom else "⚪ Глобальні"

    is_warmed_up = bool(card.get("is_warmed_up", 0))
    warmth_status_text = "🔥 Прогріта" if is_warmed_up else "⚪ Не прогріта"

    import datetime
    cooldown_str = "Немає"
    if card["cooldown_until"] > time.time():
        dt = datetime.datetime.fromtimestamp(card["cooldown_until"])
        cooldown_str = dt.strftime("%Y-%m-%d %H:%M:%S")

    text = (
        f"💳 <b>Картка:</b> {card['bank_name'].capitalize()} {card['last_four']}\n"
        f"🏷 <b>Мітка:</b> {card['label']}\n"
        f"👤 <b>Тип:</b> {'Власна' if card['is_own'] else 'Дроп'}\n"
        f"🌡️ <b>Прогрів:</b> {warmth_status_text}\n"
        f"💰 <b>Баланс:</b> {card['balance']:.2f} ₴\n\n"
        f"📊 <b>Статистика за 24г:</b>\n"
        f"📥 Надходження: {used_daily_in:.0f} ₴ (Залишок: {rem_in:.0f} ₴)\n"
        f"📤 Витрати: {used_daily_out:.0f} ₴ (Залишок: {rem_out:.0f} ₴)\n"
        f"🔄 Транзакцій: {tx_count}\n\n"
        f"📌 <b>Статус:</b> {card['status']}\n"
        f"⚙️ <b>Ліміти:</b> {limits_label}\n"
        f"⏳ <b>Cooldown до:</b> {cooldown_str}"
    )

    # Якщо викликано з FakeCall (message), то треба відповісти або відредагувати існуюче
    if hasattr(call, "message") and hasattr(call.message, "edit_text"):
        await call.message.edit_text(text,
                                     reply_markup=keyboards.card_details_kb(card_id, card["status"], card["bank_name"], is_warmed_up))
    else:
        # Для фейкового call
        await call.message.answer(text,
                                  reply_markup=keyboards.card_details_kb(card_id, card["status"], card["bank_name"], is_warmed_up))

    if hasattr(call, "answer"):
        await call.answer()


@router.callback_query(F.data.startswith("card:toggle_warmth:"))
async def cb_card_toggle_warmth(call: CallbackQuery) -> None:
    card_id = call.data.split(":")[2]
    cards = await _db.get_cards(call.from_user.id)
    card = next((c for c in cards if c["id"] == card_id), None)
    if not card:
        return await call.answer("❌ Картку не знайдено", show_alert=True)

    new_warmth = 0 if card.get("is_warmed_up", 0) else 1
    await _db.update_card(card_id, {"is_warmed_up": new_warmth})

    status_str = "Прогріта" if new_warmth else "Не прогріта"
    await call.answer(f"Картка тепер {status_str}")
    # Refresh view
    call.data = f"card:view:{card_id}"
    await cb_card_view(call)


@router.callback_query(F.data.startswith("card:toggle:"))
async def cb_card_toggle(call: CallbackQuery) -> None:
    card_id = call.data.split(":")[2]
    cards = await _db.get_cards(call.from_user.id)
    card = next((c for c in cards if c["id"] == card_id), None)
    if not card:
        return await call.answer("❌ Картку не знайдено", show_alert=True)

    new_status = "frozen_funds" if card["status"] == "active" else "active"
    await _db.update_card(card_id, {"status": new_status})

    await call.answer(f"Статус змінено на {new_status}")
    # Refresh view
    call.data = f"card:view:{card_id}"
    await cb_card_view(call)


# ── D3: Редагування полів картки (label / note / category) ──────────────

@router.callback_query(F.data.startswith("card:edit:label:"))
async def cb_card_edit_label(call: CallbackQuery, state: FSMContext) -> None:
    card_id = call.data.split(":")[3]
    await state.update_data(edit_card_id=card_id)
    await call.message.edit_text(
        "🏷 Введіть нову мітку (Label) для картки:",
        reply_markup=InlineKeyboardMarkup(
            inline_keyboard=[[InlineKeyboardButton(text="🔙 Скасувати", callback_data=f"card:view:{card_id}")]])
    )
    await state.set_state(CardEditStates.waiting_label)
    await call.answer()


@router.message(CardEditStates.waiting_label)
async def process_card_edit_label(message: Message, state: FSMContext) -> None:
    data = await state.get_data()
    card_id = data["edit_card_id"]
    new_label = message.text.strip()
    await _db.update_card(card_id, {"label": new_label})
    await state.clear()
    await message.answer(f"✅ Мітку змінено на: <b>{new_label}</b>")

    # Refresh card view
    class FakeCall:
        data = f"card:view:{card_id}"
        from_user = message.from_user

    fc = FakeCall()
    fc.message = message

    async def noop(*a, **k): pass

    fc.answer = noop
    await cb_card_view(fc)


@router.callback_query(F.data.startswith("card:edit:note:"))
async def cb_card_edit_note(call: CallbackQuery, state: FSMContext) -> None:
    card_id = call.data.split(":")[3]
    await state.update_data(edit_card_id=card_id)
    await call.message.edit_text(
        "📝 Введіть нову нотатку (Note) для картки:",
        reply_markup=InlineKeyboardMarkup(
            inline_keyboard=[[InlineKeyboardButton(text="🔙 Скасувати", callback_data=f"card:view:{card_id}")]])
    )
    await state.set_state(CardEditStates.waiting_note)
    await call.answer()


@router.message(CardEditStates.waiting_note)
async def process_card_edit_note(message: Message, state: FSMContext) -> None:
    data = await state.get_data()
    card_id = data["edit_card_id"]
    new_note = message.text.strip()
    await _db.update_card(card_id, {"note": new_note})
    await state.clear()
    await message.answer(f"✅ Нотатку змінено.")

    class FakeCall:
        data = f"card:view:{card_id}"
        from_user = message.from_user

    fc = FakeCall()
    fc.message = message

    async def noop(*a, **k): pass

    fc.answer = noop
    await cb_card_view(fc)


@router.callback_query(F.data.startswith("card:edit:category:"))
async def cb_card_edit_category(call: CallbackQuery) -> None:
    card_id = call.data.split(":")[3]
    await call.message.edit_text(
        "👥 Оберіть категорію картки:",
        reply_markup=keyboards.card_category_kb(card_id)
    )
    await call.answer()


@router.callback_query(F.data.startswith("card:set_cat:"))
async def cb_card_set_category(call: CallbackQuery) -> None:
    parts = call.data.split(":")
    card_id = parts[2]
    category = parts[3]
    is_own = 1 if category == "self" else 0
    await _db.update_card(card_id, {"category": category, "is_own": is_own})
    await call.answer(f"✅ Категорію змінено")
    call.data = f"card:view:{card_id}"
    await cb_card_view(call)


@router.callback_query(F.data.startswith("card:delete:"))
async def cb_card_delete(call: CallbackQuery) -> None:
    card_id = call.data.split(":")[2]
    conn = getattr(_db, "db", None) or getattr(_db, "_db", _db)
    await conn.execute("DELETE FROM cards WHERE id=?", (card_id,))
    await conn.commit()
    await call.answer("🗑 Картку видалено")
    await _show_cards_dashboard(call.from_user.id, call)


@router.callback_query(F.data.startswith("card:update_bal:"))
async def cb_card_update_bal_start(call: CallbackQuery, state: FSMContext) -> None:
    card_id = call.data.split(":")[2]
    cards = await _db.get_cards(call.from_user.id)
    card = next((c for c in cards if c["id"] == card_id), None)
    
    if card and card.get("bank_name", "").lower() == "monobank" and card.get("mono_account_id") and card.get("mono_x_token_encrypted"):
        await call.answer("🔄 Запит до серверів Monobank API...")
        new_balance = await _db.force_refresh_mono_balance(card_id)
        if new_balance is not None:
            await call.answer(f"✅ Баланс успішно оновлено через API: {new_balance:,.2f} ₴", show_alert=True)
            # Rerender card details
            call.data = f"card:view:{card_id}"
            await cb_card_view(call)
            return
        else:
            await call.answer("⚠️ Не вдалося оновити через API. Переходимо до ручного введення.", show_alert=True)

    await state.update_data(card_id=card_id)
    await call.message.edit_text(
        "Введіть точний актуальний баланс картки (грн):\n\n"
        "<i>Ця дія просто оновить баланс у системі без створення транзакції.</i>",
        reply_markup=InlineKeyboardMarkup(
            inline_keyboard=[[InlineKeyboardButton(text="🔙 Скасувати", callback_data=f"card:view:{card_id}")]])
    )
    await state.set_state(CardUpdateStates.waiting_true_balance)


@router.message(CardUpdateStates.waiting_true_balance)
async def process_card_update_bal(message: Message, state: FSMContext) -> None:
    try:
        balance = float(message.text.strip().replace(',', '.'))
    except ValueError:
        return await message.answer("❌ Некоректний формат числа.")

    data = await state.get_data()
    card_id = data["card_id"]

    await _db.update_card_balance(card_id, balance)

    await state.clear()
    await message.answer("✅ Баланс успішно оновлено!")

    class FakeCall:
        def __init__(self, message, card_id: str):
            self.data = f"card:view:{card_id}"
            self.from_user = message.from_user
            self.message = message

        async def answer(self, *args, **kwargs):
            pass

    # Бойовий виклик (message тепер летить у конструктор і скоуп не ламається):
    await cb_card_view(FakeCall(message, card_id))


@router.callback_query(F.data.startswith("card_match:confirm:"))
async def cb_card_match_confirm(call: CallbackQuery):
    cache_key = call.data.split(":", 2)[2]
    from bot.card_notifier import _card_matching_cache

    if cache_key not in _card_matching_cache:
        return await call.answer("❌ Дані застаріли", show_alert=True)

    data = _card_matching_cache[cache_key]
    cards = data.get("found_cards", [])
    if not cards:
        return await call.answer("❌ Картки не знайдено", show_alert=True)

    amount_per_card = data["target_amount"] / len(cards)
    for c in cards:
        await _db.reserve_card_amount(c["id"], amount_per_card, data["order_id"])

    await call.message.edit_text(
        f"✅ Успішно зарезервовано {data['target_amount']:.0f} ₴ на {len(cards)} картках для ордеру {data['order_id'][:8]}")
    del _card_matching_cache[cache_key]
    await call.answer()


@router.callback_query(F.data.startswith("card_match:other:"))
async def cb_card_match_other(call: CallbackQuery):
    cache_key = call.data.split(":", 2)[2]
    from bot.card_notifier import _card_matching_cache

    if cache_key not in _card_matching_cache:
        return await call.answer("❌ Дані застаріли", show_alert=True)

    data = _card_matching_cache[cache_key]
    cards = data.get("found_cards", [])

    for c in cards:
        if c["id"] not in data["excluded_cards"]:
            data["excluded_cards"].append(c["id"])

    if _notifier and hasattr(_notifier, "card_notifier"):
        await call.answer("🔄 Шукаю інший варіант...")
        await _notifier.card_notifier.send_card_recommendation(
            chat_id=call.from_user.id,
            target_amount=data["target_amount"],
            direction=data["direction"],
            bank=data["bank"],
            order_id=data["order_id"],
            cache_key=cache_key,
            message_id=call.message.message_id
        )
    else:
        await call.answer("❌ Модуль не підключено")


@router.callback_query(F.data.startswith("card_match:cancel:"))
async def cb_card_match_cancel(call: CallbackQuery):
    cache_key = call.data.split(":", 2)[2]
    from bot.card_notifier import _card_matching_cache
    if cache_key in _card_matching_cache:
        del _card_matching_cache[cache_key]
    await call.message.edit_text("❌ Підбір картки скасовано.")
    await call.answer()


@router.callback_query(F.data.startswith("card:mono_setup:"))
async def cb_card_mono_setup(call: CallbackQuery, state: FSMContext):
    card_id = call.data.split(":")[2]
    settings = await _db.get_card_mono_settings(card_id)
    if settings and settings.get("webhook_secret"):
        secret = settings["webhook_secret"]
        await call.message.edit_text(
            f"🐈 <b>Налаштування Monobank Webhook</b>\n\n"
            f"Токен вже підключений для цієї картки.\n"
            f"Webhook URL для Monobank:\n"
            f"<code>https://&lt;your-domain&gt;/api/v1/webhooks/mono/card/{card_id}/{secret}</code>\n\n"
            f"Щоб змінити токен, відправте новий X-Token нижче, або /cancel."
        )
    else:
        await call.message.edit_text(
            "🐈 <b>Інтеграція Monobank</b>\n\n"
            "Щоб налаштувати автоматичне підтвердження транзакцій для цієї картки, введіть X-Token.\n"
            "Ви можете отримати його тут: https://api.monobank.ua/ \n\n"
            "<i>Токен буде зашифровано.</i>"
        )
    await state.update_data(setup_card_id=card_id)
    await state.set_state(MonoStates.waiting_token)
    await call.answer()


@router.message(MonoStates.waiting_token)
async def process_mono_token(message: Message, state: FSMContext):
    token = message.text.strip()
    data = await state.get_data()
    card_id = data.get("setup_card_id")
    if not card_id:
        return await message.answer("❌ Помилка: картка не знайдена. Спробуйте ще раз через меню карток.")

    from infrastructure.api.mono_client import MonoApiClient
    from core.security.crypto_utils import CryptoUtils
    import secrets

    msg = await message.answer("⏳ Перевіряю токен...")

    client = MonoApiClient(token)
    info = await client.get_client_info()
    if not info:
        return await msg.edit_text("❌ Помилка: невірний токен або збій API Mono.")

    secret = secrets.token_urlsafe(16)
    encrypted_token = CryptoUtils.encrypt(token)

    await _db.save_card_mono_settings(card_id, encrypted_token, secret)

    # Auto-map existing cards
    accounts = info.get("accounts", [])

    # We only map THIS specific card now
    cards = await _db.get_cards(message.from_user.id)
    card_obj = next((c for c in cards if c["id"] == card_id), None)
    mapped = False

    if card_obj:
        last_four = card_obj["last_four"]
        for acc in accounts:
            pan = acc.get("maskedPan", [])
            if pan and len(pan) > 0 and pan[0].endswith(last_four):
                await _db.update_card_mono_account(card_id, acc["id"])

                # Автоматично підтягуємо актуальний баланс з АПІ
                real_balance = acc.get("balance", 0) / 100.0
                conn = getattr(_db, "db", None) or getattr(_db, "_db", _db)
                await conn.execute("UPDATE cards SET balance=? WHERE id=?", (real_balance, card_id))
                await conn.commit()

                mapped = True
                break

    await state.clear()
    await message.delete()  # hide token

    webhook_registered = False
    webhook_url = ""
    if getattr(settings, "public_url", None):
        webhook_url = f"{settings.public_url.rstrip('/')}/api/v1/webhooks/mono/card/{card_id}/{secret}"
        try:
            webhook_registered = await client.setup_webhook(webhook_url)
            if webhook_registered:
                logger.info(f"Successfully registered Monobank webhook for card {card_id}: {webhook_url}")
            else:
                logger.error(f"Monobank API refused webhook registration for card {card_id}")
        except Exception as e:
            logger.error(f"Failed to register Monobank webhook for card {card_id}: {e}")

    status_text = "✅ <b>Monobank успішно підключено!</b>\n"
    if mapped:
        status_text += "Картку знайдено в API та успішно прив'язано.\n\n"
    else:
        status_text += "⚠️ Увага: Картку з такими останніми цифрами не знайдено в цьому токені.\n\n"

    if getattr(settings, "public_url", None):
        if webhook_registered:
            status_text += f"🔔 <b>Вебхуки підключено авто-запитом!</b>\nТранзакції оновлюватимуться миттєво.\nURL: <code>{webhook_url}</code>\n\n"
        else:
            status_text += f"⚠️ <b>Помилка реєстрації вебхуку в API Моно.</b>\nУвімкнено фоновий оновлювач (Fallback Sync Worker).\n\n"
    else:
        status_text += (
            f"ℹ️ <code>PUBLIC_URL</code> не налаштований у <code>.env</code>.\n"
            f"Оновлення балансу працюватиме автоматично у фоновому режимі (Fallback Sync Worker) кожні 90 секунд.\n\n"
            f"Якщо бажаєте миттєві вебхуки, додайте <code>PUBLIC_URL</code> та зареєструйте URL:\n"
            f"<code>https://&lt;your-domain&gt;/api/v1/webhooks/mono/card/{card_id}/{secret}</code>\n\n"
        )

    await msg.edit_text(status_text)


# ═══════════════════════════════════════════════════════════════════════════════
# 🐈 Monobank Tracker Settings UI
# ═══════════════════════════════════════════════════════════════════════════════

@router.callback_query(F.data.startswith("card:mono_tracker:"))
async def cb_mono_tracker_menu(call: CallbackQuery):
    card_id = call.data.split(":")[2]
    await _show_mono_tracker_menu(call, card_id)


async def _show_mono_tracker_menu(call: CallbackQuery, card_id: str):
    import json
    cards = await _db.get_cards(call.from_user.id)
    card = next((c for c in cards if c["id"] == card_id), None)
    if not card:
        return await call.answer("❌ Картку не знайдено", show_alert=True)

    mono = await _db.get_card_mono_settings(card_id)
    enabled = int(mono.get("tracker_enabled", 1))
    mode = mono.get("tracker_mode", "INCOME")
    fields_raw = mono.get("tracker_fields") or '{}'
    try:
        fields = json.loads(fields_raw) if isinstance(fields_raw, str) else fields_raw
    except Exception:
        fields = {"amount": 1, "sender": 1, "comment": 1, "time": 1, "card": 1, "balance": 1, "p2p": 1}

    status_str = "🟢 УВІМКНЕНО" if enabled else "🔴 ВИМКНЕНО"
    mode_str = "🟢 Тільки зарахування (+)" if mode == "INCOME" else "🔄 Всі транзакції (+/-)"

    card_name = card.get("label") or f"{card.get('bank_name', 'Mono').capitalize()} *{card.get('last_four', '')}"

    text = (
        f"🐈 <b>Налаштування Трекера коштів Monobank</b>\n"
        f"Картка: <b>{card_name}</b>\n\n"
        f"🔔 <b>Трекер коштів:</b> {status_str}\n"
        f"🔄 <b>Режим сповіщень:</b> {mode_str}\n\n"
        f"⚙️ <b>Поля у Telegram-повідомленні:</b>\n"
        f" • 💰 Сума: {'✅' if fields.get('amount') else '❌'}\n"
        f" • 👤 Відправник / Опис: {'✅' if fields.get('sender') else '❌'}\n"
        f" • 💬 Коментар: {'✅' if fields.get('comment') else '❌'}\n"
        f" • ⏰ Час: {'✅' if fields.get('time') else '❌'}\n"
        f" • 💳 Назва картки: {'✅' if fields.get('card') else '❌'}\n"
        f" • 📊 Залишок: {'✅' if fields.get('balance') else '❌'}\n"
        f" • 🔗 P2P Ордер: {'✅' if fields.get('p2p') else '❌'}\n"
    )

    builder = InlineKeyboardBuilder()
    builder.row(InlineKeyboardButton(text=f"🔔 Трекер: {'УВІМКНЕНО ✅' if enabled else 'ВИМКНЕНО ❌'}", callback_data=f"card:mono_tr_toggle:{card_id}"))
    builder.row(InlineKeyboardButton(text=f"🔄 Режим: {'🟢 Тільки (+)' if mode == 'INCOME' else '🔄 Всі (+/-)'}", callback_data=f"card:mono_tr_mode:{card_id}"))
    builder.row(
        InlineKeyboardButton(text=f"💰 Сума {'✅' if fields.get('amount') else '❌'}", callback_data=f"card:mono_tr_field:{card_id}:amount"),
        InlineKeyboardButton(text=f"👤 Відправник {'✅' if fields.get('sender') else '❌'}", callback_data=f"card:mono_tr_field:{card_id}:sender"),
    )
    builder.row(
        InlineKeyboardButton(text=f"💬 Коментар {'✅' if fields.get('comment') else '❌'}", callback_data=f"card:mono_tr_field:{card_id}:comment"),
        InlineKeyboardButton(text=f"⏰ Час {'✅' if fields.get('time') else '❌'}", callback_data=f"card:mono_tr_field:{card_id}:time"),
    )
    builder.row(
        InlineKeyboardButton(text=f"💳 Картка {'✅' if fields.get('card') else '❌'}", callback_data=f"card:mono_tr_field:{card_id}:card"),
        InlineKeyboardButton(text=f"📊 Залишок {'✅' if fields.get('balance') else '❌'}", callback_data=f"card:mono_tr_field:{card_id}:balance"),
    )
    builder.row(InlineKeyboardButton(text=f"🔗 P2P Ордер {'✅' if fields.get('p2p') else '❌'}", callback_data=f"card:mono_tr_field:{card_id}:p2p"))
    builder.row(InlineKeyboardButton(text="🔙 Назад до картки", callback_data=f"card:view:{card_id}"))

    await call.message.edit_text(text, reply_markup=builder.as_markup())
    if hasattr(call, "answer"):
        await call.answer()


@router.callback_query(F.data.startswith("card:mono_tr_toggle:"))
async def cb_mono_tr_toggle(call: CallbackQuery):
    card_id = call.data.split(":")[2]
    mono = await _db.get_card_mono_settings(card_id)
    enabled = int(mono.get("tracker_enabled", 1))
    new_enabled = 0 if enabled else 1
    await _db.update_mono_tracker_config(card_id, enabled=new_enabled)
    await _show_mono_tracker_menu(call, card_id)


@router.callback_query(F.data.startswith("card:mono_tr_mode:"))
async def cb_mono_tr_mode(call: CallbackQuery):
    card_id = call.data.split(":")[2]
    mono = await _db.get_card_mono_settings(card_id)
    mode = mono.get("tracker_mode", "INCOME")
    new_mode = "ALL" if mode == "INCOME" else "INCOME"
    await _db.update_mono_tracker_config(card_id, mode=new_mode)
    await _show_mono_tracker_menu(call, card_id)


@router.callback_query(F.data.startswith("card:mono_tr_field:"))
async def cb_mono_tr_field(call: CallbackQuery):
    import json
    parts = call.data.split(":")
    card_id = parts[2]
    field_name = parts[3]
    mono = await _db.get_card_mono_settings(card_id)
    fields_raw = mono.get("tracker_fields") or '{}'
    try:
        fields = json.loads(fields_raw) if isinstance(fields_raw, str) else fields_raw
    except Exception:
        fields = {"amount": 1, "sender": 1, "comment": 1, "time": 1, "card": 1, "balance": 1, "p2p": 1}

    fields[field_name] = 0 if fields.get(field_name, 1) else 1
    await _db.update_mono_tracker_config(card_id, fields=fields)
    await _show_mono_tracker_menu(call, card_id)


# ═══════════════════════════════════════════════════════════════════════════════
# /report — Звіт по картках
# ═══════════════════════════════════════════════════════════════════════════════

@router.message(Command("report"))
async def cmd_report(message: Message) -> None:
    """Відображає вибір періоду для звіту по картках."""
    if not _db:
        return await message.answer("❌ БД не підключена.")
    await message.answer(
        "📊 <b>Звіт по картках</b>\n\nОберіть період для формування звіту:",
        reply_markup=keyboards.report_period_kb()
    )


@router.callback_query(F.data.startswith("report:period:"))
async def cb_report_period(call: CallbackQuery) -> None:
    """Генерує повний звіт по кожній картці за вибраний період."""
    if not _db:
        return await call.answer("❌ БД не підключена.", show_alert=True)

    hours = int(call.data.split(":")[2])
    period_label = {24: "24 години", 168: "7 днів", 720: "30 днів"}.get(hours, f"{hours}г")
    user_id = call.from_user.id

    cards = await _db.get_cards(user_id)
    if not cards:
        return await call.message.edit_text("У вас ще немає доданих карток. Скористайтесь /cards для додавання.")

    import time
    cutoff = time.time() - (hours * 3600)

    # Агрегація по кожній картці
    total_work_in = 0.0
    total_work_out = 0.0
    total_personal_in = 0.0
    total_personal_out = 0.0
    total_tx = 0
    card_blocks = []

    for card in cards:
        card_id = card["id"]
        conn = getattr(_db, "_db", _db)

        async with conn.execute(
                """
                SELECT COUNT(*)                                                                      as tx_count,
                       SUM(CASE WHEN direction = 'in' AND type = 'work' THEN amount ELSE 0 END)      as work_in,
                       SUM(CASE WHEN direction = 'out' AND type = 'work' THEN amount ELSE 0 END)     as work_out,
                       SUM(CASE WHEN direction = 'in' AND type = 'personal' THEN amount ELSE 0 END)  as pers_in,
                       SUM(CASE WHEN direction = 'out' AND type = 'personal' THEN amount ELSE 0 END) as pers_out
                FROM card_transactions
                WHERE card_id = ? AND timestamp > ?
                """,
                (card_id, cutoff)
        ) as cur:
            row = await cur.fetchone()
            s = dict(row) if row else {}

        tx_count = s.get("tx_count", 0) or 0
        work_in = s.get("work_in", 0.0) or 0.0
        work_out = s.get("work_out", 0.0) or 0.0
        pers_in = s.get("pers_in", 0.0) or 0.0
        pers_out = s.get("pers_out", 0.0) or 0.0

        total_work_in += work_in
        total_work_out += work_out
        total_personal_in += pers_in
        total_personal_out += pers_out
        total_tx += tx_count

        # Ліміти. Беремо ефективні, а не рядок user_bank_limits: у ньому
        # тепер NULL там, де користувач нічого не задавав, і звіт показував би
        # порожнечу замість дефолтів довідника.
        limit_line = ""
        if hours <= 24:
            limits = await _db.get_card_effective_limits(card_id, user_id, card["bank_name"])
            used_in_24 = await _db.get_rolling_used(card_id, "in", 24)
            used_out_24 = await _db.get_rolling_used(card_id, "out", 24)
            daily_in_max = limits["daily_in_max"]
            daily_out_max = limits["daily_out_max"]
            tx_today = await _db.get_card_transactions_count(card_id, 24)
            max_tx = limits["max_tx_per_day"]
            limit_line = (
                f"\n   📏 Ліміти: IN {used_in_24:.0f}/{daily_in_max:.0f} | "
                f"OUT {used_out_24:.0f}/{daily_out_max:.0f} | "
                f"TX {tx_today}/{max_tx}"
            )

        icon = "🟢" if card["status"] == "active" else ("❄️" if card["status"] == "frozen_funds" else "🔴")
        drop = " (Дроп)" if not card.get("is_own", 1) else ""
        work_total = work_in + work_out
        pers_total = pers_in + pers_out

        block = (
            f"{icon} <b>{card['bank_name'].capitalize()} •{card['last_four']}</b>{drop}\n"
            f"   💰 Баланс: {card['balance']:.0f} ₴\n"
            f"   🔄 TX: {tx_count} | Робоча: {work_total:.0f} ₴ | Особиста: {pers_total:.0f} ₴\n"
            f"   📥 Work IN: {work_in:.0f} ₴ | 📤 Work OUT: {work_out:.0f} ₴"
            f"{limit_line}"
        )
        card_blocks.append(block)

    total_work = total_work_in + total_work_out
    total_pers = total_personal_in + total_personal_out

    header = (
        f"📊 <b>Звіт по картках за {period_label}</b>\n"
        f"{'━' * 30}\n\n"
        f"💼 Робочий оборот: <b>{total_work:.0f} ₴</b> (IN: {total_work_in:.0f} | OUT: {total_work_out:.0f})\n"
        f"🏠 Особистий: <b>{total_pers:.0f} ₴</b>\n"
        f"🔄 Транзакцій: <b>{total_tx}</b>\n"
        f"{'━' * 30}\n\n"
    )

    text = header + "\n\n".join(card_blocks)

    await call.message.edit_text(text)
    await call.answer()


# ═══════════════════════════════════════════════════════════════════════════════
# D6: /set_bank_limits — Налаштування лімітів банку
# ═══════════════════════════════════════════════════════════════════════════════

@router.message(Command("set_bank_limits"))
async def cmd_set_bank_limits(message: Message) -> None:
    await _show_bank_limits_menu(message)


@router.callback_query(F.data == "menu:bank_limits")
async def cb_menu_bank_limits(call: CallbackQuery) -> None:
    await _show_bank_limits_menu(call.message)
    await call.answer()


async def _show_bank_limits_menu(message: Message):
    if not _db:
        return await message.answer("❌ БД не підключена.")

    text = "⚙️ <b>Налаштування лімітів банку</b>\n\nОберіть банк:"
    reply_markup = keyboards.bank_limits_bank_kb()

    if hasattr(message, "edit_text"):
        try:
            await message.edit_text(text, reply_markup=reply_markup)
        except:
            await message.answer(text, reply_markup=reply_markup)
    else:
        await message.answer(text, reply_markup=reply_markup)


@router.callback_query(F.data == "limits:back")
async def cb_limits_back(call: CallbackQuery, state: FSMContext) -> None:
    await state.clear()
    await call.message.edit_text(
        "⚙️ <b>Налаштування лімітів банку</b>\n\nОберіть банк:",
        reply_markup=keyboards.bank_limits_bank_kb()
    )
    await call.answer()


@router.callback_query(F.data.startswith("limits:bank:"))
async def cb_limits_bank(call: CallbackQuery) -> None:
    bank = call.data.split(":")[2]
    user_id = call.from_user.id
    row = await _db.get_user_bank_limits(user_id, bank)

    # Порожнє поле в рядку означає «не задавав» — там діє довідник банку.
    # Показуємо саме те число, за яким працює движок, і позначаємо, що з
    # нього ввів користувач: інакше меню показує 100 000, а звідки воно —
    # незрозуміло.
    user_set = {f for f in LIMIT_FIELDS if row and row.get(f) is not None}
    limits = merge_limits(bank, row)

    profile = get_bank_profile(bank)
    if profile is DEFAULT_BANK_PROFILE:
        src_line = "<i>Банку немає в довіднику — діють загальні дефолти.</i>\n"
    else:
        rec = ""
        if profile.safe_monthly_uah:
            rec = ", реком. " + f"{profile.safe_monthly_uah:,.0f}".replace(",", " ") + " ₴/міс"
        src_line = f"<i>Дефолти — з довідника банків (tier {profile.tier}{rec}).</i>\n"

    await call.message.edit_text(
        f"⚙️ <b>Ліміти: {bank.capitalize()}</b>\n"
        f"{src_line}"
        f"<i>✏️ — задано вами. Натисніть на поле для зміни значення:</i>",
        reply_markup=keyboards.bank_limits_fields_kb(bank, limits, user_set)
    )
    await call.answer()


@router.callback_query(F.data.startswith("limits:field:"))
async def cb_limits_field(call: CallbackQuery, state: FSMContext) -> None:
    parts = call.data.split(":")
    bank = parts[2]
    field = parts[3]
    label = keyboards.LIMIT_FIELD_LABELS.get(field, field)
    await state.update_data(limit_bank=bank, limit_field=field)
    is_int = field in ("max_tx_per_day", "cooldown_hours")
    hint = "ціле число" if is_int else "сума в грн"
    await call.message.edit_text(
        f"⚙️ <b>{label}</b> ({bank.capitalize()})\n\nВведіть нове значення ({hint}):",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="📴 Вимкнути ліміт", callback_data="limits:disable")],
            [InlineKeyboardButton(text="🔙 Скасувати", callback_data=f"limits:bank:{bank}")]
        ])
    )
    await state.set_state(BankLimitStates.waiting_value)
    await call.answer()


@router.callback_query(F.data == "limits:disable")
async def cb_limits_disable(call: CallbackQuery, state: FSMContext) -> None:
    data = await state.get_data()
    bank = data.get("limit_bank")
    field = data.get("limit_field")
    if not bank or not field:
        return await call.answer("❌ Сталася помилка: недостатньо даних у стані FSM.", show_alert=True)
    
    await _db.set_user_bank_limit(call.from_user.id, bank, field, -1)
    await state.clear()
    await call.answer(f"✅ Ліміт для {bank.capitalize()} вимкнено.")
    
    # Рефреш меню лімітів банку
    limits = await _db.get_user_bank_limits(call.from_user.id, bank)
    await call.message.edit_text(
        f"⚙️ <b>Ліміти: {bank.capitalize()}</b>\n"
        f"<i>Натисніть на поле для зміни значення:</i>",
        reply_markup=keyboards.bank_limits_fields_kb(bank, limits)
    )


@router.message(BankLimitStates.waiting_value)
async def process_limit_value(message: Message, state: FSMContext) -> None:
    data = await state.get_data()
    bank = data["limit_bank"]
    field = data["limit_field"]

    try:
        value = float(message.text.strip().replace(',', '.'))
        if field in ("max_tx_per_day", "cooldown_hours"):
            value = int(value)
    except ValueError:
        return await message.answer("❌ Некоректний формат числа. Спробуйте ще раз:")

    await _db.set_user_bank_limit(message.from_user.id, bank, field, value)
    label = keyboards.LIMIT_FIELD_LABELS.get(field, field)
    await state.clear()
    await message.answer(f"✅ <b>{label}</b> для {bank.capitalize()} змінено на <b>{value}</b>")

    # Показати оновлені ліміти
    limits = await _db.get_user_bank_limits(message.from_user.id, bank)
    await message.answer(
        f"⚙️ <b>Ліміти: {bank.capitalize()}</b>\n"
        f"<i>Натисніть на поле для зміни значення:</i>",
        reply_markup=keyboards.bank_limits_fields_kb(bank, limits)
    )


# ═══════════════════════════════════════════════════════════════════════════════
# D7: Індивідуальні ліміти картки (локальні override)
# ═══════════════════════════════════════════════════════════════════════════════

@router.callback_query(F.data.regexp(r"^card:limits:[a-f0-9\-]{36}$"))
async def cb_card_limits(call: CallbackQuery) -> None:
    """Показати меню індивідуальних лімітів для конкретної картки."""
    card_id = call.data.split(":")[2]
    cards = await _db.get_cards(call.from_user.id)
    card = next((c for c in cards if c["id"] == card_id), None)
    if not card:
        return await call.answer("❌ Картку не знайдено", show_alert=True)

    is_custom = bool(card.get("is_custom_limits", 0))
    effective = await _db.get_card_effective_limits(card_id, call.from_user.id, card["bank_name"])

    mode_text = "🟢 <b>Локальні ліміти</b> (перезаписують глобальні)" if is_custom else "⚪ <b>Глобальні ліміти</b> (з налаштувань банку)"

    await call.message.edit_text(
        f"⚙️ <b>Ліміти для {card['bank_name'].capitalize()} {card['last_four']}</b>\n"
        f"{mode_text}\n\n"
        f"<i>Натисніть на поле для зміни значення:</i>",
        reply_markup=keyboards.card_limits_fields_kb(card_id, effective, is_custom)
    )
    await call.answer()


@router.callback_query(F.data.startswith("card:limits:toggle:"))
async def cb_card_limits_toggle(call: CallbackQuery) -> None:
    """Тумблер локальних/глобальних лімітів."""
    card_id = call.data.split(":")[3]
    cards = await _db.get_cards(call.from_user.id)
    card = next((c for c in cards if c["id"] == card_id), None)
    if not card:
        return await call.answer("❌ Картку не знайдено", show_alert=True)

    current = bool(card.get("is_custom_limits", 0))
    new_val = not current
    await _db.toggle_card_custom_limits(card_id, new_val)

    status = "УВІМКНЕНО 🟢" if new_val else "ВИМКНЕНО ⚪"
    await call.answer(f"Локальні ліміти: {status}", show_alert=True)

    # Refresh the menu
    effective = await _db.get_card_effective_limits(card_id, call.from_user.id, card["bank_name"])
    mode_text = "🟢 <b>Локальні ліміти</b> (перезаписують глобальні)" if new_val else "⚪ <b>Глобальні ліміти</b> (з налаштувань банку)"

    await call.message.edit_text(
        f"⚙️ <b>Ліміти для {card['bank_name'].capitalize()} {card['last_four']}</b>\n"
        f"{mode_text}\n\n"
        f"<i>Натисніть на поле для зміни значення:</i>",
        reply_markup=keyboards.card_limits_fields_kb(card_id, effective, new_val)
    )


@router.callback_query(F.data.startswith("clf:"))
async def cb_card_limits_field(call: CallbackQuery, state: FSMContext) -> None:
    """Запит нового значення ліміту для конкретної картки."""
    parts = call.data.split(":")
    card_id = parts[1]
    field = parts[2]
    label = keyboards.LIMIT_FIELD_LABELS.get(field, field)
    await state.update_data(card_limit_card_id=card_id, card_limit_field=field)
    is_int = field in ("max_tx_per_day", "cooldown_hours")
    hint = "ціле число" if is_int else "сума в грн"
    await call.message.edit_text(
        f"⚙️ <b>{label}</b> (індивідуальний)\n\nВведіть нове значення ({hint}):",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="📴 Вимкнути ліміт", callback_data="card:limits:disable")],
            [InlineKeyboardButton(text="🔙 Скасувати", callback_data=f"card:limits:{card_id}")]
        ])
    )
    await state.set_state(CardLimitStates.waiting_value)
    await call.answer()


@router.callback_query(F.data == "card:limits:disable")
async def cb_card_limits_disable(call: CallbackQuery, state: FSMContext) -> None:
    data = await state.get_data()
    card_id = data.get("card_limit_card_id")
    field = data.get("card_limit_field")
    if not card_id or not field:
        return await call.answer("❌ Сталася помилка: недостатньо даних у стані FSM.", show_alert=True)
    
    await _db.update_card_limit_override(card_id, field, -1)
    await state.clear()
    await call.answer("✅ Ліміт для картки вимкнено.")
    
    # Рефреш меню індивідуальних лімітів
    cards = await _db.get_cards(call.from_user.id)
    card = next((c for c in cards if c["id"] == card_id), None)
    if not card:
        return await call.message.edit_text("❌ Картку не знайдено.")
        
    is_custom = bool(card.get("is_custom_limits", 0))
    effective = await _db.get_card_effective_limits(card_id, call.from_user.id, card["bank_name"])
    mode_text = "🟢 <b>Локальні ліміти</b> (перезаписують глобальні)" if is_custom else "⚪ <b>Глобальні ліміти</b> (з налаштувань банку)"
    
    await call.message.edit_text(
        f"⚙️ <b>Ліміти для {card['bank_name'].capitalize()} {card['last_four']}</b>\n"
        f"{mode_text}\n\n"
        f"<i>Натисніть на поле для зміни значення:</i>",
        reply_markup=keyboards.card_limits_fields_kb(card_id, effective, is_custom)
    )


@router.message(CardLimitStates.waiting_value)
async def process_card_limit_value(message: Message, state: FSMContext) -> None:
    """Зберігає нове значення ліміту для конкретної картки."""
    data = await state.get_data()
    card_id = data["card_limit_card_id"]
    field = data["card_limit_field"]

    try:
        value = float(message.text.strip().replace(',', '.'))
        if field in ("max_tx_per_day", "cooldown_hours"):
            value = int(value)
    except ValueError:
        return await message.answer("❌ Некоректний формат числа. Спробуйте ще раз:")

    await _db.update_card_limit_override(card_id, field, value)
    label = keyboards.LIMIT_FIELD_LABELS.get(field, field)
    await state.clear()
    await message.answer(f"✅ <b>{label}</b> для цієї картки змінено на <b>{value}</b>")

    # Refresh card limits view
    effective = await _db.get_card_effective_limits(card_id)
    cards = await _db.get_cards(message.from_user.id)
    card = next((c for c in cards if c["id"] == card_id), None)
    is_custom = bool(card.get("is_custom_limits", 0)) if card else True
    bank_label = card["bank_name"].capitalize() if card else ""
    last4 = card["last_four"] if card else ""

    mode_text = "🟢 <b>Локальні ліміти</b>" if is_custom else "⚪ <b>Глобальні ліміти</b>"
    await message.answer(
        f"⚙️ <b>Ліміти для {bank_label} {last4}</b>\n"
        f"{mode_text}\n\n"
        f"<i>Натисніть на поле для зміни значення:</i>",
        reply_markup=keyboards.card_limits_fields_kb(card_id, effective, is_custom)
    )


@router.callback_query(F.data.startswith("card:refresh:"))
async def cb_card_force_refresh_api(call: CallbackQuery, state: FSMContext):
    """
    Хендлер примусового полінгу балансу Монобанку через пряме API.
    Рятує від ліміту 64 байт Telegram, дістаючи ID карти з кешу підбору.
    """
    try:
        # Витягуємо cache_key з callback рядка
        cache_key = call.data.split(":")[-1]

        # Імпортуємо наш оперативний кеш із модуля нотифікатора
        from bot.card_notifier import _card_matching_cache

        cache_data = _card_matching_cache.get(cache_key)
        if not cache_data or not cache_data.get("found_cards"):
            return await call.answer("❌ Сесія підбору карт застаріла. Оновіть спред.", show_alert=True)

        # Забираємо дані картки безпосередньо з кешу оперативки
        target_card = cache_data["found_cards"][0]
        card_id = target_card.get("id") or target_card.get("card_id")

        if not card_id:
            return await call.answer("❌ Не вдалося визначити ID картки", show_alert=True)

        await call.answer("🔄 Запит до Монобанку відправлено...")

        # Викликаємо твою бойову функцію force_refresh_mono_balance
        # Переконайся, що об'єкт бази `_db` доступний у цьому модулі
        new_balance = await _db.force_refresh_mono_balance(card_id)

        if new_balance is not None:
            await call.answer(f"✅ Баланс успішно актуалізовано через API: {new_balance:,.2f} ₴", show_alert=True)

            # Опціонально: тут можна викликати метод перерендеру повідомлення,
            # щоб цифра «Баланс у боті» миттєво змінилася на екрані ТГ.
        else:
            await call.answer("❌ Монобанк відхилив запит або токен недійсний", show_alert=True)

    except Exception as e:
        logging.getLogger("Commands").error(f"Помилка кнопки оновлення балансу: {e}")
        await call.answer("🔥 Внутрішня помилка хендлера", show_alert=True)


from bot.keyboards import card_display_settings_kb  # Імпортуємо твою нову клавіатуру


# ═══════════════════════════════════════════════════════════════════════════════
# ХЕНДЛЕРИ НАЛАШТУВАННЯ ВІДОБРАЖЕННЯ КАРТКОВОГО МОДУЛЯ
# ═══════════════════════════════════════════════════════════════════════════════

@router.callback_query(F.data == "set:card_display_menu")
async def cb_open_card_display_menu(call: CallbackQuery):
    """
    Точка входу: відкриває підменю конфігурації вмісту та формату виводу карт.
    """
    try:
        chat_id = call.message.chat.id

        # Стягуємо поточний стан налаштувань користувача з SQLite
        current_settings = await _db.get_user_card_settings(chat_id) or {}

        text = (
            "💳 <b>Налаштування відображення карткового модуля</b>\n\n"
            "Конфігурація формату та деталізації виводу карток в алертах сканера:\n\n"
            "• <b>Вивід карт:</b> інтегрувати картки прямо в текст спреду чи надсилати окремою Reply-відповіддю на повідомлення.\n"
            "• <b>Логіка спойлера:</b> ховати ліміти під спойлер завжди чи автоматично розгортати та підсвічувати 🚨 картку при загрозі фінмоніторингу.\n"
            "• <b>Деталізація:</b> відображати повний зріз лімітів банку чи компактний вигляд (суто баланси та добовий залишок)."
        )

        await call.message.edit_text(
            text=text,
            reply_markup=card_display_settings_kb(current_settings, await _db.get_feature_status(chat_id, FEATURE_INTER_BANK))
        )
    except Exception as e:
        logging.getLogger("Commands").error(f"Помилка відкриття меню карт: {e}")
        await call.answer("🔥 Не вдалося завантажити підменю", show_alert=True)


@router.callback_query(F.data.in_({
    "disp:toggle:card_output_mode",
    "disp:toggle:enable_smart_spoiler",
    "disp:toggle:card_detail_level",
    "disp:toggle:enable_in_single_modes",
    "disp:toggle:show_balances_breakdown",
    "disp:toggle:show_transfer_tips"
}))
async def cb_toggle_card_display_fields(call: CallbackQuery):
    """
    Обробляє зміну станів для текстових та булевих параметрів карткового модуля.
    Реалізує циклічне перемикання значень та викликає ререндер.
    """
    try:
        chat_id = call.message.chat.id
        # Витягуємо назву налаштування (останній елемент рядка)
        field = call.data.split(":")[-1]

        # 1. Читаємо поточну конфігурацію з бази даних
        current_settings = await _db.get_user_card_settings(chat_id) or {}

        # 2. Розумно перемикаємо стани залежно від типу даних
        if field == "card_output_mode":
            # Зміна рядка: inline 🔄 reply
            current_settings["card_output_mode"] = (
                "reply" if current_settings.get("card_output_mode", "inline") == "inline" else "inline"
            )
        elif field == "enable_smart_spoiler":
            # Інверсія булевого прапорця (дефолт True)
            current_settings["enable_smart_spoiler"] = not current_settings.get("enable_smart_spoiler", True)

        elif field == "card_detail_level":
            # Зміна рядка: full 🔄 compact
            current_settings["card_detail_level"] = (
                "compact" if current_settings.get("card_detail_level", "full") == "full" else "full"
            )
        elif field == "enable_in_single_modes":
            current_settings["enable_in_single_modes"] = not current_settings.get("enable_in_single_modes", False)
        elif field == "show_balances_breakdown":
            current_settings["show_balances_breakdown"] = not current_settings.get("show_balances_breakdown", True)
        elif field == "show_transfer_tips":
            current_settings["show_transfer_tips"] = not current_settings.get("show_transfer_tips", True)

        # 3. Зберігаємо оновлений словник назад у базу даних
        # Переконайся, що назва твого методу оновлення саме така, або адаптуй під свій update_user_card_settings
        await _db.update_user_card_settings(chat_id, current_settings)

        # 4. Робимо моментальний безшовний ререндер клавіатури в ТГ
        await call.message.edit_reply_markup(
            reply_markup=card_display_settings_kb(current_settings, await _db.get_feature_status(chat_id, FEATURE_INTER_BANK))
        )
        await call.answer("⚙️ Налаштування актуалізовано")

    except Exception as e:
        logging.getLogger("Commands").error(f"Помилка зміни параметра виводу карт: {e}")
        await call.answer("🔥 Помилка під час збереження змін", show_alert=True)


@router.callback_query(F.data.in_({
    "disp:cycle:card_split_mode",
    "disp:cycle:max_cards_per_order",
    "disp:cycle:show_rejected_orders",
}))
async def cb_cycle_card_matching_fields(call: CallbackQuery):
    """
    Перемикачі з трьома і більше значеннями — по колу.

    Окремо від disp:toggle, бо там кожен пункт має рівно два стани, а тут
    цикл: off → intra_bank → (inter_bank) і 1 → 2 → 3.
    """
    if not _db:
        return await call.answer("❌ БД не підключена.", show_alert=True)

    field = call.data.split(":")[2]
    chat_id = call.message.chat.id

    try:
        current = await _db.get_user_card_settings(chat_id) or {}
        inter_bank_on = await _db.get_feature_status(chat_id, FEATURE_INTER_BANK)

        if field == "card_split_mode":
            # Крутимо лише доступними режимами. «Між банками» вмикається
            # експериментальною фічею — доки її немає, вибрати цей режим не
            # можна, і краще сказати чому, ніж дати кнопку, яка мовчки нічого
            # не змінює.
            modes = list(available_split_modes(inter_bank_on))
            cur = current.get("card_split_mode") or SPLIT_INTRA_BANK
            idx = modes.index(cur) if cur in modes else 0
            current["card_split_mode"] = modes[(idx + 1) % len(modes)]

        elif field == "max_cards_per_order":
            cur = int(current.get("max_cards_per_order", 3) or 3)
            current["max_cards_per_order"] = cur % 3 + 1

        elif field == "show_rejected_orders":
            cur = current.get("show_rejected_orders") or "with_reason"
            current["show_rejected_orders"] = "hide" if cur == "with_reason" else "with_reason"

        await _db.update_user_card_settings(chat_id, current)
        await call.message.edit_reply_markup(
            reply_markup=card_display_settings_kb(current, inter_bank_on)
        )

        if field == "card_split_mode" and not inter_bank_on:
            await call.answer(
                "«Між банками» вмикається в /features → 💳 КАРТКИ → "
                "Кошики карток між банками",
                show_alert=True,
            )
        else:
            await call.answer("⚙️ Налаштування актуалізовано")

    except Exception as e:
        logging.getLogger("Commands").error("Помилка зміни параметра матчингу: %s", e)
        await call.answer("🔥 Помилка під час збереження змін", show_alert=True)


@router.callback_query(F.data == "disp:set:cold_card_limit")
async def cb_set_cold_card_limit(call: CallbackQuery, state: FSMContext):
    if not _db:
        return await call.answer("❌ БД не підключена.", show_alert=True)
    await state.set_state(SettingStates.waiting_cold_card_limit)
    
    current_settings = await _db.get_user_card_settings(call.message.chat.id) or {}
    ccl = current_settings.get("cold_card_limit", 2000.0)
    
    text = (
        "🌱 <b>Налаштування ліміту для непрогрітих карт</b>\n\n"
        "Введіть суму ліміту в ₴, яка буде виділятися для кожної непрогрітої картки (напр. <code>5000</code>).\n"
        "Введіть <code>0</code>, щоб вимкнути обмеження прогріву повністю.\n\n"
        f"<i>Поточне значення:</i> <b>{ccl:.0f} ₴</b>"
    )
    
    cancel_kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🔙 Скасувати", callback_data="set:card_display_menu")]
    ])
    
    await call.message.edit_text(text, reply_markup=cancel_kb)
    await call.answer()


@router.message(SettingStates.waiting_cold_card_limit)
async def process_cold_card_limit_input(message: Message, state: FSMContext):
    if not _db:
        await state.clear()
        return await message.answer("❌ БД не підключена.")
        
    try:
        val = float(message.text.strip().replace(",", "."))
        if val < 0:
            raise ValueError("Limit cannot be negative")
    except ValueError:
        return await message.answer("❌ Будь ласка, введіть додатнє число або 0:")
    
    chat_id = message.chat.id
    current_settings = await _db.get_user_card_settings(chat_id) or {}
    current_settings["cold_card_limit"] = val
    
    await _db.update_user_card_settings(chat_id, current_settings)
    await state.clear()
    
    limit_text = f"<b>{val:.0f} ₴</b>" if val > 0 else "<b>вимкнено</b>"
    await message.answer(f"✅ Ліміт для непрогрітих карт змінено на {limit_text}!")
    
    # Повертаємо користувача до меню налаштувань відображення
    text = (
        "💳 <b>Налаштування відображення карткового модуля</b>\n\n"
        "Конфігурація формату та деталізації виводу карток в алертах сканера:\n\n"
        "• <b>Вивід карт:</b> інтегрувати картки прямо в текст спреду чи надсилати окремою Reply-відповіддю на повідомлення.\n"
        "• <b>Логіка спойлера:</b> ховати ліміти під спойлер завжди чи автоматично розгортати та підсвічувати 🚨 картку при загрозі фінмоніторингу.\n"
        "• <b>Деталізація:</b> відображати повний зріз лімітів банку чи компактний вигляд (суто баланси та добовий залишок)."
    )
    
    await message.answer(
        text=text,
        reply_markup=card_display_settings_kb(current_settings, await _db.get_feature_status(chat_id, FEATURE_INTER_BANK))
    )