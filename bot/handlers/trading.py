# bot/handlers/trading.py
# Create ad, taker execute, single-leg, TT, maker/taker settings FSM.

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
    _generate_dashboard_text, CreateAdStates, _maker_monitor, TakerExecuteStates, MakerSettingsStates,
    TakerSellSettingsStates, TakerBuySettingsStates, _active_repricers, _single_leg_cache, _spread_cache,
    _taker_order_cache,
)
from bot.handlers.filters import (
    _get_network_fee, _sell_roi_text, _tbuy_banks_kb, _tbuy_price_strategy_kb, _tsell_price_strategy_kb, _calc_roi,
    _buy_confirm_text, _buy_final_kb, _save_taker_sell_db, _sell_final_kb, _save_taker_buy_db,
    _set_scanner_mode_db, _get_scanner_modes,
    _update_taker_sell_param_db, _update_taker_buy_param_db, _get_taker_sell_preset, _get_taker_buy_preset,
    _get_current_market_rate, _sell_preset_text, _buy_preset_text, _taker_preset_kb
)
from bot.keyboards import back_to_main_kb, main_menu_kb
from config.banks import DEFAULT_BANK_CODES, BANK_NAMES
from config import settings

router = Router()
logger = logging.getLogger(__name__)

@router.callback_query(F.data == "ad:create")
async def on_ad_create(call: CallbackQuery, state: FSMContext) -> None:
    await state.clear()
    from bot.keyboards import create_ad_exchange_kb
    with suppress(TelegramBadRequest):
        await call.message.edit_text(
            "📝 <b>Створення P2P оголошення</b>\n\n"
            "Обери біржу:",
            reply_markup=create_ad_exchange_kb(),
        )
    await call.answer()


@router.callback_query(F.data.startswith("ad:ex:"))
async def on_ad_exchange(call: CallbackQuery, state: FSMContext) -> None:
    exchange = call.data.split(":")[2]
    if exchange == "_unsupported":
        return await call.answer("⏳ Ця біржа ще не підтримується", show_alert=True)
    await state.update_data(ad_exchange=exchange)
    await state.set_state(CreateAdStates.waiting_side)
    from bot.keyboards import create_ad_side_kb
    with suppress(TelegramBadRequest):
        await call.message.edit_text(
            f"📝 <b>Оголошення на {exchange}</b>\n\n"
            "Обери сторону:",
            reply_markup=create_ad_side_kb(),
        )
    await call.answer()


@router.callback_query(F.data.startswith("ad:side:"))
async def on_ad_side(call: CallbackQuery, state: FSMContext) -> None:
    side = call.data.split(":")[2]  # BUY or SELL
    await state.update_data(ad_side=side)
    await state.set_state(CreateAdStates.waiting_price)

    data = await state.get_data()
    exchange = data.get("ad_exchange", "")

    # Підказка PriceAdvisor
    hint = ""
    from core.engine.price_advisor import PriceAdvisor
    if side == "SELL":
        # Для продажу потрібна ціна купівлі — поки що без підказки
        hint = "\n\n💡 <i>Після вводу ціни покажу мінімальну рентабельну ціну.</i>"
    elif side == "BUY":
        # Для купівлі — дістаємо sell_book_top з БД
        sell_book_top = 0.0
        if _db:
            sell_book_top = await _db.get_best_sell_price(exchange=exchange, minutes=5)
        if sell_book_top > 0:
            advice = PriceAdvisor.suggest_buy_price(
                sell_book_top=sell_book_top,
                buy_exchange=exchange,
                sell_exchange=exchange,
            )
            hint = "\n\n" + PriceAdvisor.format_buy_suggestion(advice)
        else:
            hint = "\n\n💡 <i>Немає даних стакану — підказка буде після накопичення снапшотів.</i>"

    icon = "🛒" if side == "BUY" else "💸"
    with suppress(TelegramBadRequest):
        await call.message.edit_text(
            f"{icon} <b>{side} на {exchange}</b>\n\n"
            f"Введи ціну (UAH за 1 USDT):{hint}",
            reply_markup=back_to_main_kb(),
        )
    await call.answer()


@router.message(CreateAdStates.waiting_price)
async def on_ad_price(message: Message, state: FSMContext) -> None:
    try:
        price = float(message.text.strip().replace(",", "."))
        if price <= 0:
            raise ValueError
    except ValueError:
        return await message.answer("❌ Введи ціну (додатне число).")

    await state.update_data(ad_price=price)
    await state.set_state(CreateAdStates.waiting_amount)

    data = await state.get_data()
    side = data.get("ad_side", "SELL")
    exchange = data.get("ad_exchange", "")

    # Підказка PriceAdvisor
    hint = ""
    from core.engine.price_advisor import PriceAdvisor
    if side == "SELL":
        advice = PriceAdvisor.suggest_sell_price(
            buy_price=price,
            amount_usdt=500.0,
            buy_exchange=exchange,
            sell_exchange=exchange,
        )
        hint = "\n\n" + PriceAdvisor.format_sell_suggestion(advice)
    elif side == "BUY":
        sell_book_top = 0.0
        if _db:
            sell_book_top = await _db.get_best_sell_price(exchange=exchange, minutes=5)
        if sell_book_top > 0 and price < sell_book_top:
            advice = PriceAdvisor.suggest_buy_price(
                sell_book_top=sell_book_top,
                buy_exchange=exchange,
                sell_exchange=exchange,
            )
            if price > advice["max_buy_price"]:
                hint = f"\n\n⚠️ Ціна {price:.2f} вище рекомендованого макс. {advice['max_buy_price']:.2f}"
            else:
                hint = f"\n\n✅ Ціна ОК (макс. рекомендована: {advice['max_buy_price']:.2f})"

    await message.answer(
        f"✅ Ціна: <b>{price:.4f}</b> ₴{hint}\n\n"
        f"Введи кількість <b>USDT</b>:",
        reply_markup=back_to_main_kb(),
    )


@router.message(CreateAdStates.waiting_amount)
async def on_ad_amount(message: Message, state: FSMContext) -> None:
    try:
        amount = float(message.text.strip().replace(",", "."))
        if amount <= 0:
            raise ValueError
    except ValueError:
        return await message.answer("❌ Введи кількість USDT (додатне число).")

    await state.update_data(ad_amount=amount)
    await state.set_state(CreateAdStates.waiting_min_limit)

    data = await state.get_data()
    side = data.get("ad_side", "SELL")
    price = data.get("ad_price", 0)
    exchange = data.get("ad_exchange", "")

    # Перерахунок підказки з реальним amount
    hint = ""
    from core.engine.price_advisor import PriceAdvisor
    if side == "SELL" and price > 0:
        advice = PriceAdvisor.suggest_sell_price(
            buy_price=price,
            amount_usdt=amount,
            buy_exchange=exchange,
            sell_exchange=exchange,
        )
        hint = (
            f"\n\n💡 Перерахунок для {amount:.0f} USDT:\n"
            f"Мін. ціна продажу: <b>{advice['min_sell_price']:.4f}</b> ₴\n"
            f"Профіт: +{advice['profit_at_min_uah']:.2f} ₴"
        )

    max_fiat = amount * price
    await message.answer(
        f"✅ Кількість: <b>{amount:.2f}</b> USDT (~{max_fiat:.0f} ₴){hint}\n\n"
        f"Введи <b>мінімальний ліміт</b> угоди (₴):\n"
        f"<i>(напр. 500)</i>",
        reply_markup=back_to_main_kb(),
    )


@router.message(CreateAdStates.waiting_min_limit)
async def on_ad_min_limit(message: Message, state: FSMContext) -> None:
    try:
        val = float(message.text.strip().replace(",", "."))
        if val < 0:
            raise ValueError
    except ValueError:
        return await message.answer("❌ Введи суму (₴).")
    await state.update_data(ad_min_limit=val)
    await state.set_state(CreateAdStates.waiting_max_limit)
    await message.answer(
        f"✅ Мін. ліміт: <b>{val:.0f}</b> ₴\n\n"
        f"Введи <b>максимальний ліміт</b> угоди (₴):",
        reply_markup=back_to_main_kb(),
    )


@router.message(CreateAdStates.waiting_max_limit)
async def on_ad_max_limit(message: Message, state: FSMContext) -> None:
    try:
        val = float(message.text.strip().replace(",", "."))
        if val <= 0:
            raise ValueError
    except ValueError:
        return await message.answer("❌ Введи суму (₴).")
    data = await state.get_data()
    if val <= data.get("ad_min_limit", 0):
        return await message.answer("❌ Макс. ліміт повинен бути більше мінімального.")
    await state.update_data(ad_max_limit=val)
    await state.set_state(CreateAdStates.waiting_banks)

    # Показуємо вибір банків
    from bot.keyboards import create_ad_banks_kb
    from config.banks import BANK_NAMES
    current_banks = list(BANK_NAMES.keys())[:3]  # default top 3
    await state.update_data(ad_banks=current_banks)
    await message.answer(
        f"✅ Макс. ліміт: <b>{val:.0f}</b> ₴\n\n"
        f"Обери банки для оголошення:",
        reply_markup=create_ad_banks_kb(BANK_NAMES, current_banks),
    )


@router.callback_query(F.data.startswith("ad:bank:"))
async def on_ad_bank_toggle(call: CallbackQuery, state: FSMContext) -> None:
    code = call.data.split(":")[2]
    data = await state.get_data()
    selected = data.get("ad_banks", [])
    if code in selected:
        if len(selected) > 1:
            selected.remove(code)
        else:
            return await call.answer("❗ Мінімум 1 банк", show_alert=True)
    else:
        selected.append(code)
    await state.update_data(ad_banks=selected)
    from bot.keyboards import create_ad_banks_kb
    from config.banks import BANK_NAMES
    with suppress(TelegramBadRequest):
        await call.message.edit_reply_markup(
            reply_markup=create_ad_banks_kb(BANK_NAMES, selected)
        )
    await call.answer()


@router.callback_query(F.data == "ad:banks_done")
async def on_ad_banks_done(call: CallbackQuery, state: FSMContext) -> None:
    await state.set_state(CreateAdStates.waiting_terms)
    with suppress(TelegramBadRequest):
        await call.message.edit_text(
            "📝 <b>Умови угоди</b>\n\n"
            "Напиши умови оголошення (або <code>-</code> щоб пропустити):",
            reply_markup=back_to_main_kb(),
        )
    await call.answer()


