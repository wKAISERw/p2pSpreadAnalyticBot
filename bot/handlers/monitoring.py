# bot/handlers/monitoring.py
# Status, stats, trades, sessions, balance, users, debug.

from __future__ import annotations
import asyncio
import logging
import html as _html
from typing import TYPE_CHECKING, Optional
from contextlib import suppress
from aiogram.exceptions import TelegramBadRequest
from aiogram import Router, F
from aiogram.filters import Command
from aiogram.types import Message, CallbackQuery, InlineKeyboardMarkup, InlineKeyboardButton
from aiogram.fsm.context import FSMContext
from aiogram.utils.keyboard import InlineKeyboardBuilder

from bot import keyboards
from bot.keyboards import main_menu_kb, stats_overview_kb
from bot.keyboards.exchanges import exchanges_status_kb, exchange_toggle_kb, exchange_cooldown_kb, exchange_down_kb
from bot.keyboards.common import back_to_status_kb, EXCHANGE_ICONS, back_to_main_kb
from config.runtime import runtime_config

from bot.handlers.core import (
    _is_admin, _db, _bot, _notifier, _account_clients,
    _trade_worker, _single_leg_executor, is_muted, update_stats,
    _generate_dashboard_text,
    _scanner_stats, _mute_until
)
from config.banks import DEFAULT_BANK_CODES, BANK_NAMES
from config import settings

router = Router()
logger = logging.getLogger(__name__)

@router.callback_query(F.data == "menu:monitoring")
async def cb_monitoring_menu(call: CallbackQuery, state: FSMContext) -> None:
    await state.clear()
    text = (
        "📊 <b>ARBIX QUANTUM | Моніторинг та аналітика</b>\n\n"
        "Тут ви можете переглянути стан системи сканера, статистику спредів "
        "і торгових угод, активні сесії та баланси.\n\n"
        "<i>Оберіть розділ моніторингу:</i>"
    )
    with suppress(TelegramBadRequest):
        await call.message.edit_text(
            text,
            reply_markup=keyboards.monitoring_menu_kb(is_admin=_is_admin(call.from_user.id))
        )
    await call.answer()


@router.callback_query(F.data == "menu:health")
@router.callback_query(F.data == "health:check_all")
async def cb_health_check_all(call: CallbackQuery):
    await call.message.edit_text("🏥 <b>Health Check API</b>\n\n⏳ Опитую всі підключені біржі...", reply_markup=None)
    
    from core.engine.exchange_manager import exchange_manager
    exchanges = ["Bybit", "OKX", "MEXC", "Binance", "Wallet", "BingX"]
    lines = ["🏥 <b>Health Check API</b>\n"]
    
    tasks = [exchange_manager.health_check(name) for name in exchanges]
    results = await asyncio.gather(*tasks)
    
    for name, (ok, msg) in zip(exchanges, results):
        icon = "🟢" if ok else "🔴"
        lines.append(f"{icon} <b>{name}</b>: {msg}")
        
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🔄 Оновити", callback_data="health:check_all")],
        [InlineKeyboardButton(text="🔙 До моніторингу", callback_data="menu:monitoring")]
    ])
    
    with suppress(TelegramBadRequest):
        await call.message.edit_text("\n".join(lines), reply_markup=kb)
    await call.answer()


@router.callback_query(F.data == "menu:logs")
@router.callback_query(F.data.startswith("logs:view:"))
async def cb_logs_view(call: CallbackQuery):
    if not _is_admin(call.from_user.id):
        return await call.answer("⛔ Доступ тільки для адміністраторів", show_alert=True)
        
    parts = call.data.split(":")
    log_name = parts[2] if len(parts) > 2 else "error"
    
    allowed_logs = {
        "error": "logs/error.log",
        "debug": "logs/debug.log",
        "verdict": "logs/verdict_audit.log",
        "llm": "logs/llm_decisions.log"
    }
    
    file_path = allowed_logs.get(log_name, "logs/error.log")
    
    try:
        import os
        if os.path.exists(file_path):
            with open(file_path, "r", encoding="utf-8", errors="ignore") as f:
                f.seek(0, os.SEEK_END)
                size = f.tell()
                seek_pos = max(0, size - 2500)
                f.seek(seek_pos)
                content = f.read()
                
                lines = content.splitlines()
                if len(lines) > 20:
                    lines = lines[-20:]
                log_text = "\n".join(lines)
        else:
            log_text = f"❌ Файл {file_path} не знайдено."
    except Exception as e:
        log_text = f"❌ Помилка читання логу: {e}"
        
    if not log_text.strip():
        log_text = "📭 Лог-файл порожній."
        
    import html
    escaped_log = html.escape(log_text)
    
    text = (
        f"📝 <b>Лог-файл:</b> <code>{file_path}</code>\n\n"
        f"<pre>{escaped_log}</pre>\n"
        f"<i>Показано останні 20 рядків логу.</i>"
    )
    
    builder = InlineKeyboardBuilder()
    builder.row(
        InlineKeyboardButton(text="❌ Error", callback_data="logs:view:error"),
        InlineKeyboardButton(text="🐞 Debug", callback_data="logs:view:debug"),
    )
    builder.row(
        InlineKeyboardButton(text="🛡️ Verdict", callback_data="logs:view:verdict"),
        InlineKeyboardButton(text="🧠 LLM Decisions", callback_data="logs:view:llm"),
    )
    builder.row(
        InlineKeyboardButton(text="🔄 Оновити", callback_data=f"logs:view:{log_name}"),
        InlineKeyboardButton(text="🔙 До моніторингу", callback_data="menu:monitoring")
    )
    
    with suppress(TelegramBadRequest):
        await call.message.edit_text(text, reply_markup=builder.as_markup(), parse_mode="HTML")
    await call.answer()


