# bot/commands.py
"""
Telegram Command Center — команди бота (Multi-user + UI/UX версія).

Команди:
  /start      — привітання та Дашборд з кнопками
  /status     — стан системного сканера (цикл, черги, боти)
  /connect    — підключення персональних API ключів біржі
  /disconnect — видалення персональних API ключів
  /keys       — список підключених персональних бірж
  /balance    — баланс на всіх персональних підключених біржах
  /settings   — перегляд і зміна глобальних налаштувань
  /ban        — ручний бан мерчанта
  /help       — список команд
"""
from __future__ import annotations

import asyncio
import logging
from typing import TYPE_CHECKING, Optional
from contextlib import suppress
from aiogram.exceptions import TelegramBadRequest
from aiogram import Router, F
from aiogram.filters import Command
from aiogram.types import Message, InlineKeyboardMarkup, InlineKeyboardButton, CallbackQuery
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup

from bot import keyboards
from config import settings

_trade_worker = None
_single_leg_executor = None
_maker_monitor = None
_active_repricers: dict = {}  # ad_id → asyncio.Task (AdRepricer)

# ── Кеші для Single-Leg / Spread кнопок (заповнюються з notifier.py) ──────
_single_leg_cache: dict = {}  # "b:<key>" / "s:<key>" → {ad_id, exchange, price, ...}
_spread_cache: dict = {}  # "<key>" → (SpreadAlert, timestamp)

# 🧠 РЕЄСТР ЕКСПЕРИМЕНТАЛЬНИХ ФІЧ (Для легкого масштабування)
EXPERIMENTAL_FEATURES = {
    "routing": {
        "title": "🔀 МАРШРУТИЗАЦІЯ",
        "features": {
            "hybrid_routes": {
                "name": "Гібридні маршрути (T→M / M→T)",
                "desc": "Дозволяє запускати змішані стратегії Taker-Maker або Maker-Taker прямо з алертів сканера. Бот автоматично виставить мейкер-оголошення на потрібній біржі."
            }
        }
    },
    "analytics": {
        "title": "📈 АНАЛІТИКА ТА СПРЕДИ",
        "features": {
            "asymmetric_spread": {
                "name": "Асиметричний спред (Inventory)",
                "desc": "Сканер підтягуватиме суму закупівлі до мінімального ліміту BUY-мерчанта, якщо дзеркальний об'єм замалий. Частина крипти буде продана одразу з шаленим профітом, а залишок осяде у твоєму інвентарі за супер-дешевою ціною закупівлі."
            }
        }
    }
}
# ── Admin helper ───────────────────────────────────────────────────────────
def _is_admin(user_id: int) -> bool:
    """Перевіряє чи юзер є адміном (ADMIN_ID в .env)."""
    admin_id = getattr(settings, "admin_id", 0)
    # Fallback: якщо ADMIN_ID не встановлено — вважаємо адміном TELEGRAM_CHAT_ID
    if not admin_id:
        admin_id = getattr(settings, "telegram_chat_id", 0)
    return user_id == admin_id


from config.runtime import runtime_config, ALLOWED_KEYS
from bot.keyboards import (
    main_menu_kb, settings_menu_kb, keys_menu_kb,
    back_to_main_kb, global_settings_kb, back_to_settings_kb,
    banks_selection_kb, back_to_keys_kb, exchange_connect_kb,
    stats_overview_kb, back_to_stats_kb,
    exchanges_status_kb, exchange_toggle_kb, exchange_cooldown_kb,
    exchange_down_kb, back_to_status_kb, display_settings_kb,
)
from config.banks import BANK_NAMES, DEFAULT_BANK_CODES

# Імпортуємо клієнти для динамічної перевірки балансів
from infrastructure.api.binance_account import BinanceAccountClient
from infrastructure.api.bybit_account import BybitAccountClient
from infrastructure.api.okx_account import OKXAccountClient
from infrastructure.api.mexc_account import MEXCAccountClient

if TYPE_CHECKING:
    from core.storage.merchant_db import MerchantDB

logger = logging.getLogger("Commands")

router = Router()


# ── FSM стани ──────────────────────────────────────────────────────────────
class ConnectStates(StatesGroup):
    waiting_exchange = State()
    waiting_api_key = State()
    waiting_api_secret = State()
    waiting_passphrase = State()


class SettingStates(StatesGroup):
    waiting_capital = State()
    waiting_min_amount = State()
    waiting_spread = State()


class GlobalSettingStates(StatesGroup):
    waiting_value = State()


class MerchantFilterStates(StatesGroup):
    waiting_min_orders = State()
    waiting_min_rate = State()
    waiting_ex_min_orders = State()
    waiting_ex_min_rate = State()


class ExchangeCooldownStates(StatesGroup):
    waiting_hours = State()


class PriceRangeStates(StatesGroup):
    waiting_value = State()
    waiting_range_min = State()
    waiting_range_max = State()


class CreateAdStates(StatesGroup):
    waiting_exchange = State()
    waiting_side = State()
    waiting_price = State()
    waiting_amount = State()
    waiting_min_limit = State()
    waiting_max_limit = State()
    waiting_banks = State()
    waiting_terms = State()
    waiting_confirm = State()


class TakerExecuteStates(StatesGroup):
    waiting_amount = State()


class HybridTradeStates(StatesGroup):
    waiting_tm_amount = State()
    waiting_mt_amount = State()
    waiting_confirm = State()


class MakerSettingsStates(StatesGroup):
    waiting_buy_price = State()
    waiting_target_margin = State()


class TakerBuySettingsStates(StatesGroup):
    waiting_amount          = State()  # 1 — обʼєм USDT
    waiting_price_strategy  = State()  # 2 — стратегія ціни (inline-меню)
    waiting_price_to        = State()  # 3 — max/exact ціна (або range-max)
    waiting_price_from      = State()  # 3b — range-min ціна
    waiting_limit_min       = State()  # 4 — мін ліміт UAH
    waiting_limit_max       = State()  # 4b — макс ліміт UAH
    waiting_banks           = State()  # 5 — банки (inline-чекбокси)
    waiting_speed           = State()  # 6 — швидкість

class TakerSellSettingsStates(StatesGroup):
    waiting_amount    = State()   # крок 1 — об'єм
    waiting_buy_price = State()   # крок 2 — ціна купівлі
    waiting_exchange  = State()   # крок 3 — біржа (Network Fee)
    waiting_profit    = State()   # крок 4 — спред % (з breakeven підказкою)
    waiting_speed     = State()   # крок 5 — час важливий?

class SniperStates(StatesGroup):
    waiting_exchange = State()
    waiting_direction = State()
    waiting_min_spread = State()
    waiting_min_volume = State()

class CardAddStates(StatesGroup):
    waiting_bank = State()
    waiting_card_number = State()
    waiting_balance = State()
    waiting_label = State()
    waiting_is_own = State()

class CardUpdateStates(StatesGroup):
    waiting_true_balance = State()

class CardEditStates(StatesGroup):
    waiting_label = State()
    waiting_note = State()

class MonoStates(StatesGroup):
    waiting_token = State()

class BankLimitStates(StatesGroup):
    waiting_value = State()

class CardLimitStates(StatesGroup):
    waiting_value = State()

# ── Словник описів для UI ──────────────────────────────────────────────────
# Тільки системні параметри (персональні - в scanner_users через меню)
SETTING_DESCRIPTIONS = {
    "risk_mode": "🛡 Рівень антифроду",
    "behavior_alert_score": "🤖 Поріг балів ботів",
    "velocity_spike_per_hour": "⚡ Аномальна швидкість (угод/год)",
    "sticky_min_chain": "📌 Липкі ліміти (циклів)",
    "review_ttl_hours": "💬 Кеш відгуків (годин)",
    "max_alerts_per_cycle": "🔔 Макс. алертів за цикл",
}

# Відповідність ключ → опис для кнопок
_KEY_LABELS = {
    "risk_mode": "🛡 Антифрод",
    "behavior_alert_score": "🤖 Поріг ботів",
    "velocity_spike_per_hour": "⚡ Швидкість",
    "sticky_min_chain": "📌 Липкі ліміти",
    "review_ttl_hours": "💬 Кеш відгуків",
    "max_alerts_per_cycle": "🔔 Макс. алертів",
}

# ── Посилання на глобальні об'єкти (заповнюються з scanner.py) ─────────────
_db: Optional["MerchantDB"] = None
_notifier = None
_account_clients: dict = {}
_scanner_stats: dict = {
    "cycles": 0,
    "last_cycle_ms": 0,
    "bots_detected_today": 0,
    "spreads_found_today": 0,
    "llm_queue": 0,
    "review_queue": 0,
    "cb_status": {},
}
_mute_until: float = 0.0


def is_muted() -> bool:
    """Перевіряє чи бот на паузі."""
    import time
    return time.monotonic() < _mute_until


def setup(db, account_clients: dict, trade_worker=None, notifier=None, single_leg_executor=None,
          maker_monitor=None) -> None:
    global _db, _account_clients, _trade_worker, _notifier, _single_leg_executor, _maker_monitor
    if _trade_worker and trade_worker and _trade_worker is not trade_worker:
        logger.warning("setup(): TradeWorker перезаписується!")
    _db = db
    _account_clients = account_clients
    _trade_worker = trade_worker
    if notifier is not None:
        _notifier = notifier
    if single_leg_executor is not None:
        _single_leg_executor = single_leg_executor
    if maker_monitor is not None:
        _maker_monitor = maker_monitor


def update_stats(**kwargs) -> None:
    _scanner_stats.update(kwargs)