@router.message(CreateAdStates.waiting_terms)
async def on_ad_terms(message: Message, state: FSMContext) -> None:
    terms = message.text.strip()
    if terms == "-":
        terms = ""
    await state.update_data(ad_terms=terms)
    await state.set_state(CreateAdStates.waiting_confirm)

    data = await state.get_data()
    from config.banks import BANK_NAMES
    bank_names = [BANK_NAMES.get(c, c) for c in data.get("ad_banks", [])]
    side_icon = "🛒" if data.get("ad_side") == "BUY" else "💸"

    from bot.keyboards import create_ad_confirm_kb
    await message.answer(
        f"{side_icon} <b>Підтвердження оголошення</b>\n\n"
        f"Біржа: <b>{data.get('ad_exchange')}</b>\n"
        f"Сторона: <b>{data.get('ad_side')}</b>\n"
        f"Ціна: <code>{data.get('ad_price', 0):.4f}</code> ₴\n"
        f"Кількість: <code>{data.get('ad_amount', 0):.2f}</code> USDT\n"
        f"Ліміти: <b>{data.get('ad_min_limit', 0):.0f} — {data.get('ad_max_limit', 0):.0f}</b> ₴\n"
        f"Банки: {', '.join(bank_names)}\n"
        f"Умови: {terms or '—'}\n\n"
        f"⚠️ Натисни ✅ для створення.",
        reply_markup=create_ad_confirm_kb(),
    )


@router.callback_query(F.data == "ad:confirm")
async def on_ad_confirm(call: CallbackQuery, state: FSMContext) -> None:
    data = await state.get_data()
    await state.clear()

    exchange = data.get("ad_exchange", "")
    side = data.get("ad_side", "")

    with suppress(TelegramBadRequest):
        await call.message.edit_text(f"⏳ <b>Створюю {side} оголошення на {exchange}…</b>", reply_markup=None)

    try:
        # Завантажуємо credentials юзера
        user_id = call.from_user.id
        creds = {}
        if _db:
            creds = await _db.get_credentials_for_user(exchange, user_id) or {}
        if not creds.get("api_key"):
            raise RuntimeError(f"Немає API ключів для {exchange}. Підключіть через /connect.")

        # Використовуємо RouteExecutor для створення оголошення
        from core.engine.route_executor import RouteExecutor
        executor = RouteExecutor()

        result = await executor.create_maker_ad(
            exchange=exchange,
            action=side,
            price=data.get("ad_price", 0),
            amount_usdt=data.get("ad_amount", 0),
            min_order_uah=data.get("ad_min_limit", 500),
            max_order_uah=data.get("ad_max_limit"),
            credentials=creds,
            payment_methods=data.get("ad_banks", []),
        )

        if result.get("success"):
            ad_id = result.get("ad_id", "—")
            text = (
                f"✅ <b>Оголошення створено!</b>\n\n"
                f"ID: <code>{ad_id}</code>\n"
                f"Біржа: {exchange} | {side}"
            )

            # 🚀 Автозапуск AdRepricer (якщо це SELL оголошення)
            if side == "SELL" and ad_id and ad_id != "—":
                try:
                    from core.engine.ad_repricer import AdRepricer

                    buy_price = data.get("ad_price", 0)
                    amount_usdt = data.get("ad_amount", 500)

                    async def _notify_tg(msg: str) -> None:
                        if _notifier:
                            try:
                                await _notifier._send_with_retry(msg, chat_id=call.message.chat.id)
                            except Exception as e:
                                logger.error("AdRepricer notify error: %s", e)

                    repricer = AdRepricer(
                        session_id=0,
                        sell_ad_id=str(ad_id),
                        exchange=exchange,
                        buy_price=buy_price,
                        amount_usdt=amount_usdt,
                        network_fee=0.0,
                        min_margin=0.003,
                        notify_cb=_notify_tg,
                    )

                    from infrastructure.http.bybit_p2p_client import BybitP2PClient

                    async def _fetch_book_top(ex: str, _ad_id: str):
                        client = BybitP2PClient()
                        client.set_credentials(creds.get("api_key", ""), creds.get("api_secret", ""))
                        async with client:
                            return await client.fetch_p2p_book_top(side=1, exclude_ad_id=_ad_id)

                    async def _update_price(ex: str, _ad_id: str, new_price: float):
                        from core.engine.route_executor import RouteExecutor
                        _exec = RouteExecutor()
                        return await _exec.update_maker_ad_price(ex, _ad_id, new_price, creds)

                    import asyncio as _aio
                    repricer_key = f"{call.from_user.id}_{ad_id}"
                    _active_repricers[repricer_key] = _aio.create_task(
                        repricer.watch(_fetch_book_top, _update_price, _db),
                        name=f"repricer_{ad_id}",
                    )
                    text += "\n\n📊 <i>AdRepricer запущено — ціна автоматично оновлюється.</i>"
                except Exception as e:
                    logger.warning("Не вдалось запустити AdRepricer: %s", e)
                    text += f"\n\n⚠️ <i>AdRepricer не запущено: {str(e)[:100]}</i>"

            # 🚀 Автозапуск MakerAdMonitor
            if ad_id and ad_id != "—":
                try:
                    if _maker_monitor:
                        chat_id = call.message.chat.id
                        import asyncio as _aio
                        _aio.create_task(
                            _maker_monitor.start_watching(user_id, chat_id, exchange, str(ad_id)),
                            name=f"maker_watch_{ad_id}",
                        )
                        text += "\n🔔 <i>Моніторинг вхідних ордерів запущено.</i>"
                except Exception as e:
                    logger.warning("Не вдалось запустити MakerAdMonitor: %s", e)

        else:
            text = f"❌ <b>Помилка:</b> {result.get('error', 'Unknown')}"
    except NotImplementedError as e:
        text = f"❌ {e}"
    except Exception as e:
        logger.error("ad:confirm error: %s", e, exc_info=True)
        text = f"❌ <b>Помилка:</b> <code>{str(e)[:200]}</code>"

    with suppress(TelegramBadRequest):
        await call.message.edit_text(text, reply_markup=keyboards.back_to_main_kb())
    await call.answer()


@router.callback_query(F.data == "ad:cancel")
async def on_ad_cancel(call: CallbackQuery, state: FSMContext) -> None:
    await state.clear()
    text, is_active = await _generate_dashboard_text(call.from_user.id)
    with suppress(TelegramBadRequest):
        await call.message.edit_text(
            text, reply_markup=main_menu_kb(is_active, is_muted(), _is_admin(call.from_user.id))
        )
    await call.answer("Скасовано")


# =========================================================================
# ⚡ TAKER EXECUTE — швидке відкриття ордера
# =========================================================================

# (shared _taker_order_cache is imported from bot.handlers.core)


@router.callback_query(F.data.startswith("taker:take:"))
async def on_taker_take(call: CallbackQuery, state: FSMContext) -> None:
    if not _single_leg_executor:
        return await call.answer("❌ SingleLegExecutor не підключено!", show_alert=True)

    cache_key = call.data.split(":", 2)[2]
    data = _taker_order_cache.get(cache_key)
    if not data:
        return await call.answer("❌ Ордер застарів (>5 хв).", show_alert=True)

    price = data["price"]
    min_usdt = data["min_limit"] / price if price > 0 else 0
    max_usdt = data["max_limit"] / price if price > 0 else 0
    action = data.get("action", "BUY")

    await state.update_data(
        taker_cache_key=cache_key, taker_action=action,
        taker_ad_id=data["ad_id"], taker_exchange=data["exchange"],
        taker_price=price, taker_merchant_id=data["merchant_id"],
        taker_bank=data.get("bank", ""),
        taker_min_usdt=min_usdt, taker_max_usdt=max_usdt,
    )
    await state.set_state(TakerExecuteStates.waiting_amount)

    icon = "🛒" if action == "BUY" else "💸"
    label = "Купівля" if action == "BUY" else "Продаж"
    text = (
        f"{icon} <b>{label} (Taker)</b>\n\n"
        f"Біржа: <b>{data.get('exchange')}</b>\n"
        f"Ціна: <code>{price:.4f}</code> UAH\n"
        f"Ліміти: <b>{min_usdt:.1f} — {max_usdt:.1f} USDT</b>\n\n"
        f"👇 Введи суму в <b>USDT</b> (або <code>max</code>):"
    )
    await call.message.answer(text, reply_markup=back_to_main_kb())
    await call.answer()


@router.message(TakerExecuteStates.waiting_amount)
async def on_taker_amount(message: Message, state: FSMContext) -> None:
    data = await state.get_data()
    min_usdt = data.get("taker_min_usdt", 0)
    max_usdt = data.get("taker_max_usdt", 0)
    raw = message.text.strip().lower()

    try:
        amount = max_usdt if raw == "max" else float(raw.replace(",", "."))
        if not (min_usdt - 0.001 <= amount <= max_usdt + 0.001):
            raise ValueError
    except ValueError:
        return await message.answer(
            f"❌ Сума від <b>{min_usdt:.1f}</b> до <b>{max_usdt:.1f}</b> (або 'max'):"
        )

    await state.update_data(taker_amount=amount)
    await state.set_state(TakerExecuteStates.waiting_confirm)

    action = data.get("taker_action", "BUY")
    icon = "🛒" if action == "BUY" else "💸"
    label = "Купівля" if action == "BUY" else "Продаж"
    price = data.get("taker_price", 0)
    fiat_amount = amount * price

    from aiogram.utils.keyboard import InlineKeyboardBuilder
    builder = InlineKeyboardBuilder()
    builder.row(
        InlineKeyboardButton(text="✅ Підтвердити", callback_data="taker:confirm"),
        InlineKeyboardButton(text="❌ Скасувати", callback_data="taker:cancel"),
    )

    await message.answer(
        f"{icon} <b>Підтвердження {label}</b>\n\n"
        f"Біржа: <b>{data.get('taker_exchange')}</b>\n"
        f"Об'єм: <code>{amount:.2f} USDT</code> (~{fiat_amount:.0f} ₴)\n"
        f"Ціна: <code>{price:.4f}</code>\n\n"
        f"⚠️ <i>Натисни ✅ — ордер відкриється автоматично.\n"
        f"Після цього отримаєш пряме посилання.</i>",
        reply_markup=builder.as_markup(),
    )