@router.message(Command("start"))
async def cmd_start(message: Message, state: FSMContext) -> None:
    await state.clear()
    if _db:
        await _db.register_user(user_id=message.from_user.id, chat_id=message.chat.id)

        # 🚀 Гарантовано вмикаємо персональний сканер юзера при /start
        conn = getattr(_db, "db", None) or getattr(_db, "_db", _db)
        await conn.execute(
            "UPDATE scanner_users SET is_alerts_active = 1 WHERE user_id = ?",
            (message.from_user.id,)
        )
        await conn.commit()
    if not _db:
        return await message.answer("❌ БД не підключена.")
    text = "📊 <b>Аналітичний центр Arbix Quantum</b>\n\nОберіть, яку саме статистику ви хочете переглянути:"
    # Відправляємо нову стартову клавіатуру
    text, is_active = await _generate_dashboard_text(message.from_user.id)
    await message.answer(text, reply_markup=main_menu_kb(is_active, is_muted(), _is_admin(message.from_user.id)))


# ── /stop (ЗУПИНКА ПЕРСОНАЛЬНОГО СКАНЕРА) ──────────────────────────────────
@router.message(Command("stop"))
async def cmd_stop(message: Message) -> None:
    if _db:
        conn = getattr(_db, "db", None) or getattr(_db, "_db", _db)
        # Вимикаємо юзера з активної сітки сканування
        await conn.execute(
            "UPDATE scanner_users SET is_alerts_active = 0 WHERE user_id = ?",
            (message.from_user.id,)
        )
        await conn.commit()

    text, is_active = await _generate_dashboard_text(message.from_user.id)
    await message.answer(
        "🛑 <b>Твій персональний сканер зупинено!</b>\n"
        "Система більше не витрачає ресурси на пошук твоїх лімітів, алерти не надходитимуть.\n\n"
        "▶️ Щоб запустити знову, напиши /start",
        reply_markup=main_menu_kb(is_active, is_muted(), _is_admin(message.from_user.id))
    )


# ── /help ──────────────────────────────────────────────────────────────────
@router.message(Command("help"))
async def cmd_help(message: Message) -> None:
    text = (
        "📖 <b>Детальна довідка P2P Сканера</b>\n\n"
        "Бот у реальному часі шукає спреди між біржами (Binance, Bybit, OKX, MEXC, Wallet, BingX, CryptoBot) "
        "та використовує <b>RiskEngine</b> для аналізу умов та відгуків мерчантів.\n\n"
        "📌 <b>Основні команди управління:</b>\n"
        "• <b>/start</b> — Запуск сканування та отримання алертів.\n"
        "• <b>/stop</b> — Тимчасово призупинити алерти.\n"
        "• <b>/status</b> — Перевірити поточний статус сканера.\n"
        "• <b>/active</b> — Показати всі активні спреди в реальному часі.\n"
        "• <b>/settings</b> — Панель налаштувань (капітал, банки, фільтри).\n"
        "• <b>/mode</b> — Перемикання режимів роботи (SPREAD, TAKER_BUY, TAKER_SELL).\n\n"
        "⚙️ <b>Команди API та Балансів:</b>\n"
        "• <b>/balance</b> — Перегляд балансів на підключених біржах.\n"
        "• <b>/keys</b> — Список підключених API-ключів для авто-замовлень.\n"
        "• <b>/sessions</b> — Стан авторизацій (Cookies, JWT) для Web-парсерів.\n"
        "• <b>/connect [біржа]</b> — Підключити API-ключі.\n\n"
        "⛔ <b>Керування Чорним списком (Blacklist):</b>\n"
        "• <b>/blacklist</b> — Показати статистику та останні записи блеклісту.\n"
        "• <b>/blacklist [query]</b> — Пошук мерчанта в блеклісті за ніком.\n"
        "• <b>/ban [exchange] [name_or_id] : [reason]</b> — Додати мерчанта в бан.\n"
        "  <i>Приклад:</i> <code>/ban CryptoBot Unborn Deer : реф</code>\n"
        "• <b>/unban [exchange] [name_or_id]</b> — Вилучити мерчанта з бана.\n\n"
        "💡 <b>Налаштування Чорного Списку:</b>\n"
        "У меню <code>/settings</code> ➔ <code>Фільтри мерчантів</code> ви можете змінити режим блеклісту:\n"
        "• <b>Приховати 🚫</b> — бот автоматично ігноруватиме спреди з шахраями.\n"
        "• <b>Попередити ⚠️</b> — бот пропустить спред, але додасть червоний банер з описом ризику від RiskEngine.\n\n"
        "🔧 <b>Інші команди:</b>\n"
        "• <b>/stats</b> — Детальна статистика прибутку.\n"
        "• <b>/debugfilters</b> — Діагностика фільтрів розсилки (admin)."
    )
    await message.answer(text)