# ── /start (ГОЛОВНИЙ ДАШБОРД) ──────────────────────────────────────────────
# ── /start (ЗАПУСК ПЕРСОНАЛЬНОГО СКАНЕРА ТА ДАШБОРД) ──────────────────────
# ── /start (ЗАПУСК ПЕРСОНАЛЬНОГО СКАНЕРА ТА ДАШБОРД) ──────────────────────
async def _generate_dashboard_text(user_id: int) -> tuple[str, bool]:
    user_capital = str(settings.working_capital_uah)
    user_min_amount = "без обмежень"
    user_spread = "0.50"
    if _db:
        active_users = await _db.get_active_users()
        for u in active_users:
            if u["user_id"] == user_id:
                user_capital = f"{u['capital']:.1f}"
                user_spread = f"{u['min_spread']:.2f}"
                _min_amt = float(u.get("min_amount") or 0.0)
                user_min_amount = f"{_min_amt:.0f} ₴" if _min_amt > 0 else "без обмежень"
                break

    is_active = runtime_config.get("is_scanner_active", "false") == "true"
    status_text = "🟢 <b>АКТИВНИЙ (Парсинг іде)</b>" if is_active else "🔴 <b>ЗУПИНЕНИЙ (Пауза)</b>"

    text = (
        "👋 <b>ARBIX QUANTUM | Особистий кабінет</b>\n\n"
        f"Статус ядра: {status_text}\n\n"
        "🛠 <b>Твої персональні фільтри:</b>\n"
        f"├ Капітал: <b>{user_capital} ₴</b>\n"
        f"├ Мін. сума угоди: <b>{user_min_amount}</b>\n"
        f"└ Мін. спред: <b>{user_spread}%</b>\n\n"
        "<i>👇 Використовуй меню нижче для управління:</i>"
    )
    return text, is_active


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
        "📖 <b>Детальна допомога</b>\n\n"
        "<b>/start</b>\nЗапустити персональний сканер.\n\n"
        "<b>/stop</b>\nЗупинити алерти.\n\n"
        "<b>/active</b>\nВсі активні спреди прямо зараз.\n\n"
        "<b>/stats</b>\nСтатистика торгівлі (прибуток, банки, дні).\n\n"
        "<b>/sessions</b>\nСтан auth-сесій (TTL, здоров'я).\n\n"
        "<b>/trades</b>\nАктивні торгові сесії.\n\n"
        "<b>/connect [біржа]</b>\nПідключити персональні API ключі.\n\n"
        "<b>/disconnect</b>\nВідключити біржу.\n\n"
        "<b>/balance</b>\nПоказує баланс на твоїх біржах.\n\n"
        "<b>/keys</b>\nСписок підключених API ключів.\n\n"
        "<b>/settings</b>\nЗмінити глобальні налаштування сканера.\n\n"
        "<b>/ban [exchange] [merchant_id]</b>\nРучний бан мерчанта.\n\n"
        "<b>/mode</b>\nПерегляд і зміна режиму сканування.\n\n"
        "<b>/status</b>\nПоточний стан сканера.\n\n"
        "<b>/debugfilters</b> — діагностика фільтрів розсилки (admin)\n"
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
    from core.analytics.stats_engine import StatsEngine
    stats = StatsEngine(_db)
    text = await stats.format_stats_message(period_days=30)
    text += "\n\n<i>👇 Оберіть розділ для деталей:</i>"
    await message.answer(text, reply_markup=stats_overview_kb())


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
@router.message(Command("active"))
async def cmd_active(message: Message) -> None:
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

        for order_obj in (buy_order, sell_order):
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

    if mode != "SPREAD":
        return await message.answer(
            f"🔄 <b>Твій поточний режим: {mode}</b>\n\n"
            "В цьому режимі результати сканування надсилаються негайно як тільки з'являються вигідні пропозиції.\n"
            "Щоб повернутись до загального списку спредів, зміни режим на SPREAD через /mode."
        )

    alerts = getattr(app_state, "current_alerts", [])
    if user:
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
    for ex in ["Binance", "Bybit", "OKX", "MEXC", "Wallet"]:
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

    text = (
            "📊 <b>Стан системного сканера</b>\n\n"
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
        mode      = u.get("scanner_mode", "SPREAD")
        capital   = float(u.get("capital", 0))
        minamount = float(u.get("min_amount", 0))
        minspread = float(u.get("min_spread", 0))
        is_active = bool(u.get("is_alerts_active", 1))
        buy_banks = set(u.get("buy_bank_codes") or u.get("bank_codes") or [])
        sell_banks = set(u.get("sell_bank_codes") or u.get("bank_codes") or [])
        mf  = u.get("merchant_filters") or {}
        emf = u.get("exchange_merchant_filters") or {}

        buy_bank_names  = [BANK_NAMES.get(str(b), str(b)) for b in buy_banks]
        sell_bank_names = [BANK_NAMES.get(str(b), str(b)) for b in sell_banks]

        lines.append(f"👤 <code>{uid}</code>  {'✅' if is_active else '🔕 ВИМКНЕНО'}")
        lines.append(f"  режим:      <b>{mode}</b>")
        lines.append(f"  капітал:    <b>{capital:.0f} ₴</b>")
        lines.append(f"  мін.сума:   <b>{minamount:.0f} ₴</b>" if minamount > 0 else "  мін.сума:   вимкнено")
        lines.append(f"  мін.спред:  <b>{minspread:.2f}%</b>")
        lines.append(f"  buy банки:  {', '.join(buy_bank_names) if buy_bank_names else '⚠️ ПОРОЖНЬО'}")
        lines.append(f"  sell банки: {', '.join(sell_bank_names) if sell_bank_names else '⚠️ ПОРОЖНЬО'}")

        if mf:
            lines.append(f"  mf глобал:  ордери≥{mf.get('min_orders',0):.0f}  рейт≥{mf.get('min_rate',0):.0f}%")
        if emf:
            for ex, ef in emf.items():
                lines.append(f"  mf {ex}: ордери≥{ef.get('min_orders',0):.0f}  рейт≥{ef.get('min_rate',0):.0f}%")

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
            entry  = float(getattr(alert, "deal_amount_uah", 0))
            spread = float(getattr(alert, "spread_pct", 0))
            ob     = set(getattr(alert, "buy_banks_fit", None) or [])
            os_    = set(getattr(alert, "sell_banks_fit", None) or [])
            bo     = getattr(alert, "buy_order", None)
            so     = getattr(alert, "sell_order", None)

            if entry > capital:
                failed_reasons["entry > capital"] = failed_reasons.get("entry > capital", 0) + 1
                continue
            if minamount > 0 and entry < minamount:
                failed_reasons["entry < min_amount"] = failed_reasons.get("entry < min_amount", 0) + 1
                continue
            if spread < minspread:
                failed_reasons[f"spread {spread:.2f}% < min {minspread:.2f}%"] = failed_reasons.get(f"spread {spread:.2f}% < min {minspread:.2f}%", 0) + 1
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
                ex_name   = getattr(order_obj, "exchange", "")
                ex_filt   = emf.get(ex_name, {})
                min_ord   = float(ex_filt.get("min_orders", 0) or mf.get("min_orders", 0) or DEF_ORDERS.get(ex_name, 0))
                min_rate  = float(ex_filt.get("min_rate", 0.0) or mf.get("min_rate", 0.0) or DEF_RATE.get(ex_name, 0.0))
                if min_ord > 0 and getattr(order_obj, "month_order_count", 0) < min_ord:
                    r = f"{side_label} merchant orders < {min_ord:.0f}"
                    failed_reasons[r] = failed_reasons.get(r, 0) + 1
                    mf_fail = True; break
                if min_rate > 0 and getattr(order_obj, "finish_rate_pct", 0.0) < min_rate:
                    r = f"{side_label} merchant rate < {min_rate:.1f}%"
                    failed_reasons[r] = failed_reasons.get(r, 0) + 1
                    mf_fail = True; break
            if mf_fail:
                continue

            passed += 1

        total = len(alerts)
        if passed > 0:
            lines.append(f"  ✅ Пройшли фільтр: <b>{passed}/{total}</b> алертів")
        else:
            lines.append(f"  ❌ Жоден алерт не пройшов ({total} перевірено)")
            for reason, cnt in sorted(failed_reasons.items(), key=lambda x: -x[1]):
                lines.append(f"    └ <code>{reason}</code> × {cnt}")

            # ── Детальний дамп першого алерту ──
            a0 = alerts[0]
            e0 = float(getattr(a0, "deal_amount_uah", 0))
            s0 = float(getattr(a0, "spread_pct", 0))
            ob0 = set(getattr(a0, "buy_banks_fit", None) or [])
            os0 = set(getattr(a0, "sell_banks_fit", None) or [])
            lines.append(f"\n  📋 <i>Перший алерт:</i>")
            lines.append(f"    deal_amount_uah = <b>{e0:.0f} ₴</b>  (capital = {capital:.0f} ₴)")
            lines.append(f"    spread_pct = <b>{s0:.2f}%</b>  (min = {minspread:.2f}%)")
            lines.append(f"    buy_banks_fit  = <code>{ob0 or 'ПОРОЖНЬО'}</code>")
            lines.append(f"    sell_banks_fit = <code>{os0 or 'ПОРОЖНЬО'}</code>")
            lines.append(f"    user buy_banks  = <code>{buy_banks or 'ПОРОЖНЬО'}</code>")
            lines.append(f"    user sell_banks = <code>{sell_banks or 'ПОРОЖНЬО'}</code>")

        lines.append("")

    text = "\n".join(lines)
    # Telegram обмеження — ріжемо якщо > 4096
    for chunk in [text[i:i+4096] for i in range(0, len(text), 4096)]:
        await message.answer(chunk, parse_mode="HTML")
# ── /keys ──────────────────────────────────────────────────────────────────
@router.message(Command("keys"))
async def cmd_keys(message: Message) -> None:
    if not _db:
        return await message.answer("❌ База даних не ініціалізована")

    all_creds = await _db.get_all_credentials(user_id=message.from_user.id)
    if not all_creds:
        return await message.answer("🔑 Жодних API ключів не підключено.\nВикористай /connect")

    lines = ["🔑 <b>Твої підключені API ключі:</b>\n"]
    for exchange, creds in all_creds.items():
        key_preview = creds["api_key"][:8] + "..." if creds["api_key"] else "?"
        lines.append(f"✅ <b>{exchange}</b>: <code>{key_preview}</code>")

    await message.answer("\n".join(lines))


# ── /connect & /disconnect ─────────────────────────────────────────────────
SUPPORTED_EXCHANGES = ["Binance", "Bybit", "OKX", "MEXC", "Wallet"]


@router.message(Command("connect"))
async def cmd_connect(message: Message, state: FSMContext) -> None:
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=ex, callback_data=f"connect:{ex}")] for ex in SUPPORTED_EXCHANGES
    ])
    await message.answer("🔌 Виберіть біржу для підключення своїх ключів:", reply_markup=kb)


@router.callback_query(lambda c: c.data and c.data.startswith("connect:"))
async def on_connect_exchange(call: CallbackQuery, state: FSMContext) -> None:
    exchange = call.data.split(":")[1]
    await state.update_data(exchange=exchange)
    if exchange == "Wallet":
        await state.set_state(ConnectStates.waiting_api_key)
        await call.message.edit_text(
            f"🔑 <b>Підключення {exchange}</b>\n\n"
            f"Введи <b>X-API-Key</b> від Wallet P2P:\n"
            f"<i>(Отримати: @wallet → P2P → Settings → API)</i>"
        )
    else:
        await state.set_state(ConnectStates.waiting_api_key)
        await call.message.edit_text(f"🔑 <b>Підключення {exchange}</b>\n\nВведи API Key:")
    await call.answer()


@router.message(ConnectStates.waiting_api_key)
async def on_api_key(message: Message, state: FSMContext) -> None:
    api_key = message.text.strip()
    with suppress(Exception):
        await message.delete()
    if len(api_key) < 10:
        return await message.answer("❌ API Key занадто короткий. Спробуй ще раз:")
    data = await state.get_data()
    exchange = data.get("exchange", "")

    # Wallet: тільки API Key, без Secret
    if exchange == "Wallet":
        await _save_credentials(message, state, exchange, api_key, api_secret="", passphrase="")
        return

    await state.update_data(api_key=api_key)
    await state.set_state(ConnectStates.waiting_api_secret)
    await message.answer("✅ API Key отримано.\n\nТепер введи <b>Secret Key</b>:")


@router.message(ConnectStates.waiting_api_secret)
async def on_api_secret(message: Message, state: FSMContext) -> None:
    api_secret = message.text.strip()
    with suppress(Exception):
        await message.delete()
    if len(api_secret) < 10:
        return await message.answer("❌ Secret Key занадто короткий. Спробуй ще раз:")

    data = await state.get_data()
    exchange = data["exchange"]

    if exchange == "OKX":
        await state.update_data(api_secret=api_secret)
        await state.set_state(ConnectStates.waiting_passphrase)
        return await message.answer("✅ Secret Key отримано.\n\nOKX вимагає <b>Passphrase</b>:")

    await _save_credentials(message, state, exchange, data["api_key"], api_secret)


@router.message(ConnectStates.waiting_passphrase)
async def on_passphrase(message: Message, state: FSMContext) -> None:
    passphrase = message.text.strip()
    with suppress(Exception): await message.delete()
    data = await state.get_data()
    await _save_credentials(message, state, data["exchange"], data["api_key"], data["api_secret"], passphrase)


async def _save_credentials(message: Message, state: FSMContext, exchange: str, api_key: str, api_secret: str,
                            passphrase: str = "") -> None:
    await state.clear()
    if not _db: return await message.answer("❌ База даних недоступна")
    ok = await _db.save_credentials(exchange=exchange, api_key=api_key, api_secret=api_secret, passphrase=passphrase,
                                    label="user_keys", user_id=message.from_user.id)
    if ok:
        await message.answer(f"✅ <b>{exchange}</b> успішно підключено!\nВикористай /balance щоб перевірити свої кошти.")
    else:
        await message.answer(f"❌ Помилка збереження credentials для {exchange}")


@router.message(Command("disconnect"))
async def cmd_disconnect(message: Message) -> None:
    if not _db: return await message.answer("❌ База даних не ініціалізована")
    all_creds = await _db.get_all_credentials(user_id=message.from_user.id)
    if not all_creds:
        return await message.answer("У тебе немає підключених бірж.")

    kb = InlineKeyboardMarkup(
        inline_keyboard=[[InlineKeyboardButton(text=f"❌ {ex}", callback_data=f"disconnect:{ex}")] for ex in all_creds])
    await message.answer("Виберіть біржу для відключення:", reply_markup=kb)


@router.callback_query(lambda c: c.data and c.data.startswith("disconnect:"))
async def on_disconnect(call: CallbackQuery) -> None:
    exchange = call.data.split(":")[1]
    await call.message.edit_reply_markup(reply_markup=None)
    if _db and await _db.delete_credentials(exchange, user_id=call.from_user.id):
        await call.message.answer(f"✅ {exchange} відключено.")
    await call.answer()


# ── ЛОГІКА БАЛАНСУ ─────────────────────────────────────────────────────────
async def _generate_balance_text(user_id: int) -> str:
    """
    Баланс з акаунт-клієнтів що вже підключені в scanner.py.
    Не створює нових клієнтів — використовує існуючі сесії.
    """
    if not _account_clients:
        return "❌ Акаунт клієнти не ініціалізовані"

    lines = ["💰 <b>Баланси на біржах</b>\n"]
    found_any = False

    for exchange, client in _account_clients.items():
        if not getattr(client, "is_authenticated", False):
            lines.append(f"⚪ <b>{exchange}</b>: не підключено (<code>/connect {exchange}</code>)")
            continue
        try:
            balances = await client.get_balance()
            if not balances:
                lines.append(f"📭 <b>{exchange}</b>: порожньо або 0")
                continue
            found_any = True
            lines.append(f"\n🏦 <b>{exchange}</b>:")
            for b in balances:
                coin = b.get("coin", "?")
                free = float(b.get("free", 0))
                total = float(b.get("total", free))
                locked = total - free
                if locked > 0:
                    lines.append(f"  {coin}: <code>{free:.2f}</code> (заблок: <code>{locked:.2f}</code>)")
                else:
                    lines.append(f"  {coin}: <code>{free:.2f}</code>")
        except Exception as e:
            lines.append(f"❌ <b>{exchange}</b>: {type(e).__name__}")

    if not found_any:
        lines.append("\n<i>Підключи API ключі через /connect</i>")
    return "\n".join(lines)


@router.message(Command("balance"))
async def cmd_balance(message: Message) -> None:
    msg = await message.answer("⏳ Завантажую баланси...")
    text = await _generate_balance_text(message.from_user.id)
    await msg.edit_text(text)