@router.callback_query(F.data.in_({"taker:confirm", "taker:cancel"}))
async def on_taker_confirm(call: CallbackQuery, state: FSMContext) -> None:
    if call.data == "taker:cancel":
        await state.clear()
        with suppress(TelegramBadRequest):
            await call.message.edit_text("🚫 Скасовано.", reply_markup=keyboards.back_to_main_kb())
        return await call.answer("Скасовано.")

    data = await state.get_data()
    await state.clear()

    if not _single_leg_executor:
        return await call.answer("❌ SingleLegExecutor не підключено!", show_alert=True)

    action = data.get("taker_action", "BUY")
    amount_usdt = data.get("taker_amount", 0)
    exchange = data.get("taker_exchange", "")

    with suppress(TelegramBadRequest):
        await call.message.edit_text(
            f"⏳ <b>Відкриваю {action} ордер на {exchange}…</b>", reply_markup=None,
        )

    try:
        if action == "BUY":
            result = await _single_leg_executor.execute_single_buy(
                exchange=exchange, ad_id=data.get("taker_ad_id", ""),
                price=data.get("taker_price", 0), amount_usdt=amount_usdt,
                merchant_id=data.get("taker_merchant_id", ""),
                owner_user_id=call.from_user.id,
                payment_method=data.get("taker_bank", ""),
            )
        else:
            result = await _single_leg_executor.execute_single_sell(
                exchange=exchange, ad_id=data.get("taker_ad_id", ""),
                price=data.get("taker_price", 0), amount_usdt=amount_usdt,
                merchant_id=data.get("taker_merchant_id", ""),
                owner_user_id=call.from_user.id,
                payment_method=data.get("taker_bank", ""),
            )

        if result["success"]:
            order_id = result.get("order_id", "")
            from core.analytics.merchant_profile import build_order_url
            from bot.deeplinks import tg_button_url

            web_url = build_order_url(exchange, order_id)
            # На телефоні відкриє застосунок одразу на екрані ордера,
            # на десктопі — звичайну веб-версію.
            order_url = tg_button_url(exchange, "order", order_id, web_fallback=web_url)

            text = (
                f"✅ <b>{action} ордер відкрито!</b>\n\n"
                f"Біржа: {exchange}\n"
                f"Order ID: <code>{order_id}</code>\n"
                f"Trade #: {result.get('trade_id', '—')}\n"
            )
            if result.get("warning"):
                text += f"\n⚠️ {result['warning']}\n"
            text += "\n<b>Тепер заверши угоду вручну 👇</b>"

            kb_rows = []
            if order_url:
                kb_rows.append([InlineKeyboardButton(
                    text=f"📱 Відкрити ордер ({exchange})", url=order_url
                )])
            kb_rows.append([InlineKeyboardButton(text="🔙 В меню", callback_data="menu:main")])
            kb = InlineKeyboardMarkup(inline_keyboard=kb_rows)

            # Окреме повідомлення з лінком у тілі: кнопки зникають при
            # редагуванні/навігації, а текстове посилання лишається в історії
            # чату — до ордера можна повернутись у будь-який момент.
            if order_url:
                with suppress(Exception):
                    await call.message.answer(
                        f"🔗 <b>{action} ордер на {exchange}</b>\n"
                        f"<code>{order_id}</code>\n\n"
                        f'<a href="{order_url}">Відкрити ордер</a>',
                        disable_web_page_preview=True,
                    )
        else:
            text = f"❌ <b>Помилка {action}:</b> {result.get('error', 'Unknown')}"
            if result.get("warning"):
                text += f"\n\n⚠️ {result['warning']}"
            kb = keyboards.back_to_main_kb()

    except Exception as e:
        logger.error("taker:confirm error: %s", e, exc_info=True)
        text = f"❌ <b>Критична помилка:</b> <code>{str(e)[:200]}</code>"
        kb = keyboards.back_to_main_kb()

    with suppress(TelegramBadRequest):
        await call.message.edit_text(text, reply_markup=kb)
    await call.answer()


# =========================================================================
# 🦵 SINGLE-LEG зі спред-алертів (кнопки «Купити» / «Продати»)
# =========================================================================

@router.callback_query(F.data.regexp(r"^sl:[bs]:"))
async def on_single_leg_take(call: CallbackQuery, state: FSMContext) -> None:
    """Обробляє натискання кнопок 'Купити'/'Продати' зі спред-алерту."""
    if not _single_leg_executor:
        return await call.answer("❌ SingleLegExecutor не підключено!", show_alert=True)

    raw_key = call.data  # sl:b:<key> or sl:s:<key>
    data = _single_leg_cache.get(raw_key)
    if not data:
        return await call.answer("❌ Дані застаріли, зачекайте новий алерт.", show_alert=True)

    action = "BUY" if raw_key.startswith("sl:b:") else "SELL"
    price = data.get("price", 0)
    min_usdt = data.get("min_limit", 0) / price if price > 0 else 0
    max_usdt = data.get("max_limit", 0) / price if price > 0 else 0

    await state.update_data(
        taker_action=action, taker_ad_id=data.get("ad_id", ""),
        taker_exchange=data.get("exchange", ""), taker_price=price,
        taker_merchant_id=data.get("merchant_id", ""),
        taker_bank=data.get("bank", ""),
        taker_min_usdt=min_usdt, taker_max_usdt=max_usdt,
    )
    await state.set_state(TakerExecuteStates.waiting_amount)

    icon = "🛒" if action == "BUY" else "💸"
    label = "Купівля" if action == "BUY" else "Продаж"
    text = (
        f"{icon} <b>{label} (Single-Leg)</b>\n\n"
        f"Біржа: <b>{data.get('exchange')}</b>\n"
        f"Ціна: <code>{price:.4f}</code> UAH\n"
        f"Ліміти: <b>{min_usdt:.1f} — {max_usdt:.1f} USDT</b>\n\n"
        f"👇 Введи суму в <b>USDT</b> (або <code>max</code>):"
    )
    await call.message.answer(text, reply_markup=back_to_main_kb())
    await call.answer()


# =========================================================================
# 🔄 T→T Авто-трейд зі спред-алертів
# =========================================================================

@router.callback_query(F.data.startswith("trade:tt:"))
async def on_trade_tt(call: CallbackQuery, state: FSMContext) -> None:
    """Ініціює T→T трейд зі спред-алерту. Зберігає контекст, просить суму."""
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
        "bank": getattr(alert, "buy_bank", ""),
        "min_limit": getattr(alert, "buy_min_limit", 0),
        "max_limit": getattr(alert, "buy_max_limit", 0),
    }
    sell_leg = {
        "exchange": alert.sell_exchange, "ad_id": getattr(alert, "sell_ad_id", ""),
        "price": alert.sell_price, "merchant_id": getattr(alert, "sell_merchant_id", ""),
        "bank": getattr(alert, "sell_bank", ""),
    }

    min_usdt = buy_leg["min_limit"] / buy_leg["price"] if buy_leg["price"] > 0 else 0
    max_usdt = buy_leg["max_limit"] / buy_leg["price"] if buy_leg["price"] > 0 else 0

    await state.update_data(
        tt_buy_leg=buy_leg, tt_sell_leg=sell_leg,
        tt_min_usdt=min_usdt, tt_max_usdt=max_usdt,
        tt_spread=getattr(alert, "spread", 0),
    )
    await state.set_state(TakerExecuteStates.waiting_amount)

    text = (
        f"🔄 <b>Авто T→T трейд</b>\n\n"
        f"Купівля: <b>{alert.buy_exchange}</b> @ <code>{alert.buy_price:.4f}</code>\n"
        f"Продаж: <b>{alert.sell_exchange}</b> @ <code>{alert.sell_price:.4f}</code>\n"
        f"Маржа: <b>{getattr(alert, 'spread', 0):.2f}%</b>\n\n"
        f"Ліміти: <b>{min_usdt:.1f} — {max_usdt:.1f} USDT</b>\n\n"
        f"👇 Введи суму в <b>USDT</b> (або <code>max</code>):"
    )
    await call.message.answer(text, reply_markup=back_to_main_kb())
    await call.answer()


@router.callback_query(F.data.in_({"tt:confirm", "tt:cancel"}))
async def on_tt_confirm(call: CallbackQuery, state: FSMContext) -> None:
    """Підтверджує або скасовує T→T трейд."""
    if call.data == "tt:cancel":
        await state.clear()
        with suppress(TelegramBadRequest):
            await call.message.edit_text("🚫 T→T скасовано.", reply_markup=back_to_main_kb())
        return await call.answer("Скасовано.")

    data = await state.get_data()
    await state.clear()

    if not _trade_worker:
        return await call.answer("❌ TradeWorker не підключено!", show_alert=True)

    buy_leg = data.get("tt_buy_leg", {})
    sell_leg = data.get("tt_sell_leg", {})
    amount_usdt = data.get("taker_amount", 0)

    with suppress(TelegramBadRequest):
        await call.message.edit_text(
            f"⏳ <b>Запускаю T→T: {buy_leg.get('exchange')} → {sell_leg.get('exchange')}…</b>",
            reply_markup=None,
        )

    try:
        success = await _trade_worker.execute_tt_route(
            buy_leg=buy_leg, sell_leg=sell_leg,
            amount_usdt=amount_usdt, owner_user_id=call.from_user.id,
        )
        if success:
            text = (
                f"✅ <b>T→T трейд запущено!</b>\n\n"
                f"Купівля: {buy_leg.get('exchange')} @ {buy_leg.get('price', 0):.4f}\n"
                f"Продаж: {sell_leg.get('exchange')} @ {sell_leg.get('price', 0):.4f}\n"
                f"Об'єм: <code>{amount_usdt:.2f} USDT</code>\n\n"
                f"📡 Моніторинг активний, оновлення прийдуть автоматично."
            )
        else:
            text = "❌ <b>T→T трейд не вдалося запустити.</b>\nПеревірте логи."
    except Exception as e:
        logger.error("tt:confirm error: %s", e, exc_info=True)
        text = f"❌ <b>Критична помилка:</b> <code>{str(e)[:200]}</code>"

    with suppress(TelegramBadRequest):
        await call.message.edit_text(text, reply_markup=back_to_main_kb())
    await call.answer()


# =========================================================================
# 💲 MAKER SETTINGS: Ціна купівлі + Цільова маржа
# =========================================================================

@router.callback_query(F.data == "set:maker_buy_price")
async def on_set_maker_buy_price(call: CallbackQuery, state: FSMContext) -> None:
    """Запит ціни купівлі для MAKER_SELL."""
    await state.set_state(MakerSettingsStates.waiting_buy_price)
    current = 0.0
    if _db:
        users = await _db.get_active_users()
        user = next((u for u in users if u["user_id"] == call.from_user.id), None)
        if user:
            current = float(user.get("maker_buy_price", 0))

    text = (
        "💲 <b>Ціна купівлі (UAH/USDT)</b>\n\n"
        f"Поточна: <code>{current:.4f}</code>\n\n"
        "Введи ціну, за якою ти купив USDT.\n"
        "Це потрібно для розрахунку мінімальної ціни продажу."
    )
    with suppress(TelegramBadRequest):
        await call.message.edit_text(text, reply_markup=back_to_main_kb())
    await call.answer()


@router.message(MakerSettingsStates.waiting_buy_price)
async def on_maker_buy_price_input(message: Message, state: FSMContext) -> None:
    """Зберігає ціну купівлі."""
    try:
        value = float(message.text.strip().replace(",", "."))
        if value <= 0:
            raise ValueError
    except (ValueError, AttributeError):
        return await message.answer("❌ Введи коректну ціну (число > 0):")

    if _db:
        conn = getattr(_db, "db", None) or getattr(_db, "_db", _db)
        await conn.execute(
            "UPDATE scanner_users SET maker_buy_price = ? WHERE user_id = ?",
            (value, message.from_user.id),
        )
        await conn.commit()

    await state.clear()
    await message.answer(
        f"✅ Ціна купівлі збережена: <code>{value:.4f}</code> UAH/USDT",
        reply_markup=back_to_main_kb(),
    )


@router.callback_query(F.data == "set:target_margin")
async def on_set_target_margin(call: CallbackQuery, state: FSMContext) -> None:
    """Запит цільової маржі для MAKER_BUY."""
    await state.set_state(MakerSettingsStates.waiting_target_margin)
    current = 0.5
    if _db:
        users = await _db.get_active_users()
        user = next((u for u in users if u["user_id"] == call.from_user.id), None)
        if user:
            current = float(user.get("target_margin", 0.005)) * 100

    text = (
        "📊 <b>Цільова маржа (%)</b>\n\n"
        f"Поточна: <code>{current:.2f}%</code>\n\n"
        "Введи бажану маржу у відсотках (напр. <code>0.5</code> = 0.5%).\n"
        "Використовується для розрахунку оптимальної ціни купівлі."
    )
    with suppress(TelegramBadRequest):
        await call.message.edit_text(text, reply_markup=back_to_main_kb())
    await call.answer()