# ── /mode (ЗМІНА РЕЖИМУ СКАНУВАННЯ) ──────────────────────────────────────
@router.message(Command("mode"))
async def cmd_mode(message: Message) -> None:
    """Показує меню вибору режиму сканера."""
    current_mode = "SPREAD"
    if _db:
        users = await _db.get_active_users()
        for u in users:
            if u["user_id"] == message.from_user.id:
                current_mode = u.get("scanner_mode", "SPREAD")
                break
    from bot.keyboards import scanner_mode_kb
    await message.answer(
        "🎯 <b>Режим сканування</b>\n\n"
        "• <b>SPREAD</b> — класичний, шукає зв'язки Купівля→Продаж з маржею\n"
        "• <b>TAKER BUY</b> — шукає найвигідніші sell-ордери для швидкої покупки\n"
        "• <b>TAKER SELL</b> — шукає найвигідніші buy-ордери для швидкого продажу\n"
        "• <b>MAKER BUY</b> — аналіз ринку + підказка оптимальної ціни купівлі\n"
        "• <b>MAKER SELL</b> — розрахунок мін. ціни продажу за ціною купівлі\n\n"
        f"Поточний: <b>{current_mode}</b>",
        reply_markup=scanner_mode_kb(current_mode),
    )


# ── /stats (СТАТИСТИКА) ──────────────────────────────────────────────────
@router.message(Command("stats"))
async def cmd_stats(message: Message) -> None:
    if not _db:
        return await message.answer("❌ БД не підключена.")
    text = "📊 <b>Аналітичний центр Arbix Quantum</b>\n\nОберіть, яку саме статистику ви хочете переглянути:"
    await message.answer(text, reply_markup=keyboards.stats_source_kb())


# ── /sessions (AUTH-СЕСІЇ) ────────────────────────────────────────────────
@router.message(Command("sessions"))
async def cmd_sessions(message: Message) -> None:
    if not _db:
        return await message.answer("❌ БД не підключена.")

    from core.workers.session_manager import SESSION_TTL
    import time as _time
    sessions = await _db.get_all_auth_sessions()

    if not sessions:
        return await message.answer("📭 Жодних auth-сесій не знайдено.")

    lines = ["🩺 <b>Auth-сесії:</b>\n"]
    for s in sessions:
        exchange = s.get("exchange", "?")
        updated_at = float(s.get("updated_at", 0))
        is_active = s.get("is_active", 0)
        age_h = (_time.time() - updated_at) / 3600 if updated_at else 0
        ttl_h = SESSION_TTL.get(exchange, 96 * 3600) / 3600
        remaining_h = ttl_h - age_h

        if not is_active:
            status = "❌ Протухла"
        elif remaining_h < 2:
            status = f"⚠️ Спливає ({remaining_h:.1f}г)"
        else:
            status = f"🟢 OK ({remaining_h:.0f}г)"

        lines.append(
            f"{'🟢' if is_active else '🔴'} <b>{exchange}</b>: {status}\n"
            f"  Вік: {age_h:.1f}г / TTL: {ttl_h:.0f}г"
        )
    await message.answer("\n".join(lines), reply_markup=back_to_main_kb())


# ── /trades (АКТИВНІ ТОРГОВІ СЕСІЇ) ──────────────────────────────────────
@router.message(Command("trades"))
async def cmd_trades(message: Message) -> None:
    if not _db:
        return await message.answer("❌ БД не підключена.")

    rows = await _db.get_recent_trade_sessions(limit=5)
    if not rows:
        return await message.answer("📭 Немає активних торгових сесій.")

    lines = ["📊 <b>Останні торгові сесії:</b>\n"]
    for row in rows:
        lines.append(
            f"🔹 <b>#{row['id']}</b> | <code>{row['strategy']}</code> | "
            f"Статус: <b>{row['session_status']}</b> "
            f"(Fee: {row['network_fee']}$, Профіт: {row['gross_profit']:.0f} ₴)"
        )
    await message.answer("\n".join(lines), reply_markup=back_to_main_kb())


# ── /active (ПОКАЗАТИ ВСІ ПОТОЧНІ СПРЕДИ) ──────────────────────────────────
_active_cooldowns: dict[int, float] = {}