# ── /settings (ГЛОБАЛЬНІ НАЛАШТУВАННЯ З ВАЛІДАЦІЄЮ) ────────────────────────
def _generate_settings_text() -> str:
    lines = ["⚙️ <b>Системні налаштування</b>\n"]
    lines.append(
        "<i>Впливають на поведінку всього сканера. Особисті фільтри (капітал/спред/банки) — в головному меню.</i>\n")
    for key, desc in SETTING_DESCRIPTIONS.items():
        val = runtime_config.get(key, getattr(settings, key, "—"))
        lines.append(f"▫️ <b>{desc}</b>\n  └ <code>{key}</code>: <b>{val}</b>\n")
    lines.append("👇 <i>Обери параметр для зміни:</i>")
    return "\n".join(lines)


@router.message(Command("settings"))
async def cmd_settings(message: Message, state: FSMContext) -> None:
    await state.clear()
    if not _is_admin(message.from_user.id):
        return await message.answer("⛔ Глобальні налаштування доступні тільки адміну.")
    await message.answer(_generate_settings_text(), reply_markup=global_settings_kb(_KEY_LABELS))


@router.callback_query(F.data == "menu:global_settings")
async def on_global_settings_menu(call: CallbackQuery, state: FSMContext) -> None:
    if not _is_admin(call.from_user.id):
        await call.answer("⛔ Тільки адмін", show_alert=True)
        return
    await state.clear()
    with suppress(TelegramBadRequest):
        await call.message.edit_text(_generate_settings_text(), reply_markup=global_settings_kb(_KEY_LABELS))
    await call.answer()


@router.callback_query(F.data.startswith("gset:"))
async def on_gset_click(call: CallbackQuery, state: FSMContext) -> None:
    if not _is_admin(call.from_user.id):
        await call.answer("⛔ Тільки адмін", show_alert=True)
        return
    key = call.data.split(":", 1)[1]
    if key not in SETTING_DESCRIPTIONS:
        await call.answer("❌ Невідомий параметр", show_alert=True)
        return

    await state.update_data(setting_key=key)
    await state.set_state(GlobalSettingStates.waiting_value)

    val = runtime_config.get(key, getattr(settings, key, "—"))
    desc = SETTING_DESCRIPTIONS.get(key, key)

    _HINTS = {
        "velocity_spike_per_hour": "<i>Число з крапкою (напр. 20.0)</i>",
        "review_ttl_hours": "<i>Число з крапкою (напр. 24.0)</i>",
        "behavior_alert_score": "<i>Ціле число (напр. 60)</i>",
        "sticky_min_chain": "<i>Ціле число (напр. 3)</i>",
        "max_alerts_per_cycle": "<i>Ціле число (напр. 5)</i>",
        "risk_mode": "<i>STRICT, WARNING або RELAXED</i>",
    }
    hint = _HINTS.get(key, "<i>Введи нове значення</i>")
    text = f"✏️ <b>{desc}</b>\n<code>{key}</code>\n\nПоточне: <b>{val}</b>\n\n{hint}"
    with suppress(TelegramBadRequest):
        await call.message.edit_text(text, reply_markup=back_to_settings_kb())
    await call.answer()


@router.message(GlobalSettingStates.waiting_value)
async def on_gset_value_input(message: Message, state: FSMContext) -> None:
    data = await state.get_data()
    key = data.get("setting_key")
    if not key: return await state.clear()

    # 🚀 МАГІЯ: Автоматично міняємо кому на крапку і чистимо пробіли
    raw_value = message.text.strip().replace(",", ".")
    formatted_value = raw_value

    try:
        if key in ("velocity_spike_per_hour", "review_ttl_hours"):
            formatted_value = str(round(float(raw_value), 1))
        elif key in ("behavior_alert_score", "sticky_min_chain", "max_alerts_per_cycle"):
            formatted_value = str(int(float(raw_value)))
        elif key == "risk_mode":
            formatted_value = raw_value.upper()
            if formatted_value not in ("STRICT", "WARNING", "RELAXED"):
                raise ValueError("Допустимі тільки STRICT, WARNING, RELAXED")
        elif key not in SETTING_DESCRIPTIONS:
            raise ValueError(f"Параметр {key!r} не є глобальним налаштуванням")
    except ValueError as e:
        return await message.answer(
            f"❌ <b>Помилка!</b> {e}\n\nСпробуй ще раз:",
            reply_markup=back_to_settings_kb(),
        )

    ok = await runtime_config.set(key, formatted_value)
    if ok:
        await message.answer(f"✅ Параметр <code>{key}</code> успішно змінено на <b>{formatted_value}</b>!",
                             reply_markup=back_to_settings_kb())
    else:
        await message.answer(f"❌ Помилка БД при збереженні <code>{key}</code>.", reply_markup=back_to_settings_kb())
    await state.clear()


# ── ЗМІНА ПЕРСОНАЛЬНОГО КАПІТАЛУ ТА СПРЕДУ ────────────────────────────────
@router.callback_query(F.data == "set:capital")
async def on_set_capital(call: CallbackQuery, state: FSMContext) -> None:
    await state.set_state(SettingStates.waiting_capital)
    with suppress(TelegramBadRequest):
        await call.message.edit_text(
            "💵 <b>Введи твій персональний робочий капітал</b>\n\n"
            "<i>Автоматично конвертується у формат з крапкою (напр. 15000 -> 15000.0)</i>",
            reply_markup=back_to_main_kb()
        )
    await call.answer()


@router.message(SettingStates.waiting_capital)
async def on_capital_input(message: Message, state: FSMContext) -> None:
    try:
        # Автоматично прибираємо коми і конвертуємо
        val = float(message.text.strip().replace(",", "."))
        if _db:
            conn = getattr(_db, "db", None) or getattr(_db, "_db", _db)
            await conn.execute("UPDATE scanner_users SET working_capital = ? WHERE user_id = ?",
                               (val, message.from_user.id))
            await conn.commit()
        await message.answer(f"✅ Персональний капітал оновлено: <b>{val:.1f} ₴</b>", reply_markup=back_to_main_kb())
    except ValueError:
        await message.answer("❌ Формат невірний. Введи число (наприклад: 6000.0 або просто 6000)")
    finally:
        await state.clear()


@router.callback_query(F.data == "set:min_amount")
async def on_set_min_amount(call: CallbackQuery, state: FSMContext) -> None:
    await state.set_state(SettingStates.waiting_min_amount)
    with suppress(TelegramBadRequest):
        await call.message.edit_text(
            "📦 <b>Мінімальна сума угоди (₴)</b>\n\n"
            "Алерти з сумою <b>менше</b> цього порогу не прийдуть.\n"
            "Введи <code>0</code> щоб вимкнути нижній фільтр.\n\n"
            "<i>Приклад: 10000 — не показувати угоди менше 10 000 ₴</i>",
            reply_markup=back_to_main_kb(),
        )
    await call.answer()


@router.message(SettingStates.waiting_min_amount)
async def on_min_amount_input(message: Message, state: FSMContext) -> None:
    try:
        val = float(message.text.strip().replace(",", "."))
        if val < 0:
            raise ValueError("Значення не може бути від'ємним")
        if _db:
            conn = getattr(_db, "db", None) or getattr(_db, "_db", _db)
            # ALTER TABLE на випадок якщо стара БД без колонки
            try:
                await conn.execute(
                    "ALTER TABLE scanner_users ADD COLUMN min_amount_uah REAL DEFAULT 0.0"
                )
                await conn.commit()
            except Exception:
                pass
            await conn.execute(
                "UPDATE scanner_users SET min_amount_uah = ? WHERE user_id = ?",
                (val, message.from_user.id),
            )
            await conn.commit()
        label = f"{val:.0f} ₴" if val > 0 else "вимкнено (всі угоди)"
        await message.answer(
            f"✅ Мін. сума оновлена: <b>{label}</b>",
            reply_markup=back_to_main_kb(),
        )
    except ValueError as e:
        await message.answer(f"❌ Помилка: {e}\nВведи число (напр. 10000 або 0)")
    finally:
        await state.clear()


@router.callback_query(F.data == "set:spread")
async def on_set_spread(call: CallbackQuery, state: FSMContext) -> None:
    await state.set_state(SettingStates.waiting_spread)
    with suppress(TelegramBadRequest):
        await call.message.edit_text(
            "📉 <b>Введи мінімальний персональний спред (%)</b>\n\n"
            "<i>Можеш писати з комою або з крапкою (напр. 0,5 або 0.5)</i>",
            reply_markup=back_to_main_kb()
        )
    await call.answer()


@router.message(SettingStates.waiting_spread)
async def on_spread_input(message: Message, state: FSMContext) -> None:
    try:
        val = float(message.text.strip().replace(",", "."))
        if _db:
            conn = getattr(_db, "db", None) or getattr(_db, "_db", _db)
            await conn.execute("UPDATE scanner_users SET min_spread_pct = ? WHERE user_id = ?",
                               (val, message.from_user.id))
            await conn.commit()
        await message.answer(f"✅ Персональний мін. спред оновлено: <b>{val:.2f}%</b>", reply_markup=back_to_main_kb())
    except ValueError:
        await message.answer("❌ Формат невірний. Введи число (наприклад: 0.5 або 1.2)")
    finally:
        await state.clear()


# ── ОБРОБНИКИ ГОЛОВНИХ КНОПОК ─────────────────────────────────────────────
@router.callback_query(F.data == "menu:main")
@router.callback_query(F.data == "menu:dashboard")
async def on_main_menu(call: CallbackQuery, state: FSMContext) -> None:
    await state.clear()
    text, is_active = await _generate_dashboard_text(call.from_user.id)
    with suppress(TelegramBadRequest):
        await call.message.edit_text(text,
                                     reply_markup=main_menu_kb(is_active, is_muted(), _is_admin(call.from_user.id)))
    await call.answer()


@router.callback_query(F.data.in_(["scanner:start", "scanner:stop"]))
async def on_scanner_toggle(call: CallbackQuery) -> None:
    if not _is_admin(call.from_user.id):
        await call.answer("⛔ Тільки адмін може керувати ядром сканера", show_alert=True)
        return
    new_state = "true" if call.data == "scanner:start" else "false"
    await runtime_config.set("is_scanner_active", new_state)
    text, is_active = await _generate_dashboard_text(call.from_user.id)
    with suppress(TelegramBadRequest):
        await call.message.edit_text(text,
                                     reply_markup=main_menu_kb(is_active, is_muted(), _is_admin(call.from_user.id)))
    action = "ЗАПУЩЕНО! Парсинг почався" if is_active else "ЗУПИНЕНО! Парсинг на паузі"
    await call.answer(f"✅ Сканер {action}!", show_alert=True)


# ── menu:status (СТАТУС СИСТЕМИ) ──────────────────────────────────────────

async def _build_status_text(user_id: int) -> str:
    """Формує текст статусу системи (аналог /status, але для inline callback)."""
    from core.engine.exchange_manager import exchange_manager

    connected = []
    if _db:
        my_creds = await _db.get_all_credentials(user_id=user_id)
        for ex in ["Binance", "Bybit", "OKX", "MEXC", "Wallet"]:
            connected.append(f"✅ {ex}" if ex in my_creds else f"❌ {ex}")
    else:
        connected = ["❌ БД недоступна"]

    _my_capital = settings.working_capital_uah
    _my_spread = settings.min_spread_pct
    _my_min_amount = 0.0
    if _db:
        active_users = await _db.get_active_users()
        for u in active_users:
            if u["user_id"] == user_id:
                _my_capital = float(u["capital"])
                _my_spread = float(u["min_spread"])
                _my_min_amount = float(u.get("min_amount", 0.0))
                break

    risk = runtime_config.get("risk_mode", settings.risk_mode)
    min_amount_line = f"\n📦 Мін. сума: <code>{_my_min_amount:.0f} ₴</code>" if _my_min_amount > 0 else ""

    llm_q = _scanner_stats.get("llm_queue", 0)
    rev_q = _scanner_stats.get("review_queue", 0)
    cb_st = _scanner_stats.get("cb_status", {})

    import time as _t
    mute_left = max(0, _mute_until - _t.monotonic())
    mute_line = f"\n🔕 Пауза: <b>{mute_left / 3600:.1f} год</b>" if mute_left > 0 else ""

    # Біржі: стан з exchange_manager
    ex_lines = []
    for name, cb_state_val in cb_st.items():
        if cb_state_val == "DISABLED":
            st_list = exchange_manager.get_status_all()
            st_info = next((s for s in st_list if s["name"] == name), None)
            if st_info and st_info.get("cooldown_remaining_h", 0) > 0:
                ex_lines.append(f"  ⏱ {name}: COOLDOWN ({st_info['cooldown_remaining_h']:.1f}г)")
            else:
                ex_lines.append(f"  🔴 {name}: ВИМКНЕНО")
        elif cb_state_val == "CLOSED":
            ex_lines.append(f"  🟢 {name}: OK")
        elif cb_state_val == "OPEN":
            ex_lines.append(f"  🔴 {name}: CB OPEN")
        elif cb_state_val == "HALF_OPEN":
            ex_lines.append(f"  🟡 {name}: ВІДНОВЛЕННЯ")
        else:
            ex_lines.append(f"  ⚪ {name}: {cb_state_val}")

    text = (
            "📊 <b>Стан системи</b>\n\n"
            f"⚡ Останній цикл: <code>{_scanner_stats.get('last_cycle_ms', 0):.0f}ms</code>\n"
            f"🔄 Циклів: <code>{_scanner_stats.get('cycles', 0)}</code>\n"
            f"🧠 LLM черга: <code>{llm_q}</code>  📋 Reviews: <code>{rev_q}</code>"
            f"{mute_line}\n\n"
            f"💼 Капітал: <code>{_my_capital} ₴</code>\n"
            f"📉 Спред: <code>{_my_spread}%</code>"
            f"{min_amount_line}\n"
            f"🛡 Ризик: <code>{risk}</code>\n\n"
            "🔌 <b>API:</b>\n" + "\n".join(connected) + "\n\n"
                                                       "⚡ <b>Біржі:</b>\n" + "\n".join(ex_lines)
    )
    return text