@router.message(MakerSettingsStates.waiting_target_margin)
async def on_target_margin_input(message: Message, state: FSMContext) -> None:
    """Зберігає цільову маржу."""
    try:
        pct = float(message.text.strip().replace(",", "."))
        if pct < 0 or pct > 50:
            raise ValueError
    except (ValueError, AttributeError):
        return await message.answer("❌ Введи коректний % (0–50):")

    margin = pct / 100.0  # зберігаємо як десяткову
    if _db:
        conn = getattr(_db, "db", None) or getattr(_db, "_db", _db)
        await conn.execute(
            "UPDATE scanner_users SET target_margin = ? WHERE user_id = ?",
            (margin, message.from_user.id),
        )
        await conn.commit()

    await state.clear()
    await message.answer(
        f"✅ Цільова маржа збережена: <code>{pct:.2f}%</code>",
        reply_markup=back_to_main_kb(),
    )


# =========================================================================
# 💸 TAKER SELL FSM  (4 кроки → ROI → підтвердження)
# =========================================================================

@router.callback_query(TakerSellSettingsStates.waiting_amount_type, F.data.startswith("tsell_type:"))
async def on_tsell_amount_type(call: CallbackQuery, state: FSMContext) -> None:
    amt_type = call.data.split(":")[1]
    await state.update_data(amount_type=amt_type)
    await state.set_state(TakerSellSettingsStates.waiting_amount)
    await call.answer()
    
    if amt_type == "USDT":
        text = (
            "💸 <b>TAKER SELL — Крок 2/5</b>\n\n"
            "📦 <b>Скільки USDT ти хочеш продати?</b>\n"
            "<i>Введи кількість у USDT (наприклад: 500)</i>"
        )
    else:
        text = (
            "💸 <b>TAKER SELL — Крок 2/5</b>\n\n"
            "📦 <b>Скільки UAH ти інвестував?</b>\n"
            "<i>Введи суму в гривнях (наприклад: 20000)</i>"
        )
        
    with suppress(TelegramBadRequest):
        await call.message.edit_text(text, reply_markup=back_to_main_kb())


@router.message(TakerSellSettingsStates.waiting_amount)
async def on_tsell_amount(message: Message, state: FSMContext) -> None:
    try:
        val = float(message.text.strip().replace(",", "."))
        if val <= 0:
            raise ValueError
    except ValueError:
        return await message.answer("❌ Введи число > 0. Наприклад: <code>500</code>")
        
    data = await state.get_data()
    is_edit = data.get("is_edit", False)
    amt_type = data.get("amount_type", "USDT")
    
    if is_edit:
        if amt_type == "UAH":
            users = await _db.get_active_users()
            user_row = next((u for u in users if u["user_id"] == message.from_user.id), None)
            buy_price = float(user_row.get("taker_sell_price", 0)) if user_row else 0.0
            if buy_price <= 0:
                buy_price = 41.50
            amount_usdt = val / buy_price
        else:
            amount_usdt = val
            
        await _update_taker_sell_param_db(message.from_user.id, "amount", amount_usdt)
        await state.clear()
        
        users = await _db.get_active_users()
        user_row = next((u for u in users if u["user_id"] == message.from_user.id), None)
        preset = _get_taker_sell_preset(user_row)
        current_rate = await _get_current_market_rate(_db, preset["amount"])
        await message.answer(
            f"✅ Об'єм оновлено до {amount_usdt:.1f} USDT!\n\n" + _sell_preset_text(preset, current_rate),
            reply_markup=_taker_preset_kb("TAKER_SELL")
        )
        return

    if amt_type == "UAH":
        await state.update_data(amount_type="UAH", amount_uah=val)
    else:
        await state.update_data(amount_type="USDT", amount=val)
        
    await state.set_state(TakerSellSettingsStates.waiting_buy_price)
    label_vol = f"{val:.1f} UAH" if amt_type == "UAH" else f"{val:.1f} USDT"
    await message.answer(
        f"📦 Об'єм: <b>{label_vol}</b>\n\n"
        "💹 <b>TAKER SELL — крок 3/5</b>\n\n"
        "💲 За скільки ти купував ці USDT?\n"
        "<i>Введи курс купівлі в UAH/USDT (наприклад: 41.25)</i>",
        reply_markup=back_to_main_kb(),
    )


@router.message(TakerSellSettingsStates.waiting_buy_price)
async def on_tsell_buy_price(message: Message, state: FSMContext) -> None:
    try:
        val = float(message.text.strip().replace(",", "."))
        if val <= 0:
            raise ValueError
    except ValueError:
        return await message.answer("❌ Наприклад: <code>41.25</code>")
        
    data = await state.get_data()
    is_edit = data.get("is_edit", False)
    
    if is_edit:
        await _update_taker_sell_param_db(message.from_user.id, "buy_price", val)
        await state.clear()
        
        users = await _db.get_active_users()
        user_row = next((u for u in users if u["user_id"] == message.from_user.id), None)
        preset = _get_taker_sell_preset(user_row)
        current_rate = await _get_current_market_rate(_db, preset["amount"])
        await message.answer(
            f"✅ Ціну купівлі оновлено до {val:.4f} ₴!\n\n" + _sell_preset_text(preset, current_rate),
            reply_markup=_taker_preset_kb("TAKER_SELL")
        )
        return
        
    amt_type = data.get("amount_type", "USDT")
    if amt_type == "UAH":
        amount_uah = float(data["amount_uah"])
        amount_usdt = amount_uah / val
        await state.update_data(amount=amount_usdt, buy_price=val)
    else:
        await state.update_data(buy_price=val)
        
    await state.set_state(TakerSellSettingsStates.waiting_exchange)
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text="Binance", callback_data="tsell_ex:Binance"),
            InlineKeyboardButton(text="Bybit", callback_data="tsell_ex:Bybit"),
        ],
        [
            InlineKeyboardButton(text="OKX", callback_data="tsell_ex:OKX"),
            InlineKeyboardButton(text="MEXC", callback_data="tsell_ex:MEXC"),
        ],
        [
            InlineKeyboardButton(text="⚡ P2P (без комісії)", callback_data="tsell_ex:INTERNAL"),
        ],
    ])
    await message.answer(
        f"💲 Ціна купівлі: <b>{val:.4f} ₴</b>\n\n"
        "🏦 <b>TAKER SELL — крок 4/5</b>\n\n"
        "З якої біржі виводитимеш USDT?\n"
        "<i>(потрібно для розрахунку Network Fee)</i>",
        reply_markup=kb,
    )


@router.callback_query(TakerSellSettingsStates.waiting_exchange, F.data.startswith("tsell_ex:"))
async def on_tsell_exchange(call: CallbackQuery, state: FSMContext) -> None:
    exchange = call.data.split(":")[1]
    data = await state.get_data()
    is_edit = data.get("is_edit", False)
    
    if is_edit:
        await _update_taker_sell_param_db(call.from_user.id, "exchange", exchange)
        await state.clear()
        
        users = await _db.get_active_users()
        user_row = next((u for u in users if u["user_id"] == call.from_user.id), None)
        preset = _get_taker_sell_preset(user_row)
        current_rate = await _get_current_market_rate(_db, preset["amount"])
        await call.message.edit_text(
            _sell_preset_text(preset, current_rate),
            reply_markup=_taker_preset_kb("TAKER_SELL")
        )
        return await call.answer("✅ Біржу виводу оновлено!")
        
    await state.update_data(exchange=exchange)
    await call.answer()

    amount = float(data["amount"])
    buy_price = float(data["buy_price"])

    network_fee, network_name = _get_network_fee(exchange)
    usable_volume = max(amount - network_fee, 0.001)
    breakeven_price = (amount * buy_price) / usable_volume
    breakeven_pct = (breakeven_price / buy_price - 1) * 100

    await state.update_data(network_fee=network_fee, network_name=network_name)
    await state.set_state(TakerSellSettingsStates.waiting_profit)
    with suppress(TelegramBadRequest):
        await call.message.edit_text(
            f"🏦 Біржа: <b>{exchange}</b>  |  Network Fee: <b>{network_fee:.2f} USDT</b> ({network_name})\n"
            f"📦 Робочий об'єм: <b>{usable_volume:.2f} USDT</b>\n\n"
            "📈 <b>TAKER SELL — крок 4/5</b>\n\n"
            "Який прибуток (спред %) хочеш отримати?\n\n"
            f"⚠️ <i>Щоб не вийти в мінус — потрібно мінімум "
            f"<b>{breakeven_pct:.2f}%</b> спреду</i>\n\n"
            "<i>Введи число, наприклад: <code>0.8</code></i>",
            reply_markup=back_to_main_kb(),
        )