@router.message(Command("active"))
async def cmd_active(message: Message) -> None:
    import time
    now_ts = time.time()
    user_id = message.from_user.id
    last_ts = _active_cooldowns.get(user_id, 0.0)
    if now_ts - last_ts < 5.0:
        await message.answer(
            "⏳ <b>Занадто часто!</b> Будь ласка, зачекайте 5 секунд перед наступним викликом /active."
        )
        return
    _active_cooldowns[user_id] = now_ts

    from state import state as app_state
    from copy import copy
    from config.defaults import MIN_ORDERS as _DEF_ORDERS, MIN_COMPLETION as _DEF_RATE

    def _matches_user_filters(user_row: dict, spread_alert) -> tuple[bool, str]:
        """Same filter chain as scanner dispatch to keep /active consistent with auto-push."""
        mode_val = user_row.get("scanner_mode", "SPREAD")
        if mode_val != "SPREAD":
            return False, f"mode={mode_val}"

        entry_val = float(getattr(spread_alert, "deal_amount_uah", 0.0))
        capital_val = float(user_row.get("capital", 0.0))
        if entry_val > capital_val:
            return False, f"entry {entry_val:.0f} > capital {capital_val:.0f}"

        min_amount_val = float(user_row.get("min_amount", 0.0))
        if min_amount_val > 0 and entry_val < min_amount_val:
            return False, f"entry {entry_val:.0f} < min_amount {min_amount_val:.0f}"

        spread_val = float(getattr(spread_alert, "spread_pct", 0.0))
        user_min_spread = float(user_row.get("min_spread", 0.0))
        if spread_val < user_min_spread:
            return False, f"spread {spread_val:.2f}% < min {user_min_spread:.2f}%"

        user_buy_banks = set(user_row.get("buy_bank_codes") or user_row.get("bank_codes") or [])
        user_sell_banks = set(user_row.get("sell_bank_codes") or user_row.get("bank_codes") or [])
        opp_buy_banks = set(getattr(spread_alert, "buy_banks_fit", None) or [])
        opp_sell_banks = set(getattr(spread_alert, "sell_banks_fit", None) or [])
        if not (opp_buy_banks & user_buy_banks):
            return False, "buy banks no match"
        if not (opp_sell_banks & user_sell_banks):
            return False, "sell banks no match"

        merchant_filters = user_row.get("merchant_filters") or {}
        ex_merchant_filters = user_row.get("exchange_merchant_filters") or {}
        buy_order = getattr(spread_alert, "buy_order", None)
        sell_order = getattr(spread_alert, "sell_order", None)
        if not buy_order or not sell_order:
            return False, "missing orders"

        if "BLOCK" in (getattr(buy_order, "risk_flag", "") or ""):
            return False, "buy order blocked"
        if "BLOCK" in (getattr(sell_order, "risk_flag", "") or ""):
            return False, "sell order blocked"

        filter_fop = user_row.get("filter_fop_tov", "hide")
        filter_banka = user_row.get("filter_banka_jar", "hide")

        for order_obj in (buy_order, sell_order):
            risk_flags = getattr(order_obj, "risk_flag", "") or ""
            if filter_fop == "hide" and "FOP_TOV_BLOCKED" in risk_flags:
                return False, "FOP_TOV blocked for user"
            if filter_banka == "hide" and "BANKA_JAR_BLOCKED" in risk_flags:
                return False, "Banka/Jar blocked for user"
            ex_name = getattr(order_obj, "exchange", "")
            side_label = "buy" if order_obj is buy_order else "sell"
            ex_filters = ex_merchant_filters.get(ex_name, {})
            min_orders = float(
                ex_filters.get("min_orders", 0)
                or merchant_filters.get("min_orders", 0)
                or _DEF_ORDERS.get(ex_name, 0)
            )
            min_rate = float(
                ex_filters.get("min_rate", 0.0)
                or merchant_filters.get("min_rate", 0.0)
                or _DEF_RATE.get(ex_name, 0.0)
            )
            # Per-exchange subsidy filter: hide only used subsidies
            used_subs = user_row.get("_used_subsidies", {})
            if getattr(order_obj, "is_new_user_subsidy", False):
                ex_used = used_subs.get(ex_name, [])
                if "new_user" in ex_used:
                    return False, f"{side_label} new user subsidy already used on {ex_name}"

            if min_orders > 0 and getattr(order_obj, "month_order_count", 0) < min_orders:
                return False, f"{side_label} merchant orders < {min_orders:.0f}"
            if min_rate > 0 and getattr(order_obj, "finish_rate_pct", 0.0) < min_rate:
                return False, f"{side_label} merchant rate < {min_rate:.1f}%"

        return True, ""

    mode = "SPREAD"
    user = None
    if _db:
        users = await _db.get_active_users()
        user = next((u for u in users if u["user_id"] == message.from_user.id), None)
        if user:
            mode = user.get("scanner_mode", "SPREAD")

    if mode in ("TAKER_BUY", "TAKER_SELL"):
        if not _notifier:
            return await message.answer("❌ Нотифікатор не ініціалізований.")
        last_buy = getattr(app_state, "last_buy_grouped", {})
        last_sell = getattr(app_state, "last_sell_grouped", {})
        if not last_buy and not last_sell:
            return await message.answer(
                "📭 <b>Сканер ще не зібрав дані стаканів.</b>\n\nЗачекайте кілька секунд і спробуйте знову."
            )

        from core.engine.taker_scanner import TakerScanner
        user_full = await _db.get_user_by_id(message.from_user.id)
        if not user_full:
            return await message.answer("❌ Не вдалося завантажити ваші налаштування з БД.")

        taker_scanner = TakerScanner(_db)
        t_orders = await taker_scanner.find_orders_for_user(user_full, last_buy, last_sell)

        if t_orders:
            from bot.handlers.core import _maker_monitor_impl
            if _maker_monitor_impl and _maker_monitor_impl._risk_engine:
                try:
                    await _maker_monitor_impl._risk_engine.analyze_for_spread(t_orders)
                except Exception as re_err:
                    logger.error("Error analyzing manual Taker orders in RiskEngine: %s", re_err)

            # Фільтруємо ордери після аналізу відповідно до особистих налаштувань
            filter_fop = user_full.get("filter_fop_tov", "hide")
            filter_banka = user_full.get("filter_banka_jar", "hide")
            
            filtered = []
            for o in t_orders:
                risk_flags = getattr(o, "risk_flag", "") or ""
                if filter_fop == "hide" and "FOP_TOV_BLOCKED" in risk_flags:
                    continue
                if filter_banka == "hide" and "BANKA_JAR_BLOCKED" in risk_flags:
                    continue
                if "BLOCK" in risk_flags:
                    continue
                filtered.append(o)
            t_orders = filtered

        if not t_orders:
            return await message.answer(
                f"📭 <b>Немає активних ордерів для режиму {mode}</b>\n\n"
                "Сканер працює, але наразі немає пропозицій, що відповідають вашим фільтрам (сума, банки, ціна тощо)."
            )

        count = min(len(t_orders), 10)
        await message.answer(
            f"📡 <b>АКТИВНІ ОРДЕРИ ({mode}): {len(t_orders)} шт.</b>\n"
            f"Відправляю топ-{count}..."
        )

        await _notifier.send_taker_to_user(message.chat.id, t_orders, mode)
        return

    if mode != "SPREAD":
        return await message.answer(
            f"🔄 <b>Твій поточний режим: {mode}</b>\n\n"
            "В цьому режимі результати сканування надсилаються негайно як тільки з'являються вигідні пропозиції.\n"
            "Щоб повернутись до загального списку спредів, зміни режим на SPREAD через /mode."
        )

    alerts = getattr(app_state, "current_alerts", [])
    if user:
        # Preload used subsidies for per-exchange filtering
        if _db:
            user["_used_subsidies"] = await _db.get_used_subsidies(user.get("user_id", 0))
        alerts = [a for a in alerts if _matches_user_filters(user, a)[0]]
    if not alerts:
        return await message.answer(
            "📭 <b>Зараз активних спредів для твоїх фільтрів немає</b>\n\n"
            "Сканер працює, але поки не знайшов підходящих зв'язок під твої банки/капітал/фільтри.\n"
            "Спробуй пізніше або перевір /status"
        )

    if not _notifier:
        return await message.answer("❌ Нотифікатор не ініціалізований.")

    # Сортуємо по спреду (найвигідніші першими)
    sorted_alerts = sorted(alerts, key=lambda a: a.spread_pct, reverse=True)
    chat_id = message.chat.id
    count = min(len(sorted_alerts), 10)

    await message.answer(
        f"📡 <b>АКТИВНІ СПРЕДИ: {len(sorted_alerts)} шт.</b>\n"
        f"Відправляю топ-{count} зв'язок…"
    )

    sent = 0
    display_settings = await _db.get_user_display_settings(chat_id) if _db else {}
    group_active = display_settings.get("group_active_alerts", True)

    if group_active and len(sorted_alerts) > 1:
        batch_to_send = []
        for a in sorted_alerts[:count]:
            fresh = copy(a)
            if _db:
                b_rec, _, b_reason, _, _ = await _db.get_trade_recommendation_full(
                    a.buy_order.exchange, a.buy_order.merchant_id
                )
                s_rec, _, s_reason, _, _ = await _db.get_trade_recommendation_full(
                    a.sell_order.exchange, a.sell_order.merchant_id
                )
                fresh.buy_rec = b_rec
                fresh.sell_rec = s_rec
                fresh.buy_reason = b_reason
                fresh.sell_reason = s_reason

                from core.engine.alert_dispatcher import AlertDispatcher
                chosen_buy, chosen_sell = await AlertDispatcher.adapt_alert_for_user(_db, user, fresh)
                fresh.buy_bank = chosen_buy
                fresh.sell_bank = chosen_sell
            batch_to_send.append(fresh)

        try:
            await _notifier.send_batch_to_user(chat_id, batch_to_send)
            sent = len(batch_to_send)
        except Exception as e:
            logger.error("cmd_active send_batch error: %s", e)
            await message.answer("⚠️ Помилка при відправці списку активних спредів.")
            return
    else:
        for a in sorted_alerts[:count]:
            try:
                # 🔄 Re-fetch LLM verdicts from DB (можуть бути оновлені після створення алерту)
                fresh = copy(a)
                if _db:
                    b_rec, _, b_reason, _, _ = await _db.get_trade_recommendation_full(
                        a.buy_order.exchange, a.buy_order.merchant_id
                    )
                    s_rec, _, s_reason, _, _ = await _db.get_trade_recommendation_full(
                        a.sell_order.exchange, a.sell_order.merchant_id
                    )
                    fresh.buy_rec = b_rec
                    fresh.sell_rec = s_rec
                    fresh.buy_reason = b_reason
                    fresh.sell_reason = s_reason

                if user and _db:
                    from core.engine.alert_dispatcher import AlertDispatcher
                    chosen_buy, chosen_sell = await AlertDispatcher.adapt_alert_for_user(_db, user, fresh)
                    fresh.buy_bank = chosen_buy
                    fresh.sell_bank = chosen_sell

                await _notifier.send_to_user(chat_id, fresh)
                sent += 1
            except Exception as e:
                logger.error("cmd_active send error: %s", e)
                break

    if sent < count:
        await message.answer(f"⚠️ Відправлено {sent}/{count} (помилка при відправці)")