def _status_kb() -> InlineKeyboardMarkup:
    """Клавіатура статусу з кнопкою управління біржами."""
    from aiogram.utils.keyboard import InlineKeyboardBuilder
    builder = InlineKeyboardBuilder()
    builder.row(
        InlineKeyboardButton(text="🔌 Управління біржами", callback_data="exch:list"),
    )
    builder.row(
        InlineKeyboardButton(text="🔙 Назад", callback_data="menu:main"),
    )
    return builder.as_markup()


@router.callback_query(F.data == "menu:status")
async def on_status_menu(call: CallbackQuery, state: FSMContext) -> None:
    """Показує статус системи з кнопкою управління біржами."""
    await state.clear()
    text = await _build_status_text(call.from_user.id)
    with suppress(TelegramBadRequest):
        await call.message.edit_text(text, reply_markup=_status_kb())
    await call.answer()


# ═══════════════════════════════════════════════════════════════════════════════
# Exchange Management — Telegram обробники
# ═══════════════════════════════════════════════════════════════════════════════

@router.callback_query(F.data == "exch:list")
async def on_exchange_list(call: CallbackQuery) -> None:
    """Список всіх бірж з їхнім статусом."""
    from core.engine.exchange_manager import exchange_manager
    statuses = exchange_manager.get_status_all()
    text = "🔌 <b>Управління біржами</b>\n\n<i>Тисни на біржу для керування:</i>"
    with suppress(TelegramBadRequest):
        await call.message.edit_text(text, reply_markup=exchanges_status_kb(statuses))
    await call.answer()


@router.callback_query(F.data.startswith("exch:toggle_menu:"))
async def on_exchange_toggle_menu(call: CallbackQuery) -> None:
    """Меню конкретної біржі."""
    from core.engine.exchange_manager import exchange_manager
    name = call.data.split(":")[2]
    statuses = exchange_manager.get_status_all()
    st = next((s for s in statuses if s["name"] == name), None)
    if not st:
        await call.answer("Біржу не знайдено", show_alert=True)
        return

    if st["enabled"]:
        text = f"🟢 <b>{name}</b> — активна\n\nОберіть дію:"
    else:
        reason = st.get("disabled_reason", "")
        remaining = st.get("cooldown_remaining_h", 0)
        text = f"🔴 <b>{name}</b> — вимкнена"
        if reason:
            text += f"\nПричина: {reason}"
        if remaining > 0:
            text += f"\n⏱ Автоввімкнення через: {remaining:.1f} год"

    with suppress(TelegramBadRequest):
        await call.message.edit_text(
            text,
            reply_markup=exchange_toggle_kb(name, st["enabled"], st.get("is_cooldown", False)),
        )
    await call.answer()


@router.callback_query(F.data.startswith("exch:disable:"))
async def on_exchange_disable(call: CallbackQuery) -> None:
    """Повністю вимкає біржу (до ручного ввімкнення)."""
    from core.engine.exchange_manager import exchange_manager
    name = call.data.split(":")[2]
    await exchange_manager.disable(name, reason="manual", runtime_config=runtime_config)
    await call.answer(f"🔴 {name} вимкнено!", show_alert=True)
    # Повертаємо до списку бірж
    statuses = exchange_manager.get_status_all()
    with suppress(TelegramBadRequest):
        await call.message.edit_text(
            f"🔴 <b>{name}</b> вимкнено до ручного ввімкнення.",
            reply_markup=exchanges_status_kb(statuses),
        )


@router.callback_query(F.data.startswith("exch:enable:"))
async def on_exchange_enable(call: CallbackQuery) -> None:
    """Вмикає біржу."""
    from core.engine.exchange_manager import exchange_manager
    name = call.data.split(":")[2]
    await exchange_manager.enable(name, runtime_config=runtime_config)
    await call.answer(f"🟢 {name} ввімкнено!", show_alert=True)
    statuses = exchange_manager.get_status_all()
    with suppress(TelegramBadRequest):
        await call.message.edit_text(
            f"🟢 <b>{name}</b> ввімкнено і повернено в пошук.",
            reply_markup=exchanges_status_kb(statuses),
        )


@router.callback_query(F.data.startswith("exch:cooldown_pick:"))
async def on_exchange_cooldown_pick(call: CallbackQuery) -> None:
    """Показує вибір часу cooldown."""
    name = call.data.split(":")[2]
    text = f"⏱ <b>Cooldown для {name}</b>\n\nОберіть на скільки вимкнути:"
    with suppress(TelegramBadRequest):
        await call.message.edit_text(text, reply_markup=exchange_cooldown_kb(name))
    await call.answer()


@router.callback_query(F.data.startswith("exch:cooldown:"))
async def on_exchange_cooldown(call: CallbackQuery) -> None:
    """Вимикає біржу з cooldown на N годин."""
    from core.engine.exchange_manager import exchange_manager
    parts = call.data.split(":")
    name = parts[2]
    hours = float(parts[3])
    await exchange_manager.disable(
        name, reason=f"cooldown {hours:.0f}г",
        cooldown_hours=hours, runtime_config=runtime_config,
    )

    labels = {1: "1 годину", 2: "2 години", 3: "3 години", 4: "4 години",
              6: "6 годин", 8: "8 годин", 12: "12 годин",
              24: "1 день", 48: "2 дні", 168: "1 тиждень"}
    label = labels.get(int(hours), f"{hours:.0f} годин")

    await call.answer(f"⏱ {name} вимкнено на {label}", show_alert=True)
    statuses = exchange_manager.get_status_all()
    st = next((s for s in statuses if s["name"] == name), {})
    with suppress(TelegramBadRequest):
        await call.message.edit_text(
            f"⏱ <b>{name}</b> вимкнено на <b>{label}</b>\n"
            f"Автоматично ввімкнеться через {st.get('cooldown_remaining_h', hours):.1f} год.\n\n"
            f"Можеш увімкнути достроково нижче 👇",
            reply_markup=exchange_toggle_kb(name, is_enabled=False, is_cooldown=True),
        )


@router.callback_query(F.data.startswith("exch:cooldown_custom:"))
async def on_exchange_cooldown_custom(call: CallbackQuery, state: FSMContext) -> None:
    """Запитує ввід кількості годин вручну."""
    name = call.data.split(":")[2]
    await state.set_state(ExchangeCooldownStates.waiting_hours)
    await state.update_data(exchange_name=name)
    with suppress(TelegramBadRequest):
        await call.message.edit_text(
            f"⌨️ <b>Введіть кількість годин</b> для cooldown {name}:\n\n"
            f"Наприклад: <code>5</code> або <code>72</code>",
            reply_markup=back_to_status_kb(),
        )
    await call.answer()


@router.message(ExchangeCooldownStates.waiting_hours)
async def on_exchange_cooldown_hours_input(message: Message, state: FSMContext) -> None:
    """Обробляє ввід годин для cooldown."""
    from core.engine.exchange_manager import exchange_manager
    data = await state.get_data()
    name = data.get("exchange_name", "")
    try:
        hours = float(message.text.strip().replace(",", "."))
        if hours <= 0 or hours > 720:  # макс 30 днів
            await message.answer("⚠️ Введіть число від 1 до 720 (годин)")
            return
    except (ValueError, TypeError):
        await message.answer("⚠️ Введіть число (наприклад: 5)")
        return

    await state.clear()
    await exchange_manager.disable(
        name, reason=f"cooldown {hours:.0f}г",
        cooldown_hours=hours, runtime_config=runtime_config,
    )
    statuses = exchange_manager.get_status_all()
    await message.answer(
        f"⏱ <b>{name}</b> вимкнено на <b>{hours:.0f} годин</b>",
        reply_markup=exchanges_status_kb(statuses),
    )


@router.callback_query(F.data.startswith("exch:healthcheck:"))
async def on_exchange_healthcheck(call: CallbackQuery) -> None:
    """Health-check: 3 спроби → ввімкнути або повідомити."""
    from core.engine.exchange_manager import exchange_manager
    name = call.data.split(":")[2]

    await call.answer(f"🔍 Перевіряю {name}...", show_alert=False)
    with suppress(TelegramBadRequest):
        await call.message.edit_text(f"🔍 Перевіряю <b>{name}</b>...\n\n⏳ 3 спроби з інтервалом 2с")

    ok, msg = await exchange_manager.health_check(name)

    if ok:
        await exchange_manager.enable(name, runtime_config=runtime_config)
        statuses = exchange_manager.get_status_all()
        with suppress(TelegramBadRequest):
            await call.message.edit_text(
                f"✅ <b>{name}</b> відповідає! Біржу ввімкнено.",
                reply_markup=exchanges_status_kb(statuses),
            )
    else:
        with suppress(TelegramBadRequest):
            await call.message.edit_text(
                f"{msg}\n\n"
                f"💡 Біржа все ще не відповідає. Залишаємо вимкненою.",
                reply_markup=exchange_down_kb(name),
            )


@router.callback_query(F.data.startswith("exch:back_to_down:"))
async def on_back_to_down(call: CallbackQuery) -> None:
    """Повернення до меню 'біржа впала'."""
    name = call.data.split(":")[3]
    text = (
        f"⚠️ <b>{name} API не відповідає</b>\n\n"
        f"Оберіть дію:"
    )
    with suppress(TelegramBadRequest):
        await call.message.edit_text(text, reply_markup=exchange_down_kb(name))
    await call.answer()


@router.callback_query(F.data == "exch:ignore")
async def on_exchange_ignore(call: CallbackQuery) -> None:
    """Ігноруємо проблему — залишаємо як є."""
    text, is_active = await _generate_dashboard_text(call.from_user.id)
    with suppress(TelegramBadRequest):
        await call.message.edit_text(text,
                                     reply_markup=main_menu_kb(is_active, is_muted(), _is_admin(call.from_user.id)))
    await call.answer("👌 OK, залишаємо як є")


# ── Локальна пауза алертів юзера (не глобальна зупинка ядра) ───────────────

@router.callback_query(F.data == "user:alerts:off")
async def on_user_alerts_off(call: CallbackQuery) -> None:
    """Юзер вимикає свої алерти — ядро сканера продовжує для інших."""
    if _db:
        conn = getattr(_db, "db", None) or getattr(_db, "_db", _db)
        await conn.execute(
            "UPDATE scanner_users SET is_alerts_active = 0 WHERE user_id = ?",
            (call.from_user.id,)
        )
        await conn.commit()
    text, is_active = await _generate_dashboard_text(call.from_user.id)
    with suppress(TelegramBadRequest):
        await call.message.edit_text(text,
                                     reply_markup=main_menu_kb(is_active, is_muted(), _is_admin(call.from_user.id)))
    await call.answer("🔕 Мої алерти вимкнено", show_alert=True)


@router.callback_query(F.data == "user:alerts:on")
async def on_user_alerts_on(call: CallbackQuery) -> None:
    """Юзер вмикає свої алерти знову."""
    if _db:
        conn = getattr(_db, "db", None) or getattr(_db, "_db", _db)
        await conn.execute(
            "UPDATE scanner_users SET is_alerts_active = 1 WHERE user_id = ?",
            (call.from_user.id,)
        )
        await conn.commit()
    text, is_active = await _generate_dashboard_text(call.from_user.id)
    with suppress(TelegramBadRequest):
        await call.message.edit_text(text,
                                     reply_markup=main_menu_kb(is_active, is_muted(), _is_admin(call.from_user.id)))
    await call.answer("🔔 Мої алерти увімкнено!", show_alert=True)


@router.callback_query(F.data == "menu:settings")
async def on_settings_menu(call: CallbackQuery) -> None:
    scanner_mode = "SPREAD"
    if _db:
        users = await _db.get_active_users()
        for u in users:
            if u["user_id"] == call.from_user.id:
                scanner_mode = u.get("scanner_mode", "SPREAD")
                break
    text = "⚙️ <b>Налаштування персональних фільтрів</b>\n\nТут ти можеш змінити свої особисті обмеження. Бот надішле тобі угоду ТІЛЬКИ якщо вона проходить під ці фільтри."
    with suppress(TelegramBadRequest):
        await call.message.edit_text(text,
                                     reply_markup=settings_menu_kb(scanner_mode, is_admin=_is_admin(call.from_user.id)))
    await call.answer()