@router.message(TakerSellSettingsStates.waiting_profit)
async def on_tsell_profit(message: Message, state: FSMContext) -> None:
    data = await state.get_data()
    is_edit = data.get("is_edit", False)
    expect_price_input = data.get("expect_price_input", False)
    expect_exact_price = data.get("expect_exact_price", False)
    expect_price_range = data.get("expect_price_range", False)
    
    if expect_price_input or expect_exact_price:
        try:
            val = float(message.text.strip().replace(",", "."))
            if val <= 0: raise ValueError
        except ValueError:
            return await message.answer("❌ Введіть ціну (наприклад: 41.50)")
            
        strategy_name = "exact" if expect_exact_price else "min"
        
        if is_edit:
            await _update_taker_sell_param_db(message.from_user.id, "price_strategy", strategy_name)
            await _update_taker_sell_param_db(message.from_user.id, "price_input", val)
            await state.clear()
            
            users = await _db.get_active_users()
            user_row = next((u for u in users if u["user_id"] == message.from_user.id), None)
            preset = _get_taker_sell_preset(user_row)
            current_rate = await _get_current_market_rate(_db, preset["amount"])
            await message.answer(
                f"✅ Стратегію оновлено ({strategy_name}) та встановлено ціну {val:.4f} ₴!\n\n" + _sell_preset_text(preset, current_rate),
                reply_markup=_taker_preset_kb("TAKER_SELL")
            )
        else:
            await state.update_data(price_strategy=strategy_name, price_input=val, min_sell_price=val)
            speed_kb = InlineKeyboardMarkup(inline_keyboard=[[
                InlineKeyboardButton(text="⚡ Так, швидко!", callback_data="tsell_speed:FAST"),
                InlineKeyboardButton(text="🐢 Ні, чекатиму", callback_data="tsell_speed:ANY"),
            ]])
            await message.answer(
                f"✅ Встановлено стратегію {strategy_name} з ціною {val:.4f} ₴\n\n"
                "⏱ <b>Чи важлива швидкість продажу?</b>",
                reply_markup=speed_kb
            )
        return

    if expect_price_range:
        try:
            val = float(message.text.strip().replace(",", "."))
            if val <= 0: raise ValueError
        except ValueError:
            return await message.answer("❌ Введіть ціну (наприклад: 41.20)")
            
        step = data.get("range_step", "from")
        if step == "from":
            await state.update_data(price_from=val, range_step="to")
            await message.answer(
                f"↔️ Нижня межа: <b>{val:.4f} ₴</b>\n\n"
                "Тепер введіть <b>верхню межу</b> ціни продажу:"
            )
        else:
            price_from = float(data.get("price_from", 0.0))
            if val < price_from:
                return await message.answer(f"❌ Верхня межа ({val:.4f}) не може бути меншою за нижню ({price_from:.4f})")
                
            if is_edit:
                await _update_taker_sell_param_db(message.from_user.id, "price_strategy", "range")
                await _update_taker_sell_param_db(message.from_user.id, "price_range", (price_from, val))
                await state.clear()
                
                users = await _db.get_active_users()
                user_row = next((u for u in users if u["user_id"] == message.from_user.id), None)
                preset = _get_taker_sell_preset(user_row)
                current_rate = await _get_current_market_rate(_db, preset["amount"])
                await message.answer(
                    f"✅ Встановлено діапазон {price_from:.4f} - {val:.4f} ₴!\n\n" + _sell_preset_text(preset, current_rate),
                    reply_markup=_taker_preset_kb("TAKER_SELL")
                )
            else:
                await state.update_data(price_strategy="range", price_from=price_from, price_to=val, min_sell_price=price_from)
                speed_kb = InlineKeyboardMarkup(inline_keyboard=[[
                    InlineKeyboardButton(text="⚡ Так, швидко!", callback_data="tsell_speed:FAST"),
                    InlineKeyboardButton(text="🐢 Ні, чекатиму", callback_data="tsell_speed:ANY"),
                ]])
                await message.answer(
                    f"✅ Встановлено діапазон {price_from:.4f} - {val:.4f} ₴\n\n"
                    "⏱ <b>Чи важлива швидкість продажу?</b>",
                    reply_markup=speed_kb
                )
        return

    try:
        val = float(message.text.strip().replace(",", ".").replace("%", ""))
        if val <= 0:
            raise ValueError
    except ValueError:
        return await message.answer("❌ Введи число > 0. Наприклад: <code>0.8</code>")
        
    if is_edit:
        await _update_taker_sell_param_db(message.from_user.id, "price_strategy", "roi")
        await _update_taker_sell_param_db(message.from_user.id, "profit", val)
        await state.clear()
        
        users = await _db.get_active_users()
        user_row = next((u for u in users if u["user_id"] == message.from_user.id), None)
        preset = _get_taker_sell_preset(user_row)
        current_rate = await _get_current_market_rate(_db, preset["amount"])
        await message.answer(
            f"✅ Профіт оновлено до {val:.2f}%!\n\n" + _sell_preset_text(preset, current_rate),
            reply_markup=_taker_preset_kb("TAKER_SELL")
        )
        return
        
    network_fee = float(data.get("network_fee", 1.0))
    roi = _calc_roi(data["amount"], data["buy_price"], val, network_fee)

    await state.update_data(profit=val, roi=roi)

    speed_kb = InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text="⚡ Так, швидко!", callback_data="tsell_speed:FAST"),
        InlineKeyboardButton(text="🐢 Ні, чекатиму", callback_data="tsell_speed:ANY"),
    ]])
    await message.answer(
        f"{_sell_roi_text(data | {'profit': val, 'network_name': data.get('network_name', '')}, roi)}\n\n"
        "⏱ <b>Крок 5/5 — Час важливий?</b>\n\n"
        "⚡ <b>Швидко</b> — беремо тільки ордери які можуть повністю покрити наш обʼєм\n"
        "🐢 <b>Чекатиму</b> — беремо будь-які ордери, навіть якщо частковий обʼєм",
        reply_markup=speed_kb,
    )


@router.callback_query(F.data.startswith("tsell_speed:"))
async def on_tsell_speed(call: CallbackQuery, state: FSMContext) -> None:
    speed = call.data.split(":")[1]
    data = await state.get_data()
    is_edit = data.get("is_edit", False)
    
    if is_edit:
        await _update_taker_sell_param_db(call.from_user.id, "speed", speed)
        await state.clear()
        users = await _db.get_active_users()
        user_row = next((u for u in users if u["user_id"] == call.from_user.id), None)
        preset = _get_taker_sell_preset(user_row)
        current_rate = await _get_current_market_rate(_db, preset["amount"])
        await call.message.edit_text(
            _sell_preset_text(preset, current_rate),
            reply_markup=_taker_preset_kb("TAKER_SELL")
        )
        return await call.answer("✅ Швидкість оновлено!")
        
    await state.update_data(speed=speed)
    data = await state.get_data()
    roi = data.get("roi", {})
    await call.answer()
    
    if data.get("price_strategy", "roi") != "roi":
        min_sell = data.get("min_sell_price", 0.0)
        confirm_text = (
            "💸 <b>TAKER SELL | Підтвердження:</b>\n\n"
            f"├ 📦 Об'єм: <b>{data['amount']:.1f} USDT</b>\n"
            f"├ 💲 Стратегія: <b>{data['price_strategy']}</b>\n"
            f"├ 🔒 Ціна: <b>{min_sell:.4f} ₴</b>\n"
            f"└ ⏱ Швидкість: <b>{speed}</b>\n\n"
            "🚀 Запустити сканер?"
        )
        with suppress(TelegramBadRequest):
            await call.message.edit_text(
                confirm_text,
                reply_markup=_sell_final_kb(),
            )
    else:
        with suppress(TelegramBadRequest):
            await call.message.edit_text(
                _sell_roi_text(data, roi),
                reply_markup=_sell_final_kb(),
            )


@router.callback_query(F.data.in_({"tsell:launch", "tsell:save_and_launch"}))
async def on_tsell_launch(call: CallbackQuery, state: FSMContext) -> None:
    data = await state.get_data()
    strategy = data.get("price_strategy", "roi")
    
    if strategy == "roi":
        roi = data.get("roi")
        if not roi or not data.get("amount"):
            return await call.answer("❌ Дані FSM втрачено. Почни знову /mode.", show_alert=True)
    else:
        roi = {
            "min_sell_price": data.get("min_sell_price", 0.0),
            "net_profit_uah": 0.0,
            "invest_uah": data.get("amount", 0.0) * data.get("buy_price", 0.0)
        }
        if not data.get("amount"):
            return await call.answer("❌ Дані FSM втрачено. Почни знову /mode.", show_alert=True)

    await _save_taker_sell_db(call.from_user.id, data, roi)
    # Режим додається до активних, а не замінює їх: продаж можна ловити
    # одночасно з купівлею чи спредом.
    await _set_scanner_mode_db(call.from_user.id, "TAKER_SELL")
    await state.clear()

    from bot.keyboards import scanner_mode_kb
    price_label = f"{roi['min_sell_price']:.4f} ₴"
    if strategy == "range":
        price_label = f"{data.get('price_from', 0.0):.4f} - {data.get('price_to', 0.0):.4f} ₴"
        
    with suppress(TelegramBadRequest):
        await call.message.edit_text(
            "🚀 <b>TAKER SELL запущено!</b>\n\n"
            f"🔒 Ціна продажу: <b>{price_label}</b>\n"
            f"📦 Об'єм: <b>{data['amount']:.1f} USDT</b>\n\n"
            "<i>Сканер шукає ордери — алерт прийде як тільки знайдеться підходящий.</i>",
            reply_markup=scanner_mode_kb(await _get_scanner_modes(call.from_user.id)),
        )
    await call.answer("🚀 Запущено!")


def _tsell_edit_menu_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text="📦 Об'єм", callback_data="tsell_edit:amount"),
            InlineKeyboardButton(text="💲 Ціна входу", callback_data="tsell_edit:buy_price"),
        ],
        [
            InlineKeyboardButton(text="🏦 Біржа", callback_data="tsell_edit:exchange"),
            InlineKeyboardButton(text="📈 Стратегія", callback_data="tsell_edit:strategy"),
        ],
        [
            InlineKeyboardButton(text="⏱ Швидкість", callback_data="tsell_edit:speed"),
        ],
        [
            InlineKeyboardButton(text="🔙 Назад до пресету", callback_data="smode:TAKER_SELL"),
        ]
    ])


def _tbuy_edit_menu_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text="📦 Об'єм", callback_data="tbuy_edit:amount"),
            InlineKeyboardButton(text="📈 Стратегія ціни", callback_data="tbuy_edit:strategy"),
        ],
        [
            InlineKeyboardButton(text="📏 Ліміти", callback_data="tbuy_edit:limits"),
            InlineKeyboardButton(text="🏦 Банки", callback_data="tbuy_edit:banks"),
        ],
        [
            InlineKeyboardButton(text="💳 Режим балансу", callback_data="tbuy_edit:balance_mode"),
            InlineKeyboardButton(text="⏱ Швидкість", callback_data="tbuy_edit:speed"),
        ],
        [
            InlineKeyboardButton(text="🔙 Назад до пресету", callback_data="smode:TAKER_BUY"),
        ]
    ])


@router.callback_query(F.data == "tsell_edit_menu")
async def on_tsell_edit_menu(call: CallbackQuery, state: FSMContext) -> None:
    await state.clear()
    with suppress(TelegramBadRequest):
        await call.message.edit_reply_markup(reply_markup=_tsell_edit_menu_kb())
    await call.answer()


@router.callback_query(F.data == "tbuy_edit_menu")
async def on_tbuy_edit_menu(call: CallbackQuery, state: FSMContext) -> None:
    await state.clear()
    with suppress(TelegramBadRequest):
        await call.message.edit_reply_markup(reply_markup=_tbuy_edit_menu_kb())
    await call.answer()