# ── /status ────────────────────────────────────────────────────────────────
@router.message(Command("status"))
async def cmd_status(message: Message) -> None:
    if not _db:
        return await message.answer("❌ База даних не ініціалізована")

    # 🚀 ФІКС: Перевіряємо ОСОБИСТІ ключі юзера з БД
    my_creds = await _db.get_all_credentials(user_id=message.from_user.id)
    connected = []
    for ex in ["Binance", "Bybit", "OKX", "MEXC", "Wallet", "BingX"]:
        if ex in my_creds:
            connected.append(f"✅ {ex}")
        else:
            connected.append(f"❌ {ex} (відсутні)")

    # Персональні параметри з scanner_users
    _my_capital = settings.working_capital_uah
    _my_spread = settings.min_spread_pct
    _my_min_amount = 0.0
    active_users = await _db.get_active_users()
    for u in active_users:
        if u["user_id"] == message.from_user.id:
            _my_capital = float(u["capital"])
            _my_spread = float(u["min_spread"])
            _my_min_amount = float(u.get("min_amount", 0.0))
            break

    # Глобальні системні параметри
    capital = _my_capital
    spread = _my_spread
    risk = runtime_config.get("risk_mode", settings.risk_mode)
    min_amount_line = f"\n📦 Мін. сума: <code>{_my_min_amount:.0f} ₴</code>" if _my_min_amount > 0 else ""

    llm_q = _scanner_stats.get("llm_queue", 0)
    rev_q = _scanner_stats.get("review_queue", 0)
    cb_st = _scanner_stats.get("cb_status", {})
    _cb_icons = {"CLOSED": "🟢", "OPEN": "🔴", "HALF_OPEN": "🟡", "DISABLED": "⏸"}
    cb_lines = [f"  {_cb_icons.get(v, '⚪')} {k}: {v}" for k, v in cb_st.items()]
    import time as _t
    mute_left = max(0, _mute_until - _t.monotonic())
    mute_line = f"\n🔕 Пауза: <b>{mute_left / 3600:.1f} год</b>" if mute_left > 0 else ""

    internet_status = "🟢 OK" if _scanner_stats.get("internet_connected", True) else "🔴 ВІДСУТНІЙ"

    text = (
            "📊 <b>Стан системного сканера</b>\n\n"
            f"🌐 Інтернет: <b>{internet_status}</b>\n"
            f"⚡ Останній цикл: <code>{_scanner_stats.get('last_cycle_ms', 0):.0f}ms</code>\n"
            f"🔄 Циклів всього: <code>{_scanner_stats.get('cycles', 0)}</code>\n"
            f"🤖 Ботів сьогодні: <code>{_scanner_stats.get('bots_detected_today', 0)}</code>\n"
            f"📈 Спредів сьогодні: <code>{_scanner_stats.get('spreads_found_today', 0)}</code>\n"
            f"🧠 LLM черга: <code>{llm_q}</code>  📋 Reviews: <code>{rev_q}</code>"
            f"{mute_line}\n\n"
            f"💼 Капітал: <code>{capital} ₴</code>\n"
            f"📉 Спред: <code>{spread}%</code>\n"
            f"{min_amount_line}"
            f"🛡 Ризик: <code>{risk}</code>\n\n"
            "🔌 <b>API:</b>\n" + "\n".join(connected) +
            ("\n\n⚡ <b>Circuit Breakers:</b>\n" + "\n".join(cb_lines) if cb_lines else "")
    )
    await message.answer(text)