@router.callback_query(F.data == "menu:keys")
async def on_keys_menu(call: CallbackQuery) -> None:
    has_keys = False
    if _db:
        creds = await _db.get_all_credentials(user_id=call.from_user.id)
        has_keys = len(creds) > 0
    text = "🔑 <b>Управління API Ключами</b>\n\nПідключи ключі, щоб дивитись актуальний баланс."
    with suppress(TelegramBadRequest):
        await call.message.edit_text(text, reply_markup=keys_menu_kb(has_keys))
    await call.answer()


@router.callback_query(F.data == "menu:help")
async def on_help_menu(call: CallbackQuery) -> None:
    text = "📖 <b>Довідка</b>\n\nСканер шукає P2P спреди 24/7 і фільтрує шахраїв через ШІ."
    with suppress(TelegramBadRequest):
        await call.message.edit_text(text, reply_markup=back_to_main_kb())
    await call.answer()


@router.callback_query(F.data == "menu:balance")
async def on_balance_button(call: CallbackQuery) -> None:
    await call.message.edit_text("⏳ Завантажую баланси...", reply_markup=None)
    text = await _generate_balance_text(call.from_user.id)
    with suppress(TelegramBadRequest):
        await call.message.edit_text(text, reply_markup=back_to_main_kb())
    await call.answer()


@router.callback_query(F.data == "menu:stats")
async def on_stats_menu(call: CallbackQuery) -> None:
    """Кнопка '📈 Статистика' з головного меню → 1-й рівень (вибір джерела)."""
    if not _db:
        return await call.answer("❌ БД не підключена.", show_alert=True)
    text = "📊 <b>Аналітичний центр Arbix Quantum</b>\n\nОберіть, яку саме статистику ви хочете переглянути:"
    with suppress(TelegramBadRequest):
        await call.message.edit_text(text, reply_markup=keyboards.stats_source_kb(), parse_mode="HTML")
    await call.answer()


@router.callback_query(F.data.startswith("stats:"))
async def on_stats_callback(call: CallbackQuery):
    """Універсальний роутер для всіх кнопок статистики."""
    # Відповідаємо Telegram ПЕРШИМ — без цього кнопка вічно крутиться незалежно від результату
    await call.answer()

    if not _db:
        return

    parts = call.data.split(":")
    action = parts[1]  # main | menu | daily | exchanges | heatmap | routes
    source = parts[2] if len(parts) > 2 else "my"  # my | scanner

    from core.analytics.stats_engine import StatsEngine
    engine = StatsEngine(_db)

    try:
        if action == "main":
            text = "📊 <b>Аналітичний центр Arbix Quantum</b>\n\nОберіть джерело даних:"
            await call.message.edit_text(text, reply_markup=keyboards.stats_source_kb(), parse_mode="HTML")

        elif action == "menu":
            if source == "my":
                summary = await engine.get_summary(30)
                text = (
                    f"💼 <b>Моя статистика (За 30 днів)</b>\n\n"
                    f"📈 Успішних угод: <b>{summary.get('total_trades', 0)}</b>\n"
                    f"💰 Зароблено: <b>{summary.get('total_profit', 0):.2f} ₴</b>\n"
                    f"📉 Середній профіт: <b>{summary.get('avg_profit', 0):.2f} ₴</b>\n"
                    f"🏆 Найкращий день: <b>{summary.get('best_day', 'N/A')}</b>"
                )
            else:
                props = await engine.get_proposals_summary(7)
                text = (
                    f"📡 <b>Аналітика ринку (За 7 днів)</b>\n\n"
                    f"🎯 Знайдено спредів: <b>{props.get('total', 0)}</b>\n"
                    f"📤 Надіслано алертів: <b>{props.get('sent', 0)}</b>\n"
                    f"📈 Середній спред: <b>{props.get('avg_spread', 0):.2f}%</b>\n"
                    f"🔝 Макс. спред: <b>{props.get('max_spread', 0):.2f}%</b>"
                )
            await call.message.edit_text(text, reply_markup=keyboards.stats_metrics_kb(source), parse_mode="HTML")

        elif action == "daily":
            if source == "my":
                data = await engine.get_profit_by_day(14)
                body = "\n".join(
                    f"▫️ {d['date']}: <b>+{d['profit']:.0f} ₴</b> ({d['trades']} угод)"
                    for d in data
                ) if data else "Немає даних."
                text = "📅 <b>Мій профіт по днях (14д):</b>\n\n" + body
            else:
                text = await engine.format_proposals_report(14)
            await call.message.edit_text(text, reply_markup=keyboards.stats_metrics_kb(source), parse_mode="HTML")

        elif action == "exchanges":
            if source == "my":
                data = await engine.get_top_exchanges(30)
                body = "\n".join(
                    f"🥇 {d.get('exchange', '?')}: <b>{d.get('volume_uah', 0):.0f} ₴</b> ({d.get('trades', 0)} угод)"
                    for d in data
                ) if data else "Немає даних."
                text = "🏦 <b>Мої топ біржі (За 30д):</b>\n\n" + body
            else:
                data = await engine.get_proposals_top_exchanges(14)
                body = "\n".join(
                    f"🔸 {d['exchange']}: <b>{d['count']} спредів</b> (avg {d['avg_spread']:.2f}%)"
                    for d in data
                ) if data else "Немає даних."
                text = "🏦 <b>Топ бірж сканера (За 14д):</b>\n\n" + body
            await call.message.edit_text(text, reply_markup=keyboards.stats_metrics_kb(source), parse_mode="HTML")

        elif action == "heatmap":
            heatmap_data = (
                await engine.get_my_hourly_heatmap(30)
                if source == "my"
                else await engine.get_proposals_hourly_heatmap(14)
            )
            DAYS = {"1": "Пн", "2": "Вт", "3": "Ср", "4": "Чт", "5": "Пт", "6": "Сб", "0": "Нд"}
            title = "Мої угоди" if source == "my" else "Ринок"
            lines = [f"🔥 <b>Теплова карта ({title}):</b>\n"]
            for d_idx, d_name in DAYS.items():
                hours = heatmap_data.get(d_idx, {})
                active = [f"{h}:00({c})" for h, c in sorted(hours.items()) if c > 0]
                if active:
                    lines.append(f"📅 <b>{d_name}:</b> " + ", ".join(active[:4]) + ("..." if len(active) > 4 else ""))
            text = "\n".join(lines) if len(lines) > 1 else "📭 Недостатньо даних для теплової карти."
            await call.message.edit_text(text, reply_markup=keyboards.stats_metrics_kb(source), parse_mode="HTML")

        elif action == "routes":
            if source == "my":
                text = "🗺 <b>Мої маршрути:</b>\n\n<i>Функція в розробці. Для маршрутів сканера — оберіть «Аналітику ринку».</i>"
            else:
                text = await engine.format_proposals_routes(14)
            await call.message.edit_text(text, reply_markup=keyboards.stats_metrics_kb(source), parse_mode="HTML")

    except TelegramBadRequest:
        pass  # Повідомлення не змінилось — ігноруємо
    except Exception as e:
        logger.error("on_stats_callback [%s/%s]: %s", action, source, e, exc_info=True)


@router.callback_query(F.data == "menu:sessions")
async def on_sessions_button(call: CallbackQuery) -> None:
    if not _db:
        with suppress(TelegramBadRequest):
            await call.message.edit_text("❌ БД не підключена.", reply_markup=back_to_main_kb())
        return await call.answer()

    from core.workers.session_manager import SESSION_TTL
    import time as _time
    sessions = await _db.get_all_auth_sessions()

    if not sessions:
        with suppress(TelegramBadRequest):
            await call.message.edit_text("📭 Жодних auth-сесій не знайдено.", reply_markup=back_to_main_kb())
        return await call.answer()

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

    with suppress(TelegramBadRequest):
        await call.message.edit_text("\n".join(lines), reply_markup=back_to_main_kb())
    await call.answer()


@router.callback_query(F.data == "keys:connect")
async def on_keys_connect_button(call: CallbackQuery) -> None:
    with suppress(TelegramBadRequest):
        await call.message.edit_text(
            "🔌 <b>Підключення біржі</b>\n\n"
            "Обери біржу для підключення API ключів:",
            reply_markup=exchange_connect_kb(SUPPORTED_EXCHANGES),
        )
    await call.answer()


@router.callback_query(F.data == "keys:disconnect")
async def on_keys_disconnect_button(call: CallbackQuery) -> None:
    if not _db: return await call.answer("БД недоступна", show_alert=True)
    all_creds = await _db.get_all_credentials(user_id=call.from_user.id)
    if not all_creds:
        with suppress(TelegramBadRequest): await call.message.edit_text("У тебе немає підключених бірж.",
                                                                        reply_markup=back_to_main_kb())
        return await call.answer()
    kb = InlineKeyboardMarkup(
        inline_keyboard=[[InlineKeyboardButton(text=f"❌ {ex}", callback_data=f"disconnect:{ex}")] for ex in
                         all_creds] + [[InlineKeyboardButton(text="🔙 Назад", callback_data="menu:keys")]])
    with suppress(TelegramBadRequest):
        await call.message.edit_text("Виберіть біржу для відключення:", reply_markup=kb)
    await call.answer()


# ── ВИБІР БАНКІВ (галочки) ─────────────────────────────────────────────────
def _get_user_banks_from_db_cache(user_id: int) -> list[str]:
    """Повертає поточні банки юзера з кешу active_users або defaults."""
    return list(BANK_NAMES.keys())  # fallback — всі банки


@router.callback_query(F.data == "set:banks_menu")
async def on_banks_menu(call: CallbackQuery) -> None:
    """Показує підменю вибору банків: загальні / покупка / продаж."""
    from aiogram.utils.keyboard import InlineKeyboardBuilder
    builder = InlineKeyboardBuilder()
    builder.row(InlineKeyboardButton(text="🏦 Загальні банки", callback_data="set:banks:general"))
    builder.row(InlineKeyboardButton(text="🛒 Банки для покупки", callback_data="set:banks:buy"))
    builder.row(InlineKeyboardButton(text="💸 Банки для продажу", callback_data="set:banks:sell"))
    builder.row(InlineKeyboardButton(text="🔙 Назад", callback_data="menu:settings"))

    # Підготуємо інфо про поточні налаштування
    info_lines = ["🏦 <b>Налаштування банків</b>\n"]
    if _db:
        users = await _db.get_active_users()
        for u in users:
            if u["user_id"] == call.from_user.id:
                gen = [BANK_NAMES.get(c, c) for c in u["bank_codes"]]
                buy = [BANK_NAMES.get(c, c) for c in u.get("buy_bank_codes", [])]
                sell = [BANK_NAMES.get(c, c) for c in u.get("sell_bank_codes", [])]
                info_lines.append(f"├ Загальні: <b>{', '.join(gen) or '—'}</b>")
                # Перевіряємо чи buy/sell відрізняються від загальних
                if u.get("buy_bank_codes") and u["buy_bank_codes"] != u["bank_codes"]:
                    info_lines.append(f"├ Покупка: <b>{', '.join(buy)}</b>")
                else:
                    info_lines.append(f"├ Покупка: <i>= загальні</i>")
                if u.get("sell_bank_codes") and u["sell_bank_codes"] != u["bank_codes"]:
                    info_lines.append(f"└ Продаж: <b>{', '.join(sell)}</b>")
                else:
                    info_lines.append(f"└ Продаж: <i>= загальні</i>")
                break

    info_lines.append("\n<i>Загальні — фільтри для обох сторін.\nОкремі buy/sell мають перевагу.</i>")

    with suppress(TelegramBadRequest):
        await call.message.edit_text("\n".join(info_lines), reply_markup=builder.as_markup())
    await call.answer()


@router.callback_query(F.data == "set:banks")
async def on_set_banks_legacy(call: CallbackQuery) -> None:
    """Fallback для старого callback — перенаправляє на нове меню."""
    return await on_banks_menu(call)


@router.callback_query(F.data.startswith("set:banks:"))
async def on_set_banks(call: CallbackQuery, state: FSMContext) -> None:
    """Показує меню вибору банків з галочками для конкретної сторони."""
    side = call.data.split(":")[-1]  # general / buy / sell
    side_labels = {"general": "загальні", "buy": "для покупки 🛒", "sell": "для продажу 💸"}

    current_banks: list[str] = []
    if _db:
        users = await _db.get_active_users()
        for u in users:
            if u["user_id"] == call.from_user.id:
                if side == "buy":
                    current_banks = u.get("buy_bank_codes", [])
                elif side == "sell":
                    current_banks = u.get("sell_bank_codes", [])
                else:
                    current_banks = u["bank_codes"]
                break
    if not current_banks:
        current_banks = DEFAULT_BANK_CODES

    await state.update_data(bank_side=side, selected_banks=current_banks[:])

    with suppress(TelegramBadRequest):
        await call.message.edit_text(
            f"🏦 <b>Банки — {side_labels.get(side, side)}</b>\n\n"
            "Обери банки для яких шукати спреди.\n"
            "<i>Натискай для вмикання/вимикання:</i>",
            reply_markup=banks_selection_kb(BANK_NAMES, current_banks, side),
        )
    await call.answer()