@router.callback_query(F.data.startswith("tsell_edit:"))
async def on_tsell_edit(call: CallbackQuery, state: FSMContext) -> None:
    param = call.data.split(":")[1]
    await state.clear()
    await state.update_data(is_edit=True, pending_mode="TAKER_SELL", edit_param=param)
    
    if param == "amount":
        await state.set_state(TakerSellSettingsStates.waiting_amount_type)
        kb = InlineKeyboardMarkup(inline_keyboard=[
            [
                InlineKeyboardButton(text="💵 USDT (крипта)", callback_data="tsell_type:USDT"),
                InlineKeyboardButton(text="₴ UAH (гривня)", callback_data="tsell_type:UAH"),
            ],
            [
                InlineKeyboardButton(text="❌ Скасувати", callback_data="smode:TAKER_SELL"),
            ]
        ])
        await call.message.edit_text("📦 <b>Редагування об'єму для продажу</b>\n\nОберіть валюту:", reply_markup=kb)
        await call.answer()
        
    elif param == "buy_price":
        await state.set_state(TakerSellSettingsStates.waiting_buy_price)
        await call.message.edit_text(
            "💲 <b>Редагування ціни купівлі</b>\n\nВведіть нову ціну купівлі (UAH/USDT):",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[[
                InlineKeyboardButton(text="❌ Скасувати", callback_data="smode:TAKER_SELL")
            ]])
        )
        await call.answer()
        
    elif param == "exchange":
        await state.set_state(TakerSellSettingsStates.waiting_exchange)
        kb = InlineKeyboardMarkup(inline_keyboard=[
            [
                InlineKeyboardButton(text="Binance", callback_data="tsell_ex:Binance"),
                InlineKeyboardButton(text="Bybit", callback_data="tsell_ex:Bybit"),
            ],
            [
                InlineKeyboardButton(text="OKX", callback_data="tsell_ex:OKX"),
                InlineKeyboardButton(text="MEXC", callback_data="tsell_ex:MEXC"),
            ],
            [
                InlineKeyboardButton(text="⚡ P2P (без комісії)", callback_data="tsell_ex:INTERNAL"),
            ],
            [
                InlineKeyboardButton(text="❌ Скасувати", callback_data="smode:TAKER_SELL"),
            ]
        ])
        await call.message.edit_text("🏦 <b>Редагування біржі виводу</b>\n\nОберіть біржу:", reply_markup=kb)
        await call.answer()
        
    elif param == "strategy":
        await state.set_state(TakerSellSettingsStates.waiting_profit)
        await call.message.edit_text(
            "📈 <b>Редагування стратегії ціни</b>\n\nОберіть нову стратегію:",
            reply_markup=_tsell_price_strategy_kb()
        )
        await call.answer()
        
    elif param == "speed":
        await state.set_state(TakerSellSettingsStates.waiting_speed)
        kb = InlineKeyboardMarkup(inline_keyboard=[
            [
                InlineKeyboardButton(text="⚡ Так, швидко!", callback_data="tsell_speed:FAST"),
                InlineKeyboardButton(text="🐢 Ні, чекатиму", callback_data="tsell_speed:ANY"),
            ],
            [
                InlineKeyboardButton(text="❌ Скасувати", callback_data="smode:TAKER_SELL"),
            ]
        ])
        await call.message.edit_text("⏱ <b>Редагування швидкості</b>\n\nЧи важлива швидкість?", reply_markup=kb)
        await call.answer()


@router.callback_query(TakerSellSettingsStates.waiting_profit, F.data.startswith("tsell_ps:"))
async def on_tsell_price_strategy_cb(call: CallbackQuery, state: FSMContext) -> None:
    strategy = call.data.split(":")[1]
    await state.update_data(price_strategy=strategy)
    await call.answer()
    
    data = await state.get_data()
    is_edit = data.get("is_edit", False)
    cancel_cb = "smode:TAKER_SELL" if is_edit else "menu:main"
    
    if strategy == "roi":
        await state.set_state(TakerSellSettingsStates.waiting_profit)
        await call.message.edit_text(
            "📈 <b>Введіть прибуток (спред %) який ви хочете отримати:</b>\n"
            "<i>Наприклад: 0.8</i>",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[[
                InlineKeyboardButton(text="❌ Скасувати", callback_data=cancel_cb)
            ]])
        )
    elif strategy == "min":
        await state.set_state(TakerSellSettingsStates.waiting_profit)
        await state.update_data(expect_price_input=True)
        await call.message.edit_text(
            "⬆️ <b>Введіть мінімальну ціну продажу (UAH/USDT):</b>\n"
            "<i>Наприклад: 41.50</i>",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[[
                InlineKeyboardButton(text="❌ Скасувати", callback_data=cancel_cb)
            ]])
        )
    elif strategy == "range":
        await state.set_state(TakerSellSettingsStates.waiting_profit)
        await state.update_data(expect_price_range=True, range_step="from")
        await call.message.edit_text(
            "↔️ <b>Введіть нижню межу ціни продажу (UAH/USDT):</b>\n"
            "<i>Наприклад: 41.20</i>",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[[
                InlineKeyboardButton(text="❌ Скасувати", callback_data=cancel_cb)
            ]])
        )
    elif strategy == "exact":
        await state.set_state(TakerSellSettingsStates.waiting_profit)
        await state.update_data(expect_exact_price=True)
        await call.message.edit_text(
            "🎯 <b>Введіть точну ціну для снайпера (UAH/USDT):</b>\n"
            "<i>Наприклад: 41.35</i>",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[[
                InlineKeyboardButton(text="❌ Скасувати", callback_data=cancel_cb)
            ]])
        )


# =========================================================================
# 🛒 TAKER BUY FSM (Flexible Strategy, Single-param Edit, Confirmation)
# =========================================================================

@router.callback_query(F.data.startswith("tbuy_type:"))
async def on_tbuy_type_selected(call: CallbackQuery, state: FSMContext) -> None:
    tbuy_type = call.data.split(":", 1)[1]
    await state.update_data(tbuy_type=tbuy_type)
    await state.set_state(TakerBuySettingsStates.waiting_amount)

    if tbuy_type == "UAH":
        msg_text = (
            "🛒 <b>TAKER BUY — Крок 1/5</b>\n\n"
            "₴ <b>Введіть суму у гривнях (UAH) для купівлі:</b>\n"
            "<i>Наприклад: 20000. Бот авто-конвертує в еквівалент USDT за поточним курсом ринку!</i>"
        )
    else:
        msg_text = (
            "🛒 <b>TAKER BUY — Крок 1/5</b>\n\n"
            "📦 <b>Введіть кількість USDT для купівлі:</b>\n"
            "<i>Наприклад: 500</i>"
        )
    with suppress(TelegramBadRequest):
        await call.message.edit_text(msg_text, reply_markup=keyboards.back_to_main_kb())
    await call.answer()
async def on_tbuy_scale_on(call: CallbackQuery, state: FSMContext) -> None:
    user_id = call.from_user.id
    if _db:
        await _db.update_buy_balance_mode(user_id, "AUTO_SCALE", scale_down=1, scale_up=1)
        from core.engine.taker_scanner import trigger_buy_autoscale_check
        await trigger_buy_autoscale_check(_db, user_id)
    with suppress(TelegramBadRequest):
        await call.message.edit_text("✅ <b>Авто-масштабування під баланс карт увімкнено!</b>\nТепер об'єм купівлі підлаштовується під картки автоматично.")
    await call.answer("Увімкнено!")


@router.callback_query(F.data.startswith("tbuy_fit_bal:"))
async def on_tbuy_fit_bal(call: CallbackQuery, state: FSMContext) -> None:
    user_id = call.from_user.id
    fit_usdt = float(call.data.split(":", 1)[1])
    if _db:
        await _db.update_taker_buy_amount(user_id, fit_usdt)
    with suppress(TelegramBadRequest):
        await call.message.edit_text(f"✅ <b>Об'єм купівлі успішно встановлено до {fit_usdt:.2f} USDT!</b>")
    await call.answer("Збережено!")


@router.message(TakerBuySettingsStates.waiting_amount)
async def on_tbuy_amount(message: Message, state: FSMContext) -> None:
    text_raw = message.text.strip().lower()
    data = await state.get_data()
    tbuy_type = data.get("tbuy_type", "USDT")
    is_uah = tbuy_type == "UAH" or "грн" in text_raw or "uah" in text_raw or "₴" in text_raw
    cleaned = text_raw.replace("грн", "").replace("uah", "").replace("₴", "").replace("usdt", "").strip().replace(",", ".")
    try:
        val = float(cleaned)
        if val <= 0: raise ValueError
    except ValueError:
        return await message.answer("❌ Введи коректну суму. Наприклад: <code>500</code> або <code>20 000 грн</code>")

    est_rate = 40.0
    if _db:
        from bot.handlers.filters import _get_current_market_rate
        est_rate = await _get_current_market_rate(_db, 100.0)

    if is_uah:
        val_usdt = round(val / est_rate, 2)
        equiv_uah = val
    else:
        val_usdt = val
        equiv_uah = val_usdt * est_rate

    data = await state.get_data()
    is_edit = data.get("is_edit", False)
    if is_edit:
        await _update_taker_buy_param_db(message.from_user.id, "amount", val_usdt)
        await state.clear()
        
        users = await _db.get_active_users()
        user_row = next((u for u in users if u["user_id"] == message.from_user.id), None)
        preset = _get_taker_buy_preset(user_row)
        await message.answer(
            f"✅ Об'єм оновлено до {val_usdt:,.2f} USDT (~{equiv_uah:,.0f} ₴)!\n\n" + _buy_preset_text(preset),
            reply_markup=_taker_preset_kb("TAKER_BUY")
        )
        return
        
    await state.update_data(amount=val_usdt)
    await state.set_state(TakerBuySettingsStates.waiting_price_strategy)
    await message.answer(
        f"📦 Обʼєм: <b>{val_usdt:,.2f} USDT (~{equiv_uah:,.0f} ₴)</b>\n\n"
        f"💹 <b>TAKER BUY — крок 2/6</b>\n\n"
        f"Обери <b>стратегію ціни</b> для цього закупу:",
        reply_markup=_tbuy_price_strategy_kb(),
    )


async def _ask_tbuy_limits_msg(message: Message, state: FSMContext) -> None:
    await state.set_state(TakerBuySettingsStates.waiting_limit_min)
    kb = InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text="⏭ Пропустити ліміти", callback_data="tbuy_skip:limits")
    ]])
    await message.answer(
        "🛒 <b>TAKER BUY — Крок 4/6</b>\n\n"
        "📏 <b>Мінімальний ліміт ордерів (UAH)?</b>\n"
        "<i>Ордери з меншим лімітом ігноруються. Наприклад: 5000\nПропусти якщо не важливо.</i>",
        reply_markup=kb,
    )


@router.message(TakerBuySettingsStates.waiting_limit_min)
async def on_tbuy_limit_min(message: Message, state: FSMContext) -> None:
    try:
        val = float(message.text.strip().replace(",", "."))
        if val < 0:
            raise ValueError
    except ValueError:
        return await message.answer("❌ Введи число (наприклад: 5000):")
        
    await state.update_data(limit_min=val)
    await state.set_state(TakerBuySettingsStates.waiting_limit_max)
    
    data = await state.get_data()
    is_edit = data.get("is_edit", False)
    cancel_cb = "smode:TAKER_BUY" if is_edit else "menu:main"
    
    await message.answer(
        f"✅ Мін. ліміт: <b>{val:.0f} ₴</b>\n\n"
        "🛒 <b>TAKER BUY</b>\n\n"
        "📏 <b>Максимальний ліміт ордерів (UAH)?</b>\n"
        "<i>Ордери з більшим лімітом ігноруються. Наприклад: 50000</i>",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[[
            InlineKeyboardButton(text="❌ Скасувати", callback_data=cancel_cb)
        ]]),
    )