# ── /users (ТІЛЬКИ АДМІН) ──────────────────────────────────────────────────
@router.message(Command("users"))
async def cmd_users(message: Message) -> None:
    # Захист: пускаємо тільки адміна
    if not _is_admin(message.from_user.id):
        return await message.answer("⛔ Ця команда доступна лише адміністратору.")

    if not _db:
        return await message.answer("❌ База даних недоступна.")

    conn = getattr(_db, "db", None) or getattr(_db, "_db", _db)
    if not conn:
        return await message.answer("❌ З'єднання з БД відсутнє.")

    # Дістаємо всіх юзерів з таблиці
    async with conn.execute(
            "SELECT user_id, telegram_chat_id, working_capital, is_alerts_active FROM scanner_users"
    ) as cur:
        rows = await cur.fetchall()

    if not rows:
        return await message.answer("👥 У базі ще немає зареєстрованих користувачів.")

    lines = ["👥 <b>Користувачі сканера:</b>\n"]
    active_count = 0

    for row in rows:
        uid = row[0]
        cap = float(row[2])
        is_active = bool(row[3])

        if is_active:
            status = "🟢 Активний"
            active_count += 1
        else:
            status = "🔴 Пауза"

        admin_mark = " 👑 (Адмін)" if _is_admin(uid) else ""
        lines.append(f"👤 <code>{uid}</code>{admin_mark}\n ├ Статус: {status}\n └ Капітал: {cap:.0f} ₴\n")

    lines.append(f"📊 Всього: <b>{len(rows)}</b> | З увімкненими алертами: <b>{active_count}</b>")

    await message.answer("\n".join(lines))