@router.callback_query(F.data.startswith("bank:toggle:"))
async def on_bank_toggle(call: CallbackQuery, state: FSMContext) -> None:
    """Перемикає банк і оновлює повідомлення з галочками."""
    parts = call.data.split(":")
    # bank:toggle:side:code
    side = parts[2] if len(parts) >= 4 else "general"
    code = parts[3] if len(parts) >= 4 else parts[2]

    # Читаємо поточний стан зі стейту (або БД якщо нема)
    data = await state.get_data()
    selected = data.get("selected_banks")

    if selected is None:
        # Перше натискання — читаємо з БД
        selected = DEFAULT_BANK_CODES[:]
        if _db:
            users = await _db.get_active_users()
            for u in users:
                if u["user_id"] == call.from_user.id:
                    if side == "buy":
                        selected = list(u.get("buy_bank_codes", u["bank_codes"]))
                    elif side == "sell":
                        selected = list(u.get("sell_bank_codes", u["bank_codes"]))
                    else:
                        selected = u["bank_codes"][:]
                    break

    selected_set = set(selected)
    if code in selected_set:
        if len(selected_set) > 1:  # Не дозволяємо вимкнути всі
            selected_set.discard(code)
        else:
            await call.answer("❗ Потрібен хоча б один банк", show_alert=True)
            return
    else:
        selected_set.add(code)

    selected = sorted(selected_set, key=lambda c: list(BANK_NAMES.keys()).index(c) if c in BANK_NAMES else 99)
    await state.update_data(selected_banks=selected, bank_side=side)

    with suppress(TelegramBadRequest):
        await call.message.edit_reply_markup(
            reply_markup=banks_selection_kb(BANK_NAMES, selected, side)
        )
    await call.answer()


@router.callback_query(F.data.startswith("bank:save:"))
async def on_bank_save(call: CallbackQuery, state: FSMContext) -> None:
    """Зберігає вибрані банки в БД."""
    side = call.data.split(":")[-1]  # general / buy / sell
    data = await state.get_data()
    selected = data.get("selected_banks", DEFAULT_BANK_CODES)
    await state.clear()

    if _db:
        conn = getattr(_db, "db", None) or getattr(_db, "_db", _db)
        if side == "buy":
            await conn.execute(
                "UPDATE scanner_users SET buy_bank_codes = ? WHERE user_id = ?",
                (",".join(selected), call.from_user.id),
            )
        elif side == "sell":
            await conn.execute(
                "UPDATE scanner_users SET sell_bank_codes = ? WHERE user_id = ?",
                (",".join(selected), call.from_user.id),
            )
        else:
            await conn.execute(
                "UPDATE scanner_users SET bank_codes = ? WHERE user_id = ?",
                (",".join(selected), call.from_user.id),
            )
        await conn.commit()

    side_labels = {"general": "загальні", "buy": "покупка 🛒", "sell": "продаж 💸"}
    bank_names = [BANK_NAMES.get(c, c) for c in selected]
    with suppress(TelegramBadRequest):
        await call.message.edit_text(
            f"✅ <b>Банки ({side_labels.get(side, side)}) збережено!</b>\n\n"
            f"Активні: {', '.join(bank_names)}\n\n"
            "<i>Сканер враховуватиме нові налаштування з наступного циклу.</i>",
            reply_markup=back_to_main_kb(),
        )
    await call.answer("✅ Збережено!")


# ── /ban ───────────────────────────────────────────────────────────────────
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
    global _mute_until
    import time
    action = call.data.split(":")[1]
    if action == "off":
        _mute_until = 0.0
        text, is_active = await _generate_dashboard_text(call.from_user.id)
        with suppress(TelegramBadRequest):
            await call.message.edit_text(text, reply_markup=main_menu_kb(is_active, False))
        await call.answer("🔔 Алерти увімкнено!")
        return
    hours = float(action)
    _mute_until = time.monotonic() + hours * 3600
    with suppress(TelegramBadRequest):
        await call.message.edit_text(
            f"🔕 <b>Алерти вимкнено на {hours:.0f} год</b>\n\n"
            "Щоб увімкнути — натисни /start або кнопку нижче.",
            reply_markup=back_to_main_kb(),
        )
    await call.answer(f"🔕 Пауза на {hours:.0f} год")


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
async def on_display_toggle(call: CallbackQuery) -> None:
    """Перемикає одне з налаштувань виводу."""
    if not _db:
        return await call.answer("БД не підключена", show_alert=True)

    key = call.data.split(":", 2)[2]  # e.g. "show_ai_terms_summary"
    valid_keys = {"show_ai_terms_summary", "show_full_terms", "show_ai_logic", "show_bank_details", "show_llm_summary"}
    if key not in valid_keys:
        return await call.answer("Невідома опція", show_alert=True)

    conn = getattr(_db, "db", None) or getattr(_db, "_db", _db)
    async with conn.execute(
            f"SELECT COALESCE({key}, 1) FROM scanner_users WHERE user_id=?",
            (call.from_user.id,)
    ) as cur:
        row = await cur.fetchone()
    current = int(row[0]) if row else 1
    new_val = 0 if current else 1
    await conn.execute(
        f"UPDATE scanner_users SET {key} = ? WHERE user_id = ?",
        (new_val, call.from_user.id),
    )
    await conn.commit()

    label = _DISPLAY_LABELS.get(key, key)
    status = "увімкнено ✅" if new_val else "вимкнено ❌"

    # Перечитуємо всі налаштування і оновлюємо клавіатуру
    display = await _db.get_user_display_settings(call.message.chat.id)
    with suppress(TelegramBadRequest):
        await call.message.edit_text(
            "🖥 <b>Налаштування виводу повідомлень</b>\n\n"
            f"{label}: <b>{status}</b>\n"
            f"<i>{_DISPLAY_DESCRIPTIONS.get(key, '')}</i>\n\n"
            "✅ = увімкнено, ❌ = вимкнено",
            reply_markup=display_settings_kb(display),
        )
    await call.answer(f"{label}: {status}")


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

    orders_label = f"{min_orders:.0f}" if min_orders else "без обмежень"
    rate_label = f"{min_rate:.0f}%" if min_rate else "без обмежень"

    from aiogram.utils.keyboard import InlineKeyboardBuilder
    builder = InlineKeyboardBuilder()
    builder.button(text=f"📊 Мін. угод: {orders_label}", callback_data="mf:orders")
    builder.button(text=f"⭐ Мін. рейтинг: {rate_label}", callback_data="mf:rate")
    builder.adjust(1)
    # Per-exchange кнопки
    for ex in _MF_EXCHANGES:
        icon = _MF_EX_ICONS.get(ex, "🔌")
        ex_f = emf.get(ex, {})
        if ex_f:
            o = ex_f.get("min_orders", 0)
            r = ex_f.get("min_rate", 0.0)
            parts = []
            if o: parts.append(f"≥{o:.0f} угод")
            if r: parts.append(f"≥{r:.0f}%")
            label = f"{icon} {ex}: {', '.join(parts)}"
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
        f"└ Мін. рейтинг: <b>{rate_label}</b>",
    ]
    if emf:
        lines.append("")
        lines.append("<b>📋 Per-exchange (мають перевагу):</b>")
        for ex in _MF_EXCHANGES:
            ef = emf.get(ex, {})
            if ef:
                o = ef.get("min_orders", 0)
                r = ef.get("min_rate", 0.0)
                lines.append(f"  {_MF_EX_ICONS.get(ex, '')} {ex}: угод≥{o:.0f}, рейтинг≥{r:.0f}%")
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


# ── Per-exchange фільтри мерчанта ──────────────────────────────────────────