@router.message(TakerBuySettingsStates.waiting_limit_max)
async def on_tbuy_limit_max(message: Message, state: FSMContext) -> None:
    try:
        val = float(message.text.strip().replace(",", "."))
        if val < 0:
            raise ValueError
    except ValueError:
        return await message.answer("❌ Введи число:")
        
    data = await state.get_data()
    is_edit = data.get("is_edit", False)
    
    if is_edit:
        limit_min = float(data.get("limit_min", 0.0))
        await _update_taker_buy_param_db(message.from_user.id, "limits", (limit_min, val))
        await state.clear()
        
        users = await _db.get_active_users()
        user_row = next((u for u in users if u["user_id"] == message.from_user.id), None)
        preset = _get_taker_buy_preset(user_row)
        await message.answer(
            f"✅ Ліміти оновлено до {limit_min:.0f} - {val:.0f} ₴!\n\n" + _buy_preset_text(preset),
            reply_markup=_taker_preset_kb("TAKER_BUY")
        )
        return
        
    await state.update_data(limit_max=val)
    await _show_tbuy_banks_msg(message.from_user.id, state, message.answer)


@router.callback_query(
    TakerBuySettingsStates.waiting_price_strategy,
    F.data.startswith("tbuy_ps:")
)
async def on_tbuy_price_strategy(call: CallbackQuery, state: FSMContext) -> None:
    strategy = call.data.split(":")[1]
    await state.update_data(price_strategy=strategy)
    
    data = await state.get_data()
    is_edit = data.get("is_edit", False)
    cancel_cb = "smode:TAKER_BUY" if is_edit else "menu:main"

    if strategy == "any":
        if is_edit:
            await _update_taker_buy_param_db(call.from_user.id, "price_strategy", "any")
            await state.clear()
            users = await _db.get_active_users()
            user_row = next((u for u in users if u["user_id"] == call.from_user.id), None)
            preset = _get_taker_buy_preset(user_row)
            await call.message.edit_text(
                _buy_preset_text(preset),
                reply_markup=_taker_preset_kb("TAKER_BUY")
            )
            await call.answer("✅ Встановлено стратегію: будь-яка ціна")
            return
            
        await state.set_state(TakerBuySettingsStates.waiting_limit_min)
        with suppress(TelegramBadRequest):
            await call.message.edit_text(
                "🌐 Ціновий фільтр вимкнено.\n\n"
                "💰 <b>TAKER BUY — крок 4/6</b>\n\n"
                "Мінімальний ліміт ордера (UAH)?\n"
                "<i>0 — без обмеження</i>",
                reply_markup=keyboards.back_to_main_kb(),
            )
    elif strategy == "max":
        await state.set_state(TakerBuySettingsStates.waiting_price_to)
        with suppress(TelegramBadRequest):
            await call.message.edit_text(
                "⬇️ <b>Макс. ціна купівлі</b>\n\n"
                "Введи максимальну ціну (UAH/USDT):\n"
                "<i>Наприклад: 41.50</i>",
                reply_markup=InlineKeyboardMarkup(inline_keyboard=[[
                    InlineKeyboardButton(text="❌ Скасувати", callback_data=cancel_cb)
                ]]),
            )
    elif strategy == "range":
        await state.set_state(TakerBuySettingsStates.waiting_price_from)
        with suppress(TelegramBadRequest):
            await call.message.edit_text(
                "↔️ <b>Ціновий діапазон — нижня межа</b>\n\n"
                "Введи мінімальну ціну (UAH/USDT):\n"
                "<i>Наприклад: 41.20</i>",
                reply_markup=InlineKeyboardMarkup(inline_keyboard=[[
                    InlineKeyboardButton(text="❌ Скасувати", callback_data=cancel_cb)
                ]]),
            )
    elif strategy == "exact":
        await state.set_state(TakerBuySettingsStates.waiting_price_to)
        with suppress(TelegramBadRequest):
            await call.message.edit_text(
                "🎯 <b>Точна ціна (Снайпер)</b>\n\n"
                "Введи точну ціну (UAH/USDT):\n"
                "<i>Наприклад: 41.35\nБот реагуватиме лише на ордери з цією ціною ±0.005₴</i>",
                reply_markup=InlineKeyboardMarkup(inline_keyboard=[[
                    InlineKeyboardButton(text="❌ Скасувати", callback_data=cancel_cb)
                ]]),
            )
    await call.answer()


@router.message(TakerBuySettingsStates.waiting_price_from)
async def on_tbuy_price_from(message: Message, state: FSMContext) -> None:
    try:
        val = float(message.text.strip().replace(",", "."))
        if val <= 0: raise ValueError
    except ValueError:
        return await message.answer("❌ Введи коректну ціну. Наприклад: <code>41.20</code>")

    await state.update_data(price_from=val)
    await state.set_state(TakerBuySettingsStates.waiting_price_to)
    
    data = await state.get_data()
    is_edit = data.get("is_edit", False)
    cancel_cb = "smode:TAKER_BUY" if is_edit else "menu:main"
    
    await message.answer(
        f"↔️ Від: <b>{val:.4f} ₴</b>\n\n"
        "Тепер введи <b>верхню межу</b> діапазону:",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[[
            InlineKeyboardButton(text="❌ Скасувати", callback_data=cancel_cb)
        ]])
    )


@router.message(TakerBuySettingsStates.waiting_price_to)
async def on_tbuy_price_to(message: Message, state: FSMContext) -> None:
    try:
        val = float(message.text.strip().replace(",", "."))
        if val <= 0: raise ValueError
    except ValueError:
        return await message.answer("❌ Введи коректну ціну. Наприклад: <code>41.50</code>")

    data = await state.get_data()
    strategy = data.get("price_strategy", "max")
    price_from = data.get("price_from", 0.0)
    is_edit = data.get("is_edit", False)

    if strategy == "range" and val < price_from:
        return await message.answer(
            f"❌ Верхня межа <b>{val:.4f}</b> менша за нижню <b>{price_from:.4f}</b>"
        )

    if is_edit:
        await _update_taker_buy_param_db(message.from_user.id, "price_strategy", strategy)
        if strategy == "range":
            await _update_taker_buy_param_db(message.from_user.id, "price_from", price_from)
        await _update_taker_buy_param_db(message.from_user.id, "price_to", val)
        await state.clear()
        
        users = await _db.get_active_users()
        user_row = next((u for u in users if u["user_id"] == message.from_user.id), None)
        preset = _get_taker_buy_preset(user_row)
        await message.answer(
            f"✅ Стратегію ціни оновлено!\n\n" + _buy_preset_text(preset),
            reply_markup=_taker_preset_kb("TAKER_BUY")
        )
        return

    await state.update_data(price_to=val)
    await state.set_state(TakerBuySettingsStates.waiting_limit_min)

    labels = {"max": f"макс. {val:.4f} ₴", "exact": f"точно {val:.4f} ₴",
              "range": f"{price_from:.4f}–{val:.4f} ₴"}
    await message.answer(
        f"✅ Ціна: <b>{labels.get(strategy, str(val))}</b>\n\n"
        "💰 <b>TAKER BUY — крок 4/6</b>\n\n"
        "Мінімальний ліміт ордера (UAH)?\n"
        "<i>0 — без обмеження</i>",
        reply_markup=keyboards.back_to_main_kb(),
    )


@router.callback_query(F.data == "tbuy_skip:limits")
async def on_tbuy_skip_limits(call: CallbackQuery, state: FSMContext) -> None:
    data = await state.get_data()
    is_edit = data.get("is_edit", False)
    if is_edit:
        await _update_taker_buy_param_db(call.from_user.id, "limits", (0.0, 0.0))
        await state.clear()
        users = await _db.get_active_users()
        user_row = next((u for u in users if u["user_id"] == call.from_user.id), None)
        preset = _get_taker_buy_preset(user_row)
        await call.message.edit_text(
            _buy_preset_text(preset),
            reply_markup=_taker_preset_kb("TAKER_BUY")
        )
        return await call.answer("✅ Ліміти скинуто!")

    await state.update_data(limit_min=0.0, limit_max=0.0)
    await call.answer()
    await state.set_state(TakerBuySettingsStates.waiting_banks)
    selected = await _get_user_buy_banks_db(call.from_user.id)
    await state.update_data(selected_banks=selected)
    with suppress(TelegramBadRequest):
        await call.message.edit_text(
            "🛒 <b>TAKER BUY — Крок 5/6</b>\n\n"
            "🏦 <b>Обери банки для купівлі:</b>\n"
            "<i>Позначені банки вже з твоїх глобальних налаштувань</i>",
            reply_markup=_tbuy_banks_kb(selected),
        )


async def _show_tbuy_banks_msg(user_id: int, state: FSMContext, send_fn) -> None:
    await state.set_state(TakerBuySettingsStates.waiting_banks)
    selected = await _get_user_buy_banks_db(user_id)
    await state.update_data(selected_banks=selected)
    await send_fn(
        "🛒 <b>TAKER BUY — Крок 5/6</b>\n\n"
        "🏦 <b>Обери банки для купівлі:</b>\n"
        "<i>Позначені банки вже з твоїх глобальних налаштувань</i>",
        reply_markup=_tbuy_banks_kb(selected),
    )


async def _get_user_buy_banks_db(user_id: int) -> list:
    """
    Банки, з якими майстер Taker Buy стартує.

    resolve_banks поверне перевизначення режиму, якщо воно є, інакше —
    спільний список. Раніше тут читалось лише buy_bank_codes, тобто майстер
    показував (а потім і перезаписував) банки спред-режиму.
    """
    if not _db:
        return []

    from core.engine.bank_scope import resolve_banks

    users = await _db.get_active_users()
    user = next((u for u in users if u["user_id"] == user_id), None)
    return list(resolve_banks(user, "TAKER_BUY", "buy")) if user else []


@router.callback_query(TakerBuySettingsStates.waiting_banks, F.data.startswith("tbuy_bank:"))
async def on_tbuy_bank_action(call: CallbackQuery, state: FSMContext) -> None:
    code = call.data.split(":")[1]

    if code == "done":
        data = await state.get_data()
        if not data.get("selected_banks"):
            return await call.answer("⚠️ Обери хоча б один банк!", show_alert=True)
            
        is_edit = data.get("is_edit", False)
        if is_edit:
            await _update_taker_buy_param_db(call.from_user.id, "banks", data["selected_banks"])
            await state.clear()
            users = await _db.get_active_users()
            user_row = next((u for u in users if u["user_id"] == call.from_user.id), None)
            preset = _get_taker_buy_preset(user_row)
            await call.message.edit_text(
                _buy_preset_text(preset),
                reply_markup=_taker_preset_kb("TAKER_BUY")
            )
            return await call.answer("✅ Банки оновлено!")

        await state.set_state(TakerBuySettingsStates.waiting_speed)
        await call.answer()
        speed_kb = InlineKeyboardMarkup(inline_keyboard=[[
            InlineKeyboardButton(text="⚡ Важлива (ордер ≥ мій об'єм)", callback_data="tbuy_speed:FAST"),
            InlineKeyboardButton(text="🐢 Не важлива (частинами теж ок)", callback_data="tbuy_speed:ANY"),
        ]])
        with suppress(TelegramBadRequest):
            await call.message.edit_text(
                "🛒 <b>TAKER BUY — Крок 6/6</b>\n\n"
                "⏱ <b>Чи важлива швидкість купівлі?</b>\n\n"
                "⚡ <b>Важлива:</b> шукаємо ордери де ліміт ≥ твій об'єм (купиш одним ордером)\n"
                "🐢 <b>Не важлива:</b> показуємо всі підходящі ордери, навіть якщо частинами",
                reply_markup=speed_kb,
            )
        return

    data = await state.get_data()
    selected = list(data.get("selected_banks", []))
    code_str = str(code)
    if code_str in [str(s) for s in selected]:
        selected = [s for s in selected if str(s) != code_str]
    else:
        selected.append(code_str)
    await state.update_data(selected_banks=selected)
    with suppress(TelegramBadRequest):
        await call.message.edit_reply_markup(reply_markup=_tbuy_banks_kb(selected))
    await call.answer()