# ═══════════════════════════════════════════════════════
# 🔍 /debugfilters — admin-only діагностика розсилки
# ═══════════════════════════════════════════════════════

@router.message(Command("debugfilters"))
async def cmd_debugfilters(message: Message) -> None:
    """Показує фільтри кожного юзера + симулює dispatch на поточних алертах."""
    import html as _html
    if not _is_admin(message.from_user.id):
        return await message.answer("⛔ Тільки для адміна.")
    if not _db:
        return await message.answer("❌ БД недоступна.")

    from config.defaults import MIN_ORDERS as DEF_ORDERS, MIN_COMPLETION as DEF_RATE
    from config.banks import BANK_NAMES

    users = await _db.get_active_users()
    if not users:
        return await message.answer("Юзерів немає.")

    # ── Поточні алерти зі state ──
    try:
        from state import state as appstate
        raw_alerts = getattr(appstate, "current_alerts", [])
        alerts = [a[0] if isinstance(a, (list, tuple)) else a for a in raw_alerts]
    except Exception:
        alerts = []

    lines = ["🔍 <b>Debug Filters</b>\n"]

    for u in users:
        uid = u.get("user_id") or u.get("userid") or "?"
        mode = u.get("scanner_mode", "SPREAD")
        capital = float(u.get("capital", 0))
        cap_mode = u.get("capital_mode", "manual")
        card_settings = await _db.get_user_card_settings(u["user_id"])
        card_module_enabled = card_settings and card_settings.get("card_module_mode") != "off"
        auto_cap = await _db.get_user_auto_capital(u["user_id"])
        
        if cap_mode == "auto":
            capital = auto_cap
            capital_str = f"{capital:.0f} (Авто)"
        else:
            if card_module_enabled and auto_cap > 0 and auto_cap < capital:
                capital = auto_cap
                capital_str = f"{capital:.0f} (Обмеж. карт.)"
            else:
                capital_str = f"{capital:.0f}"
        minamount = float(u.get("min_amount", 0))
        minspread = float(u.get("min_spread", 0))
        is_active = bool(u.get("is_alerts_active", 1))
        # Нормалізуємо так само як AlertDispatcher._clean_and_normalize_banks
        from scanner import AlertDispatcher as _AD
        buy_banks = _AD._clean_and_normalize_banks(u.get("buy_bank_codes") or u.get("bank_codes"))
        sell_banks = _AD._clean_and_normalize_banks(u.get("sell_bank_codes") or u.get("bank_codes"))
        mf = u.get("merchant_filters") or {}
        emf = u.get("exchange_merchant_filters") or {}

        buy_bank_names = [BANK_NAMES.get(str(b), str(b)) for b in buy_banks]
        sell_bank_names = [BANK_NAMES.get(str(b), str(b)) for b in sell_banks]

        lines.append(f"👤 <code>{uid}</code>  {'✅' if is_active else '🔕 ВИМКНЕНО'}")
        lines.append(f"  режим:      <b>{mode}</b>")
        lines.append(f"  капітал:    <b>{capital_str} ₴</b>")
        lines.append(f"  мін.сума:   <b>{minamount:.0f} ₴</b>" if minamount > 0 else "  мін.сума:   вимкнено")
        lines.append(f"  мін.спред:  <b>{minspread:.2f}%</b>")
        lines.append(f"  buy банки:  {', '.join(buy_bank_names) if buy_bank_names else '⚠️ ПОРОЖНЬО'}")
        lines.append(f"  sell банки: {', '.join(sell_bank_names) if sell_bank_names else '⚠️ ПОРОЖНЬО'}")

        if mf:
            lines.append(f"  mf глобал:  ордери≥{mf.get('min_orders', 0):.0f}  рейт≥{mf.get('min_rate', 0):.0f}%  офлайн≤{mf.get('max_offline_mins', 0):.0f}хв")
        if emf:
            for ex, ef in emf.items():
                lines.append(f"  mf {ex}: ордери≥{ef.get('min_orders', 0):.0f}  рейт≥{ef.get('min_rate', 0):.0f}%  офлайн≤{ef.get('max_offline_mins', 0):.0f}хв")

        # ── Симуляція фільтру на кожному поточному алерті ──
        if mode != "SPREAD":
            lines.append(f"  <i>⏭ Пропускаємо симуляцію — не SPREAD режим</i>")
            lines.append("")
            continue

        if not alerts:
            lines.append("  <i>ℹ️ Поточних алертів у state немає</i>")
            lines.append("")
            continue

        failed_reasons: dict[str, int] = {}
        passed = 0

        for alert in alerts:
            entry = float(getattr(alert, "deal_amount_uah", 0))
            spread = float(getattr(alert, "spread_pct", 0))
            ob = _AD._clean_and_normalize_banks(getattr(alert, "buy_banks_fit", None) or [])
            os_ = _AD._clean_and_normalize_banks(getattr(alert, "sell_banks_fit", None) or [])
            bo = getattr(alert, "buy_order", None)
            so = getattr(alert, "sell_order", None)

            if entry > capital:
                failed_reasons["entry > capital"] = failed_reasons.get("entry > capital", 0) + 1
                continue
            if minamount > 0 and entry < minamount:
                failed_reasons["entry < min_amount"] = failed_reasons.get("entry < min_amount", 0) + 1
                continue
            if spread < minspread:
                failed_reasons[f"spread {spread:.2f}% < min {minspread:.2f}%"] = failed_reasons.get(
                    f"spread {spread:.2f}% < min {minspread:.2f}%", 0) + 1
                continue
            if not (ob & buy_banks):
                failed_reasons["buy banks no match"] = failed_reasons.get("buy banks no match", 0) + 1
                continue
            if not (os_ & sell_banks):
                failed_reasons["sell banks no match"] = failed_reasons.get("sell banks no match", 0) + 1
                continue
            if not bo or not so:
                failed_reasons["missing orders obj"] = failed_reasons.get("missing orders obj", 0) + 1
                continue

            # merchant filters
            mf_fail = False
            for side_label, order_obj in [("buy", bo), ("sell", so)]:
                ex_name = getattr(order_obj, "exchange", "")
                ex_filt = emf.get(ex_name, {})
                min_ord = float(ex_filt.get("min_orders", 0) or mf.get("min_orders", 0) or DEF_ORDERS.get(ex_name, 0))
                min_rate = float(ex_filt.get("min_rate", 0.0) or mf.get("min_rate", 0.0) or DEF_RATE.get(ex_name, 0.0))
                max_off = int(ex_filt.get("max_offline_mins") or mf.get("max_offline_mins") or 0)

                if min_ord > 0 and getattr(order_obj, "month_order_count", 0) < min_ord:
                    r = f"{side_label} merchant orders < {min_ord:.0f}"
                    failed_reasons[r] = failed_reasons.get(r, 0) + 1
                    mf_fail = True
                    break
                if min_rate > 0 and getattr(order_obj, "finish_rate_pct", 0.0) < min_rate:
                    r = f"{side_label} merchant rate < {min_rate:.1f}%"
                    failed_reasons[r] = failed_reasons.get(r, 0) + 1
                    mf_fail = True
                    break
                if max_off > 0:
                    last_online = getattr(order_obj, "last_online_mins", None)
                    if last_online is not None and last_online > max_off:
                        r = f"{side_label} merchant offline {last_online}m > {max_off}m"
                        failed_reasons[r] = failed_reasons.get(r, 0) + 1
                        mf_fail = True
                        break
            if mf_fail:
                continue

            passed += 1

        total = len(alerts)
        if passed > 0:
            lines.append(f"  ✅ Пройшли фільтр: <b>{passed}/{total}</b> алертів")
        else:
            lines.append(f"  ❌ Жоден алерт не пройшов ({total} перевірено)")
            for reason, cnt in sorted(failed_reasons.items(), key=lambda x: -x[1]):
                lines.append(f"    └ <code>{_html.escape(reason)}</code> × {cnt}")

            # ── Детальний дамп першого алерту ──
            a0 = alerts[0]
            e0 = float(getattr(a0, "deal_amount_uah", 0))
            s0 = float(getattr(a0, "spread_pct", 0))
            ob0 = set(getattr(a0, "buy_banks_fit", None) or [])
            os0 = set(getattr(a0, "sell_banks_fit", None) or [])
            lines.append(f"\n  📋 <i>Перший алерт:</i>")
            lines.append(f"    deal_amount_uah = <b>{e0:.0f} ₴</b>  (capital = {capital:.0f} ₴)")
            lines.append(f"    spread_pct = <b>{s0:.2f}%</b>  (min = {minspread:.2f}%)")
            lines.append(f"    buy_banks_fit  = <code>{_html.escape(str(ob0 or 'ПОРОЖНЬО'))}</code>")
            lines.append(f"    sell_banks_fit = <code>{_html.escape(str(os0 or 'ПОРОЖНЬО'))}</code>")
            lines.append(f"    user buy_banks  = <code>{_html.escape(str(buy_banks or 'ПОРОЖНЬО'))}</code>")
            lines.append(f"    user sell_banks = <code>{_html.escape(str(sell_banks or 'ПОРОЖНЬО'))}</code>")

        lines.append("")

    text = "\n".join(lines)
    # Telegram обмеження — ріжемо якщо > 4096
    for chunk in [text[i:i + 4096] for i in range(0, len(text), 4096)]:
        await message.answer(chunk, parse_mode="HTML")


# ── /keys ──────────────────────────────────────────────────────────────────