@router.callback_query(F.data.startswith("mf:exchange:"))
async def on_mf_exchange(call: CallbackQuery) -> None:
    """Показує фільтри для конкретної біржі."""
    ex_name = call.data.split(":", 2)[2]
    _, emf = await _load_merchant_filters(call.from_user.id)
    ef = emf.get(ex_name, {})

    o = ef.get("min_orders", 0)
    r = ef.get("min_rate", 0.0)
    o_label = f"{o:.0f}" if o else "загальний"
    r_label = f"{r:.0f}%" if r else "загальний"

    icon = _MF_EX_ICONS.get(ex_name, "🔌")

    from aiogram.utils.keyboard import InlineKeyboardBuilder
    builder = InlineKeyboardBuilder()
    builder.button(text=f"📊 Мін. угод: {o_label}", callback_data=f"mf:ex_orders:{ex_name}")
    builder.button(text=f"⭐ Мін. рейтинг: {r_label}", callback_data=f"mf:ex_rate:{ex_name}")
    builder.adjust(1)
    if ef:
        builder.row(InlineKeyboardButton(text="🗑 Скинути (використ. загальні)", callback_data=f"mf:ex_reset:{ex_name}"))
    builder.row(InlineKeyboardButton(text="🔙 До фільтрів", callback_data="set:merchant_filters"))

    with suppress(TelegramBadRequest):
        await call.message.edit_text(
            f"{icon} <b>Фільтри для {ex_name}</b>\n\n"
            f"├ Мін. угод: <b>{o_label}</b>\n"
            f"└ Мін. рейтинг: <b>{r_label}</b>\n\n"
            "<i>0 = використовувати загальний фільтр</i>",
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
    if value == 0 or value == 0.0:
        mf.pop(key, None)  # 0 = вимкнути фільтр
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
    if value == 0 or value == 0.0:
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
    min_price = roi["min_sell_price"] if strategy == "roi" else d.get("price_input", 0.0)
    price_to  = d.get("price_to", 0.0)  # для range

    await conn.execute(
        """UPDATE scanner_users SET
               scanner_mode              = 'TAKER_SELL',
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
        """UPDATE scanner_users SET
               scanner_mode              = 'TAKER_BUY',
               is_alerts_active          = 1,
               taker_buy_amount          = ?,
               taker_buy_price_strategy  = ?,
               taker_buy_price_from      = ?,
               taker_buy_max_price       = ?,
               taker_buy_limit_min       = ?,
               taker_buy_limit_max       = ?,
               taker_buy_speed           = ?,
               buy_bank_codes            = ?
            
            
           WHERE user_id = ?""",
        (
            d["amount"],
            d.get("price_strategy", "any"),
            d.get("price_from", 0.0),
            d.get("price_to", 0.0),       # max_price або exact/range-max
            d.get("limit_min", 0.0),
            d.get("limit_max", 0.0),
            d.get("speed", "ANY"),
            banks_csv,
            user_id,
        ),
    )
    await conn.commit()


def _tbuy_price_strategy_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🌐 Будь-яка ціна",         callback_data="tbuy_ps:any")],
        [InlineKeyboardButton(text="⬇️ Не дорожче ніж...",      callback_data="tbuy_ps:max")],
        [InlineKeyboardButton(text="↔️ Ціновий діапазон",       callback_data="tbuy_ps:range")],
        [InlineKeyboardButton(text="🎯 Точно по ціні (Снайпер)", callback_data="tbuy_ps:exact")],
    ])

def _tsell_price_strategy_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🧮 ROI Калькулятор",         callback_data="tsell_ps:roi")],
        [InlineKeyboardButton(text="⬆️ Не дешевше ніж...",       callback_data="tsell_ps:min")],
        [InlineKeyboardButton(text="↔️ Ціновий діапазон",        callback_data="tsell_ps:range")],
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


def _get_taker_sell_preset(user_row: dict | None) -> dict | None:
    if not user_row:
        return None
    amount = float(user_row.get("taker_sell_amount", 0))
    price = float(user_row.get("taker_sell_price", 0))
    profit = float(user_row.get("taker_sell_profit", 0))
    if amount > 0 and price > 0 and profit > 0:
        return {
            "amount": amount,
            "buy_price": price,
            "profit": round(profit * 100, 2),
            "exchange": user_row.get("taker_sell_exchange", "—"),
            "min_sell_price": float(user_row.get("taker_sell_min_price", 0)),
            "speed": user_row.get("taker_sell_speed", "ANY"),
            "price_strategy": user_row.get("taker_sell_price_strategy", "roi"),
        }
    return None


def _get_taker_buy_preset(user_row: dict | None) -> dict | None:
    if not user_row:
        return None
    amount = float(user_row.get("taker_buy_amount", 0))
    if amount > 0:
        return {
            "amount":         amount,
            "max_price":      float(user_row.get("taker_buy_max_price", 0)),
            "limit_min":      float(user_row.get("taker_buy_limit_min", 0)),
            "limit_max":      float(user_row.get("taker_buy_limit_max", 0)),
            "speed":          user_row.get("taker_buy_speed", "ANY"),
            "price_strategy": user_row.get("taker_buy_price_strategy", "any"),  # ← додати
            "price_from":     float(user_row.get("taker_buy_price_from", 0)),   # ← додати
        }
    return None


def _taker_preset_kb(mode: str) -> InlineKeyboardMarkup:
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


def _sell_preset_text(p: dict) -> str:
    strategy_labels = {
        "roi":   "🧮 ROI авто",
        "min":   "⬆️ Мінімальна",
        "range": "↔️ Діапазон",
        "exact": "🎯 Точна",
    }
    strategy = p.get("price_strategy", "roi")
    strat_line = f"  • Стратегія: <b>{strategy_labels.get(strategy, strategy)}</b>\n"

    mp_line = f"  • Мін. ціна: <b>{p['min_sell_price']:.4f} ₴</b>\n" if p.get("min_sell_price", 0) > 0 else ""
    speed = "⚡ FAST" if p.get("speed") == "FAST" else "🐢 ANY"
    return (
        f"💾 <b>TAKER SELL — збережені налаштування</b>\n\n"
        f"  • Об'єм: <b>{p['amount']:.1f} USDT</b>\n"
        f"  • Купівля: <b>{p['buy_price']:.4f} ₴</b>\n"
        f"  • Біржа: <b>{p['exchange']}</b>\n"
        f"  • Прибуток: <b>{p['profit']:.2f}%</b>\n"
        f"{strat_line}{mp_line}"
        f"  • Швидкість: {speed}\n\n"
        f"Що робимо?"
    )

def _buy_preset_text(p: dict) -> str:
    strategy_labels = {
        "any":   "🔓 Будь-яка",
        "max":   "⬇️ Макс. ціна",
        "range": "↔️ Діапазон",
        "exact": "🎯 Точна",
    }
    strategy = p.get("price_strategy", "any")
    strat_line = f"  • Стратегія: <b>{strategy_labels.get(strategy, strategy)}</b>\n"

    # range — показуємо price_from теж
    if strategy == "range" and p.get("price_from", 0) > 0:
        strat_line += f"  • Від: <b>{p['price_from']:.4f} ₴</b>\n"

    mp_line  = f"  • До: <b>{p['max_price']:.4f} ₴</b>\n" if p.get("max_price", 0) > 0 else ""
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


def _sell_roi_text(d: dict, roi: dict) -> str:
    speed_text = "⚡ Важлива (лише великі ордери)" if d.get("speed") == "FAST" else "🐢 Не важлива"
    return (
        "🧮 <b>ROI КАЛЬКУЛЯТОР | Результат:</b>\n\n"
        f"📦 Продаю: <b>{d['amount']:.1f} USDT</b>\n"
        f"💲 Ціна входу: <b>{d['buy_price']:.4f} ₴</b>\n"
        f"📤 Біржа відправки: <b>{d.get('exchange', '—')}</b>\n"
        f"🌐 Network Fee: <b>{roi['network_fee']:.2f} USDT</b> ({d.get('network_name', '')})\n"
        f"⚡ Корисний об'єм: <b>{roi['usable_volume']:.2f} USDT</b>\n\n"
        f"💸 Вкладено: <b>{roi['invest_uah']:.2f} ₴</b>\n"
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
    await state.set_state(TakerSellSettingsStates.waiting_amount)
    with suppress(TelegramBadRequest):
        await call.message.edit_text(
            "💸 <b>TAKER SELL — Крок 1/4</b>\n\n"
            "📦 <b>Скільки USDT ти хочеш продати?</b>\n"
            "<i>Наприклад: 500</i>",
            reply_markup=back_to_main_kb(),
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
            with suppress(TelegramBadRequest):
                await call.message.edit_text(
                    _sell_preset_text(preset),
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
            creds = await _db.get_credentials(exchange=exchange, user_id=user_id) or {}
            if not creds.get("api_key"):
                creds = await _db.get_credentials(exchange=exchange, user_id=0) or {}
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

from core.utils.cache import TTLCache as _TTLCache

_taker_order_cache: _TTLCache = _TTLCache(ttl_seconds=300.0, max_size=500)


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
            order_url = build_order_url(exchange, order_id)

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
                    text=f"🔗 Відкрити ордер на {exchange}", url=order_url
                )])
            kb_rows.append([InlineKeyboardButton(text="🔙 В меню", callback_data="menu:main")])
            kb = InlineKeyboardMarkup(inline_keyboard=kb_rows)
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

@router.message(TakerSellSettingsStates.waiting_amount)
async def on_tsell_amount(message: Message, state: FSMContext) -> None:
    try:
        val = float(message.text.strip().replace(",", "."))
        if val <= 0:
            raise ValueError
    except ValueError:
        return await message.answer("❌ Введи число > 0. Наприклад: <code>500</code>")
    await state.update_data(amount=val)
    await state.set_state(TakerSellSettingsStates.waiting_buy_price)
    await message.answer(
        f"📦 Об'єм: <b>{val:.1f} USDT</b>\n\n"
        "💹 <b>TAKER SELL — крок 2/5</b>\n\n"
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
    await state.update_data(buy_price=val)
    await state.set_state(TakerSellSettingsStates.waiting_exchange)
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text="Binance", callback_data="tsell_ex:Binance"),
            InlineKeyboardButton(text="Bybit",   callback_data="tsell_ex:Bybit"),
        ],
        [
            InlineKeyboardButton(text="OKX",     callback_data="tsell_ex:OKX"),
            InlineKeyboardButton(text="MEXC",    callback_data="tsell_ex:MEXC"),
        ],
        [
            InlineKeyboardButton(text="⚡ P2P (без комісії)", callback_data="tsell_ex:INTERNAL"),
        ],
    ])
    await message.answer(
        f"💲 Ціна купівлі: <b>{val:.4f} ₴</b>\n\n"
        "🏦 <b>TAKER SELL — крок 3/5</b>\n\n"
        "З якої біржі виводитимеш USDT?\n"
        "<i>(потрібно для розрахунку Network Fee)</i>",
        reply_markup=kb,
    )


@router.callback_query(TakerSellSettingsStates.waiting_exchange, F.data.startswith("tsell_ex:"))
async def on_tsell_exchange(call: CallbackQuery, state: FSMContext) -> None:
    exchange = call.data.split(":")[1]
    await state.update_data(exchange=exchange)
    await call.answer()

    data = await state.get_data()
    amount    = float(data["amount"])
    buy_price = float(data["buy_price"])

    # Рахуємо breakeven прямо тут
    network_fee, network_name = _get_network_fee(exchange)
    usable_volume   = max(amount - network_fee, 0.001)
    breakeven_price = (amount * buy_price) / usable_volume
    breakeven_pct   = (breakeven_price / buy_price - 1) * 100

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
    try:
        val = float(message.text.strip().replace(",", ".").replace("%", ""))
        if val <= 0:
            raise ValueError
    except ValueError:
        return await message.answer("❌ Введи число > 0. Наприклад: <code>0.8</code>")

    data = await state.get_data()
    network_fee = float(data.get("network_fee", 1.0))
    roi = _calc_roi(data["amount"], data["buy_price"], val, network_fee)

    await state.update_data(profit=val, roi=roi)
    # Стан залишається waiting_profit → speed-кнопки підхоплюються нижче

    speed_kb = InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text="⚡ Так, швидко!",    callback_data="tsell_speed:FAST"),
        InlineKeyboardButton(text="🐢 Ні, чекатиму",   callback_data="tsell_speed:ANY"),
    ]])
    await message.answer(
        f"{_sell_roi_text(data | {'profit': val, 'network_name': data.get('network_name','')}, roi)}\n\n"
        "⏱ <b>Крок 5/5 — Час важливий?</b>\n\n"
        "⚡ <b>Швидко</b> — беремо тільки ордери які можуть повністю покрити наш обʼєм\n"
        "🐢 <b>Чекатиму</b> — беремо будь-які ордери, навіть якщо частковий обʼєм",
        reply_markup=speed_kb,
    )


@router.callback_query(TakerSellSettingsStates.waiting_profit, F.data.startswith("tsell_speed:"))
async def on_tsell_speed(call: CallbackQuery, state: FSMContext) -> None:
    speed = call.data.split(":")[1]
    await state.update_data(speed=speed)
    data = await state.get_data()
    roi  = data.get("roi", {})
    await call.answer()
    with suppress(TelegramBadRequest):
        await call.message.edit_text(
            _sell_roi_text(data, roi),
            reply_markup=_sell_final_kb(),
        )


@router.callback_query(F.data.in_({"tsell:launch", "tsell:save_and_launch"}))
async def on_tsell_launch(call: CallbackQuery, state: FSMContext) -> None:
    """✅ ФІКС Bug 1: ТІЛЬКИ тут записуємо scanner_mode і параметри в БД."""
    data = await state.get_data()
    roi = data.get("roi")
    if not roi or not data.get("amount"):
        return await call.answer("❌ Дані FSM втрачено. Почни знову /mode.", show_alert=True)

    await _save_taker_sell_db(call.from_user.id, data, roi)
    await state.clear()

    from bot.keyboards import scanner_mode_kb
    with suppress(TelegramBadRequest):
        await call.message.edit_text(
            "🚀 <b>TAKER SELL запущено!</b>\n\n"
            f"🔒 Мін. ціна продажу: <b>{roi['min_sell_price']:.4f} ₴</b>\n"
            f"📦 Об'єм: <b>{data['amount']:.1f} USDT</b>\n"
            f"💰 Цільовий профіт: <b>+{roi['net_profit_uah']:.2f} ₴</b>\n\n"
            "<i>Сканер шукає ордери — алерт прийде як тільки знайдеться підходящий.</i>",
            reply_markup=scanner_mode_kb("TAKER_SELL"),
        )
    await call.answer("🚀 Запущено!")
# =========================================================================
# 🛒 TAKER BUY FSM  (5 кроків + підтвердження)
# =========================================================================

@router.message(TakerBuySettingsStates.waiting_amount)
async def on_tbuy_amount(message: Message, state: FSMContext) -> None:
    try:
        val = float(message.text.strip().replace(",", "."))
        if val <= 0: raise ValueError
    except ValueError:
        return await message.answer("❌ Введи коректну суму USDT. Наприклад: <code>500</code>")

    await state.update_data(amount=val)
    await state.set_state(TakerBuySettingsStates.waiting_price_strategy)
    await message.answer(
        f"📦 Обʼєм: <b>{val:.1f} USDT</b>\n\n"
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
        "🛒 <b>TAKER BUY — Крок 3/5</b>\n\n"
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
    await message.answer(
        f"✅ Мін. ліміт: <b>{val:.0f} ₴</b>\n\n"
        "🛒 <b>TAKER BUY — Крок 3b/5</b>\n\n"
        "📏 <b>Максимальний ліміт ордерів (UAH)?</b>\n"
        "<i>Ордери з більшим лімітом ігноруються. Наприклад: 50000</i>",
        reply_markup=back_to_main_kb(),
    )


@router.message(TakerBuySettingsStates.waiting_limit_max)
async def on_tbuy_limit_max(message: Message, state: FSMContext) -> None:
    try:
        val = float(message.text.strip().replace(",", "."))
        if val < 0:
            raise ValueError
    except ValueError:
        return await message.answer("❌ Введи число:")
    await state.update_data(limit_max=val)
    await _show_tbuy_banks_msg(message.from_user.id, state, message.answer)


@router.callback_query(
    TakerBuySettingsStates.waiting_price_strategy,
    F.data.startswith("tbuy_ps:")
)
async def on_tbuy_price_strategy(call: CallbackQuery, state: FSMContext) -> None:
    strategy = call.data.split(":")[1]  # any | max | range | exact
    await state.update_data(price_strategy=strategy)

    if strategy == "any":
        # Пропускаємо введення ціни → одразу до лімітів
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
                reply_markup=keyboards.back_to_main_kb(),
            )
    elif strategy == "range":
        await state.set_state(TakerBuySettingsStates.waiting_price_from)
        with suppress(TelegramBadRequest):
            await call.message.edit_text(
                "↔️ <b>Ціновий діапазон — нижня межа</b>\n\n"
                "Введи мінімальну ціну (UAH/USDT):\n"
                "<i>Наприклад: 41.20</i>",
                reply_markup=keyboards.back_to_main_kb(),
            )
    elif strategy == "exact":
        await state.set_state(TakerBuySettingsStates.waiting_price_to)
        with suppress(TelegramBadRequest):
            await call.message.edit_text(
                "🎯 <b>Точна ціна (Снайпер)</b>\n\n"
                "Введи точну ціну (UAH/USDT):\n"
                "<i>Бот реагуватиме лише на ордери з цією ціною ±0.005₴</i>",
                reply_markup=keyboards.back_to_main_kb(),
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
    await message.answer(
        f"↔️ Від: <b>{val:.4f} ₴</b>\n\n"
        "Тепер введи <b>верхню межу</b> діапазону:",
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

    if strategy == "range" and val < price_from:
        return await message.answer(
            f"❌ Верхня межа <b>{val:.4f}</b> менша за нижню <b>{price_from:.4f}</b>"
        )

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
    await state.update_data(limit_min=0.0, limit_max=0.0)
    await call.answer()
    await state.set_state(TakerBuySettingsStates.waiting_banks)
    selected = await _get_user_buy_banks_db(call.from_user.id)
    await state.update_data(selected_banks=selected)
    with suppress(TelegramBadRequest):
        await call.message.edit_text(
            "🛒 <b>TAKER BUY — Крок 4/5</b>\n\n"
            "🏦 <b>Обери банки для купівлі:</b>\n"
            "<i>Позначені банки вже з твоїх глобальних налаштувань</i>",
            reply_markup=_tbuy_banks_kb(selected),
        )


async def _show_tbuy_banks_msg(user_id: int, state: FSMContext, send_fn) -> None:
    await state.set_state(TakerBuySettingsStates.waiting_banks)
    selected = await _get_user_buy_banks_db(user_id)
    await state.update_data(selected_banks=selected)
    await send_fn(
        "🛒 <b>TAKER BUY — Крок 4/5</b>\n\n"
        "🏦 <b>Обери банки для купівлі:</b>\n"
        "<i>Позначені банки вже з твоїх глобальних налаштувань</i>",
        reply_markup=_tbuy_banks_kb(selected),
    )


async def _get_user_buy_banks_db(user_id: int) -> list:
    if not _db:
        return []
    users = await _db.get_active_users()
    user = next((u for u in users if u["user_id"] == user_id), None)
    if user:
        return list(user.get("buy_bank_codes") or user.get("bank_codes") or [])
    return []


@router.callback_query(TakerBuySettingsStates.waiting_banks, F.data.startswith("tbuy_bank:"))
async def on_tbuy_bank_action(call: CallbackQuery, state: FSMContext) -> None:
    code = call.data.split(":")[1]

    if code == "done":
        data = await state.get_data()
        if not data.get("selected_banks"):
            return await call.answer("⚠️ Обери хоча б один банк!", show_alert=True)
        await state.set_state(TakerBuySettingsStates.waiting_speed)
        await call.answer()
        speed_kb = InlineKeyboardMarkup(inline_keyboard=[[
            InlineKeyboardButton(text="⚡ Важлива (ордер ≥ мій об'єм)", callback_data="tbuy_speed:FAST"),
            InlineKeyboardButton(text="🐢 Не важлива (частинами теж ок)", callback_data="tbuy_speed:ANY"),
        ]])
        with suppress(TelegramBadRequest):
            await call.message.edit_text(
                "🛒 <b>TAKER BUY — Крок 5/5</b>\n\n"
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
    """✅ ФІКС Bug 1: ТІЛЬКИ тут записуємо scanner_mode і параметри в БД."""
    data = await state.get_data()
    if not data.get("amount"):
        return await call.answer("❌ Дані FSM втрачено. Почни знову /mode.", show_alert=True)

    data["banks"] = [str(b) for b in data.get("selected_banks", [])]
    await _save_taker_buy_db(call.from_user.id, data)
    await state.clear()

    from bot.keyboards import scanner_mode_kb
    mp_line = f"💰 Макс. ціна: <b>{data['max_price']:.2f} ₴</b>\n" if data.get("max_price", 0) > 0 else ""
    with suppress(TelegramBadRequest):
        await call.message.edit_text(
            "🚀 <b>TAKER BUY запущено!</b>\n\n"
            f"📦 Шукаю ордери для купівлі <b>{data['amount']:.1f} USDT</b>\n"
            + mp_line
            + "\n<i>Алерти надходять одразу як з'являються підходящі ордери.</i>",
            reply_markup=scanner_mode_kb("TAKER_BUY"),
        )
    await call.answer("🚀 Запущено!")


# =========================================================================
# 🎯 СНАЙПЕР-ОРДЕРИ (VOLUME SWEEPER)
# =========================================================================

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

    import asyncio
    asyncio.create_task(_trade_worker.execute_tm_route(
        buy_leg=data["tm_buy_leg"],
        sell_exchange=data["tm_sell_exchange"],
        amount_usdt=amount_usdt,
        owner_user_id=message.from_user.id
    ))


# =========================================================================
# 📱 РУЧНЕ ПЕРЕХОПЛЕННЯ СЕСІЙ (Bookmarklet)
# =========================================================================

@router.callback_query(F.data.startswith("intercept:"))
async def on_intercept_session(call: CallbackQuery, state: FSMContext) -> None:
    exchange = call.data.split(":")[1]
    user_id = call.from_user.id

    # Визначаємо хост. Поки бекенд без домену, використовуємо ngrok або локалку.
    domain = "http://192.168.1.100:8000"  # TODO: replace with config domain or env bot IP
    domain = runtime_config.get("api_domain", domain)

    js_code = f"""javascript:(function(){{
    let c = document.cookie;
    fetch('{domain}/api/v1/session/receive', {{
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
        f"📱 <b>Оновлення сесії {exchange} (через телефон/ПК)</b>\n\n"
        f"1️⃣ <b>Скопіюй код нижче</b> (натисни на нього).\n"
        f"2️⃣ Створи в браузері (Safari/Chrome) нову закладку.\n"
        f"3️⃣ Зміни URL-адресу цієї закладки на скопійований код.\n"
        f"4️⃣ Зайди на сторінку P2P {exchange} та переконайся, що увійшов в акаунт.\n"
        f"5️⃣ Тікни на щойно створену закладку у вибраному.\n\n"
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

    import asyncio
    asyncio.create_task(_trade_worker.execute_mt_route(
        buy_exchange=data["mt_buy_exchange"],
        buy_price=data["mt_buy_price"],
        sell_leg=data["mt_sell_leg"],
        amount_usdt=amount_usdt,
        owner_user_id=message.from_user.id
    ))


# ═══════════════════════════════════════════════════════════════════════════════
# CARD MANAGEMENT SYSTEM UI
# ═══════════════════════════════════════════════════════════════════════════════

@router.message(Command("cards"))
async def cmd_cards_dashboard(message: Message) -> None:
    if not _db:
        return await message.answer("❌ БД не підключена.")
    await _show_cards_dashboard(message.from_user.id, message)

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

@router.callback_query(F.data == "card:toggle_module")
async def cb_card_toggle_module(call: CallbackQuery) -> None:
    if not _db:
        return await call.answer("❌ БД не підключена.")
    settings = await _db.get_user_card_settings(call.from_user.id)
    new_mode = "full" if settings.get("card_module_mode") == "off" else "off"
    conn = getattr(_db, "db", None) or getattr(_db, "_db", _db)
    await conn.execute("UPDATE user_card_settings SET card_module_mode=? WHERE user_id=?", (new_mode, call.from_user.id))
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
    
    import datetime
    cooldown_str = "Немає"
    if card["cooldown_until"] > time.time():
        dt = datetime.datetime.fromtimestamp(card["cooldown_until"])
        cooldown_str = dt.strftime("%Y-%m-%d %H:%M:%S")
        
    text = (
        f"💳 <b>Картка:</b> {card['bank_name'].capitalize()} {card['last_four']}\n"
        f"🏷 <b>Мітка:</b> {card['label']}\n"
        f"👤 <b>Тип:</b> {'Власна' if card['is_own'] else 'Дроп'}\n"
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
        await call.message.edit_text(text, reply_markup=keyboards.card_details_kb(card_id, card["status"], card["bank_name"]))
    else:
        # Для фейкового call
        await call.message.answer(text, reply_markup=keyboards.card_details_kb(card_id, card["status"], card["bank_name"]))
        
    if hasattr(call, "answer"):
        await call.answer()

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
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="🔙 Скасувати", callback_data=f"card:view:{card_id}")]])
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
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="🔙 Скасувати", callback_data=f"card:view:{card_id}")]])
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
    await state.update_data(card_id=card_id)
    await call.message.edit_text(
        "Введіть точний актуальний баланс картки (грн):\n\n"
        "<i>Ця дія просто оновить баланс у системі без створення транзакції.</i>",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="🔙 Скасувати", callback_data=f"card:view:{card_id}")]])
    )
    await state.set_state(CardUpdateStates.waiting_true_balance)
    await call.answer()

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
        
    await call.message.edit_text(f"✅ Успішно зарезервовано {data['target_amount']:.0f} ₴ на {len(cards)} картках для ордеру {data['order_id'][:8]}")
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
    await message.delete() # hide token
    
    status_text = "✅ <b>Monobank успішно підключено!</b>\n"
    if mapped:
        status_text += "Картку знайдено в API та успішно прив'язано.\n\n"
    else:
        status_text += "⚠️ Увага: Картку з такими останніми цифрами не знайдено в цьому токені.\n\n"
        
    await msg.edit_text(
        status_text +
        f"Встановіть цей Webhook URL у налаштуваннях Mono:\n"
        f"<code>https://&lt;your-domain&gt;/api/v1/webhooks/mono/card/{card_id}/{secret}</code>\n"
    )

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
            SELECT 
                COUNT(*) as tx_count,
                SUM(CASE WHEN direction='in' AND type='work' THEN amount ELSE 0 END) as work_in,
                SUM(CASE WHEN direction='out' AND type='work' THEN amount ELSE 0 END) as work_out,
                SUM(CASE WHEN direction='in' AND type='personal' THEN amount ELSE 0 END) as pers_in,
                SUM(CASE WHEN direction='out' AND type='personal' THEN amount ELSE 0 END) as pers_out
            FROM card_transactions
            WHERE card_id=? AND timestamp > ?
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

        # Ліміти
        limits = await _db.get_user_bank_limits(user_id, card["bank_name"])
        limit_line = ""
        if limits and hours <= 24:
            used_in_24 = await _db.get_rolling_used(card_id, "in", 24)
            used_out_24 = await _db.get_rolling_used(card_id, "out", 24)
            daily_in_max = limits.get("daily_in_max", 150000)
            daily_out_max = limits.get("daily_out_max", 150000)
            tx_today = await _db.get_card_transactions_count(card_id, 24)
            max_tx = limits.get("max_tx_per_day", 15)
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
    limits = await _db.get_user_bank_limits(user_id, bank)
    if not limits:
        # Показуємо дефолтні значення
        limits = {
            "daily_out_max": 150000.0, "daily_in_max": 150000.0,
            "monthly_out_max": 400000.0, "monthly_in_max": 400000.0,
            "max_single_tx_out": 29999.0, "max_single_tx_in": 29999.0,
            "max_tx_per_day": 15, "cooldown_hours": 24
        }
    await call.message.edit_text(
        f"⚙️ <b>Ліміти: {bank.capitalize()}</b>\n"
        f"<i>Натисніть на поле для зміни значення:</i>",
        reply_markup=keyboards.bank_limits_fields_kb(bank, limits)
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
            [InlineKeyboardButton(text="🔙 Скасувати", callback_data=f"limits:bank:{bank}")]
        ])
    )
    await state.set_state(BankLimitStates.waiting_value)
    await call.answer()

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
            [InlineKeyboardButton(text="🔙 Скасувати", callback_data=f"card:limits:{card_id}")]
        ])
    )
    await state.set_state(CardLimitStates.waiting_value)
    await call.answer()

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

        # Шукаємо або додаємо обробник головного екрану налаштувань у commands.py:
        @router.callback_query(F.data == "gset:main")
        async def cb_global_settings_main(call: CallbackQuery):
            """
            Рендерить головне вікно налаштувань (як на скріншоті).
            Підтягує актуальний стан конфігу та викликає оновлену клавіатуру.
            """
            from config.runtime import runtime_config
            from bot.keyboards import global_settings_kb

            # Збираємо поточний зріз конфігурації для рендерингу бейджів на кнопках
            current_settings = {
                "min_spread_pct": float(runtime_config.get("min_spread_pct", 0.5)),
                "safety_buffer_pct": float(runtime_config.get("safety_buffer_pct", 0.3)),
                "max_alerts_per_cycle": int(runtime_config.get("max_alerts_per_cycle", 4)),
                "require_sessions": runtime_config.get("require_sessions", "true"),
            }

            text = (
                "⚙️ <b>Глобальні налаштування ядра Arbix Quantum</b>\n\n"
                "Тут ви можете змінити базові параметри пошуку спредів для всього сканера. "
                "Для конфігурації експериментальних фіч перейдіть у відповідну вкладку:"
            )

            # Викликаємо клавіатуру з keyboards.py, куди ми вже додали нову кнопку
            await call.message.edit_text(
                text=text,
                reply_markup=global_settings_kb(current_settings)
            )

        @router.callback_query(F.data.startswith("card:update_bal:"))
        async def cb_card_update_balance_mexc(call: CallbackQuery):
            """
            Обробник кнопки '🔄 Актуалізувати баланс' з меню деталей картки.
            Стукає в direct API Монобанку, оновлює SQLite та робить ререндер картки.
            """
            try:
                card_id = call.data.split(":")[-1]

                # 1. Повідомляємо юзера про початок сесії
                await call.answer("🔄 Запит до серверів Monobank API...")

                # 2. Викликаємо наш прямий метод полінгу з MerchantDB
                # Об'єкт бази даних у твоїх командах зазвичай доступний як _db або self._db
                new_balance = await _db.force_refresh_mono_balance(card_id)

                if new_balance is not None:
                    await call.answer(f"✅ Баланс успішно оновлено: {new_balance:,.2f} ₴", show_alert=True)

                    # 3. 🚀 Автоматичний РЕРЕНДЕР: імітуємо повторний клік на перегляд картки,
                    # щоб юзер одразу побачив нову цифру балансу в ТГ без закриття меню.
                    call.data = f"card:view:{card_id}"
                    try:
                        # Викликаємо твій існуючий хендлер детального перегляду картки
                        await cb_card_view(call)
                    except NameError:
                        # Якщо назва хендлера відрізняється, бот просто оновить сповіщення
                        pass
                else:
                    await call.answer(
                        "❌ Не вдалося оновити через API.\n\n"
                        "Перевірте, чи це картка Monobank та чи підключено дійсний X-Token.",
                        show_alert=True
                    )

            except Exception as e:
                logging.getLogger("Commands").error(f"Помилка мануального оновлення балансу: {e}")
                await call.answer("🔥 Внутрішня помилка обробника балансу", show_alert=True)