@router.callback_query(TakerBuySettingsStates.waiting_speed, F.data.startswith("tbuy_speed:"))
async def on_tbuy_speed(call: CallbackQuery, state: FSMContext) -> None:
    speed = call.data.split(":")[1]
    data = await state.get_data()
    is_edit = data.get("is_edit", False)
    if is_edit:
        await _update_taker_buy_param_db(call.from_user.id, "speed", speed)
        await state.clear()
        users = await _db.get_active_users()
        user_row = next((u for u in users if u["user_id"] == call.from_user.id), None)
        preset = _get_taker_buy_preset(user_row)
        await call.message.edit_text(
            _buy_preset_text(preset),
            reply_markup=_taker_preset_kb("TAKER_BUY")
        )
        return await call.answer("✅ Швидкість оновлено!")
        
    await state.update_data(speed=speed)
    data = await state.get_data()
    await call.answer()
    with suppress(TelegramBadRequest):
        await call.message.edit_text(
            _buy_confirm_text(data),
            reply_markup=_buy_final_kb(),
        )


@router.callback_query(F.data.in_({"tbuy:launch", "tbuy:save_and_launch"}))
async def on_tbuy_launch(call: CallbackQuery, state: FSMContext) -> None:
    data = await state.get_data()
    if not data.get("amount"):
        return await call.answer("❌ Дані FSM втрачено. Почни знову /mode.", show_alert=True)

    data["banks"] = [str(b) for b in data.get("selected_banks", [])]
    await _save_taker_buy_db(call.from_user.id, data)
    await _set_scanner_mode_db(call.from_user.id, "TAKER_BUY")
    await state.clear()

    from bot.keyboards import scanner_mode_kb
    mp_line = f"💰 Макс. ціна: <b>{data['max_price']:.2f} ₴</b>\n" if data.get("max_price", 0) > 0 else ""
    with suppress(TelegramBadRequest):
        await call.message.edit_text(
            "🚀 <b>TAKER BUY запущено!</b>\n\n"
            f"📦 Шукаю ордери для купівлі <b>{data['amount']:.1f} USDT</b>\n"
            + mp_line
            + "\n<i>Алерти надходять одразу як з'являються підходящі ордери.</i>",
            reply_markup=scanner_mode_kb(await _get_scanner_modes(call.from_user.id)),
        )
    await call.answer("🚀 Запущено!")


@router.callback_query(F.data.startswith("tbuy_edit:"))
async def on_tbuy_edit(call: CallbackQuery, state: FSMContext) -> None:
    param = call.data.split(":")[1]
    await state.clear()
    await state.update_data(is_edit=True, pending_mode="TAKER_BUY", edit_param=param)
    
    if param == "amount":
        await state.set_state(TakerBuySettingsStates.waiting_amount)
        await call.message.edit_text(
            "📦 <b>Редагування об'єму для купівлі</b>\n\nВведіть нову кількість у USDT:",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[[
                InlineKeyboardButton(text="❌ Скасувати", callback_data="smode:TAKER_BUY")
            ]])
        )
        await call.answer()
        
    elif param == "strategy":
        await state.set_state(TakerBuySettingsStates.waiting_price_strategy)
        await call.message.edit_text(
            "📈 <b>Редагування стратегії ціни</b>\n\nОберіть нову стратегію:",
            reply_markup=_tbuy_price_strategy_kb()
        )
        await call.answer()
        
    elif param == "limits":
        await state.set_state(TakerBuySettingsStates.waiting_limit_min)
        await call.message.edit_text(
            "📏 <b>Редагування лімітів</b>\n\nВведіть мінімальний ліміт ордера (UAH):\n"
            "<i>0 — без обмежень</i>",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[[
                InlineKeyboardButton(text="❌ Скасувати", callback_data="smode:TAKER_BUY")
            ]])
        )
        await call.answer()
        
    elif param == "banks":
        await state.set_state(TakerBuySettingsStates.waiting_banks)
        selected = await _get_user_buy_banks_db(call.from_user.id)
        await state.update_data(selected_banks=selected)
        await call.message.edit_text(
            "🏦 <b>Редагування банків для купівлі:</b>",
            reply_markup=_tbuy_banks_kb(selected)
        )
        await call.answer()
        
    elif param == "speed":
        await state.set_state(TakerBuySettingsStates.waiting_speed)
        kb = InlineKeyboardMarkup(inline_keyboard=[
            [
                InlineKeyboardButton(text="⚡ Важлива (ордер ≥ мій об'єм)", callback_data="tbuy_speed:FAST"),
                InlineKeyboardButton(text="🐢 Не важлива (частинами теж ок)", callback_data="tbuy_speed:ANY"),
            ],
            [
                InlineKeyboardButton(text="❌ Скасувати", callback_data="smode:TAKER_BUY"),
            ]
        ])
    elif param == "balance_mode":
        await _show_tbuy_balance_mode_menu(call, call.from_user.id)


# ═══════════════════════════════════════════════════════════════════════════════
# 💳 TAKER BUY Balance Mode & Auto-scaler Sub-menu
# ═══════════════════════════════════════════════════════════════════════════════

async def _show_tbuy_balance_mode_menu(call: CallbackQuery, user_id: int):
    user = await _db.get_user_by_id(user_id) if _db else None
    if not user:
        return await call.answer("❌ Користувача не знайдено", show_alert=True)

    mode = user.get("buy_balance_mode", "CARD_ENFORCED")
    down = int(user.get("buy_auto_scale_down", 1))
    up = int(user.get("buy_auto_scale_up", 1))

    text = (
        "💳 <b>Режим балансу та авто-масштабування (TAKER BUY)</b>\n\n"
        "Оберіть як бот повинен перевіряти суму на ваших картках:\n\n"
        "1️⃣ <b>💳 З урахуванням балансу карт (CARD_ENFORCED):</b>\n"
        "   Перевіряє, щоб сума на активних картках була не меншою за закупівлю. Якщо грошей недостатньо — виводить попередження в чат.\n\n"
        "2️⃣ <b>🔓 Ручний / Без перевірки карт (MANUAL_STRICT):</b>\n"
        "   Шукає ордери під вказаний об'єм USDT, не зважаючи на баланси у боті.\n\n"
        "3️⃣ <b>⚡ Авто-масштабування під баланс (AUTO_SCALE):</b>\n"
        "   Якщо баланс картки зменшився (наприклад, після купівлі) або поповнився — бот автоматично масштабує параметр закупівлі USDT!"
    )

    builder = InlineKeyboardBuilder()
    builder.row(InlineKeyboardButton(
        text=f"💳 З урахуванням карт {'✅' if mode == 'CARD_ENFORCED' else ''}",
        callback_data="tbuy_bm:CARD_ENFORCED"
    ))
    builder.row(InlineKeyboardButton(
        text=f"🔓 Без перевірки карт {'✅' if mode == 'MANUAL_STRICT' else ''}",
        callback_data="tbuy_bm:MANUAL_STRICT"
    ))
    builder.row(InlineKeyboardButton(
        text=f"⚡ Авто-масштабування {'✅' if mode == 'AUTO_SCALE' else ''}",
        callback_data="tbuy_bm:AUTO_SCALE"
    ))

    if mode == "AUTO_SCALE":
        builder.row(
            InlineKeyboardButton(text=f"📉 Авто-зменшення {'✅' if down else '❌'}", callback_data="tbuy_scale:down"),
            InlineKeyboardButton(text=f"📈 Авто-збільшення {'✅' if up else '❌'}", callback_data="tbuy_scale:up"),
        )

    builder.row(InlineKeyboardButton(text="🔙 Назад до пресету", callback_data="smode:TAKER_BUY"))

    await call.message.edit_text(text, reply_markup=builder.as_markup())
    if hasattr(call, "answer"):
        await call.answer()


@router.callback_query(F.data.startswith("tbuy_bm:"))
async def cb_tbuy_bal_mode(call: CallbackQuery):
    new_mode = call.data.split(":")[1]
    await _db.update_buy_balance_mode(call.from_user.id, mode=new_mode)
    await _show_tbuy_balance_mode_menu(call, call.from_user.id)


@router.callback_query(F.data.startswith("tbuy_scale:"))
async def cb_tbuy_scale_toggle(call: CallbackQuery):
    action = call.data.split(":")[1]
    user = await _db.get_user_by_id(call.from_user.id)
    if action == "down":
        cur_down = int(user.get("buy_auto_scale_down", 1))
        await _db.update_buy_balance_mode(call.from_user.id, scale_down=0 if cur_down else 1)
    elif action == "up":
        cur_up = int(user.get("buy_auto_scale_up", 1))
        await _db.update_buy_balance_mode(call.from_user.id, scale_up=0 if cur_up else 1)
    await _show_tbuy_balance_mode_menu(call, call.from_user.id)


# =========================================================================
# 🎯 СНАЙПЕР-ОРДЕРИ (VOLUME SWEEPER)
# =========================================================================

@router.callback_query(F.data.startswith("mkord:"))
async def on_maker_order_action(call: CallbackQuery):
    """Обробка кнопок Прийняти/Відхилити для вхідних maker-ордерів."""
    try:
        parts = call.data.split(":")
        if len(parts) < 3:
            return await call.answer("Помилка формату")

        action = parts[1]   # "accept" або "reject"
        order_id = parts[2]

        if action == "accept":
            # Просто підтверджуємо — фактичний release робиться на біржі вручну
            await call.answer("✅ Прийнято! Завершіть угоду на біржі.", show_alert=True)
            old_text = call.message.html_text or ""
            new_text = f"✅ <b>ПРИЙНЯТО</b>\n\n{old_text[:3500]}"
            from contextlib import suppress
            from aiogram.exceptions import TelegramBadRequest
            with suppress(TelegramBadRequest):
                await call.message.edit_text(new_text, reply_markup=None)

        elif action == "reject":
            await call.answer("❌ Відхилено. Спробуйте скасувати на біржі.", show_alert=True)
            old_text = call.message.html_text or ""
            new_text = f"❌ <b>ВІДХИЛЕНО</b>\n\n<del>{old_text[:3500]}</del>"
            from contextlib import suppress
            from aiogram.exceptions import TelegramBadRequest
            with suppress(TelegramBadRequest):
                await call.message.edit_text(new_text, reply_markup=None)

        else:
            await call.answer("Невідома дія")

    except Exception as e:
        logger.error("mkord callback error: %s", e)
        await call.answer("Помилка обробки", show_alert=True)


