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
    waiting_capital    = State()
    waiting_min_amount = State()
    waiting_spread     = State()


class GlobalSettingStates(StatesGroup):
    waiting_value = State()


class MerchantFilterStates(StatesGroup):
    waiting_min_orders = State()
    waiting_min_rate   = State()
    waiting_ex_min_orders = State()
    waiting_ex_min_rate   = State()


class ExchangeCooldownStates(StatesGroup):
    waiting_hours = State()


# ── Словник описів для UI ──────────────────────────────────────────────────
# Тільки системні параметри (персональні - в scanner_users через меню)
SETTING_DESCRIPTIONS = {
    "risk_mode":              "🛡 Рівень антифроду",
    "behavior_alert_score":   "🤖 Поріг балів ботів",
    "velocity_spike_per_hour":"⚡ Аномальна швидкість (угод/год)",
    "sticky_min_chain":       "📌 Липкі ліміти (циклів)",
    "review_ttl_hours":       "💬 Кеш відгуків (годин)",
    "max_alerts_per_cycle":   "🔔 Макс. алертів за цикл",
}

# Відповідність ключ → опис для кнопок
_KEY_LABELS = {
    "risk_mode":               "🛡 Антифрод",
    "behavior_alert_score":    "🤖 Поріг ботів",
    "velocity_spike_per_hour": "⚡ Швидкість",
    "sticky_min_chain":        "📌 Липкі ліміти",
    "review_ttl_hours":        "💬 Кеш відгуків",
    "max_alerts_per_cycle":    "🔔 Макс. алертів",
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


def setup(db, account_clients: dict, trade_worker=None, notifier=None, single_leg_executor=None) -> None:
    global _db, _account_clients, _trade_worker, _notifier, _single_leg_executor
    if _trade_worker and trade_worker and _trade_worker is not trade_worker:
        logger.warning("setup(): TradeWorker перезаписується!")
    _db = db
    _account_clients = account_clients
    _trade_worker = trade_worker
    if notifier is not None:
        _notifier = notifier
    if single_leg_executor is not None:
        _single_leg_executor = single_leg_executor


def update_stats(**kwargs) -> None:
    _scanner_stats.update(kwargs)


# ── /start (ГОЛОВНИЙ ДАШБОРД) ──────────────────────────────────────────────
# ── /start (ЗАПУСК ПЕРСОНАЛЬНОГО СКАНЕРА ТА ДАШБОРД) ──────────────────────
# ── /start (ЗАПУСК ПЕРСОНАЛЬНОГО СКАНЕРА ТА ДАШБОРД) ──────────────────────
async def _generate_dashboard_text(user_id: int) -> tuple[str, bool]:
    user_capital    = str(settings.working_capital_uah)
    user_min_amount = "без обмежень"
    user_spread     = "0.50"
    if _db:
        active_users = await _db.get_active_users()
        for u in active_users:
            if u["user_id"] == user_id:
                user_capital = f"{u['capital']:.1f}"
                user_spread  = f"{u['min_spread']:.2f}"
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
        "<b>/status</b>\nПоточний стан сканера."
    )
    await message.answer(text)


# ── /active (ПОКАЗАТИ ВСІ ПОТОЧНІ СПРЕДИ) ──────────────────────────────────
@router.message(Command("active"))
async def cmd_active(message: Message) -> None:
    from state import state as app_state
    from copy import copy

    alerts = getattr(app_state, "current_alerts", [])
    if not alerts:
        return await message.answer(
            "📭 <b>Зараз активних спредів немає</b>\n\n"
            "Сканер працює, але поки не знайшов підходящих зв'язок.\n"
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
                b_rec, _, b_reason, _ = await _db.get_trade_recommendation_full(
                    a.buy_order.exchange, a.buy_order.merchant_id
                )
                s_rec, _, s_reason, _ = await _db.get_trade_recommendation_full(
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
    _my_capital    = settings.working_capital_uah
    _my_spread     = settings.min_spread_pct
    _my_min_amount = 0.0
    active_users = await _db.get_active_users()
    for u in active_users:
        if u["user_id"] == message.from_user.id:
            _my_capital    = float(u["capital"])
            _my_spread     = float(u["min_spread"])
            _my_min_amount = float(u.get("min_amount", 0.0))
            break

    # Глобальні системні параметри
    capital = _my_capital
    spread  = _my_spread
    risk    = runtime_config.get("risk_mode", settings.risk_mode)
    min_amount_line = f"\n📦 Мін. сума: <code>{_my_min_amount:.0f} ₴</code>" if _my_min_amount > 0 else ""

    llm_q = _scanner_stats.get("llm_queue", 0)
    rev_q = _scanner_stats.get("review_queue", 0)
    cb_st = _scanner_stats.get("cb_status", {})
    _cb_icons = {"CLOSED": "🟢", "OPEN": "🔴", "HALF_OPEN": "🟡", "DISABLED": "⏸"}
    cb_lines = [f"  {_cb_icons.get(v, '⚪')} {k}: {v}" for k, v in cb_st.items()]
    import time as _t
    mute_left = max(0, _mute_until - _t.monotonic())
    mute_line = f"\n🔕 Пауза: <b>{mute_left/3600:.1f} год</b>" if mute_left > 0 else ""

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
    with suppress(Exception): await message.delete()
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
                coin   = b.get("coin", "?")
                free   = float(b.get("free", 0))
                total  = float(b.get("total", free))
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
    lines.append("<i>Впливають на поведінку всього сканера. Особисті фільтри (капітал/спред/банки) — в головному меню.</i>\n")
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

    val  = runtime_config.get(key, getattr(settings, key, "—"))
    desc = SETTING_DESCRIPTIONS.get(key, key)

    _HINTS = {
        "velocity_spike_per_hour": "<i>Число з крапкою (напр. 20.0)</i>",
        "review_ttl_hours":        "<i>Число з крапкою (напр. 24.0)</i>",
        "behavior_alert_score":    "<i>Ціле число (напр. 60)</i>",
        "sticky_min_chain":        "<i>Ціле число (напр. 3)</i>",
        "max_alerts_per_cycle":    "<i>Ціле число (напр. 5)</i>",
        "risk_mode":               "<i>STRICT, WARNING або RELAXED</i>",
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
        await call.message.edit_text(text, reply_markup=main_menu_kb(is_active, is_muted(), _is_admin(call.from_user.id)))
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
        await call.message.edit_text(text, reply_markup=main_menu_kb(is_active, is_muted(), _is_admin(call.from_user.id)))
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
    """Повністю вимикає біржу (до ручного ввімкнення)."""
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
        await call.message.edit_text(text, reply_markup=main_menu_kb(is_active, is_muted(), _is_admin(call.from_user.id)))
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
        await call.message.edit_text(text, reply_markup=main_menu_kb(is_active, is_muted(), _is_admin(call.from_user.id)))
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
        await call.message.edit_text(text, reply_markup=main_menu_kb(is_active, is_muted(), _is_admin(call.from_user.id)))
    await call.answer("🔔 Мої алерти увімкнено!", show_alert=True)


@router.callback_query(F.data == "menu:settings")
async def on_settings_menu(call: CallbackQuery) -> None:
    text = "⚙️ <b>Налаштування персональних фільтрів</b>\n\nТут ти можеш змінити свої особисті обмеження. Бот надішле тобі угоду ТІЛЬКИ якщо вона проходить під ці фільтри."
    with suppress(TelegramBadRequest):
        await call.message.edit_text(text, reply_markup=settings_menu_kb())
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
    action = parts[1]   # main | menu | daily | exchanges | heatmap | routes
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
    exchange    = parts[1].strip()
    merchant_id = parts[2].strip()
    reason      = parts[3].strip() if len(parts) > 3 else "Ручний бан"

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
    "show_full_terms":       "🔘 Повні умови (спойлер)",
    "show_ai_logic":         "🔘 Логіка AI (спойлер)",
    "show_bank_details":     "🔘 Деталі банків (спойлер)",
    "show_llm_summary":      "🔘 Вердикт AI в алерті",
}

_DISPLAY_DESCRIPTIONS = {
    "show_ai_terms_summary": "ШІ генерує коротку вижимку умов мерчанта (ключові вимоги, нюанси). Показується прямо в тілі повідомлення.",
    "show_full_terms":       "Сирий текст умов мерчанта ховається під спойлер. Завжди можна розгорнути і прочитати оригінал.",
    "show_ai_logic":         "Думки нейромережі (reason) ховаються під розгортання. Якщо вимкнено — лише бейдж (✅/⚡/🚫).",
    "show_bank_details":     "Деталі банків, фільтрів та варіантів зв'язки показуються у спойлері.",
    "show_llm_summary":      "Повний блок вердикту AI (рекомендація + причина) показується в алерті.",
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
# 🚀 АВТО-ТРЕЙД (Вдосконалена версія)
# =========================================================================
from aiogram.utils.keyboard import InlineKeyboardBuilder
import time
from core.engine.network_fee_engine import NetworkFeeEngine

_spread_cache: dict[str, tuple] = {}  # cache_key -> (SpreadAlert, timestamp)


class AutoTradeStates(StatesGroup):
    waiting_amount = State()
    waiting_confirm = State()


# 🚀 ФІКС #1: Винесено дублювання лімітів
def _calc_usdt_limits(alert) -> tuple[float, float]:
    min_usdt = max(
        float(alert.buy_order.min_limit) / float(alert.buy_order.price),
        float(alert.sell_order.min_limit) / float(alert.sell_order.price)
    )
    max_usdt = min(
        float(alert.buy_order.max_limit) / float(alert.buy_order.price),
        float(alert.sell_order.max_limit) / float(alert.sell_order.price),
        alert.deal_amount_uah / float(alert.buy_order.price)
    )
    return min_usdt, max_usdt


@router.callback_query(F.data.startswith("trade:tt:"))
async def on_auto_trade_start(call: CallbackQuery, state: FSMContext) -> None:
    if not _trade_worker:
        return await call.answer("❌ TradeWorker не підключено!", show_alert=True)

    cache_key = call.data.replace("trade:tt:", "")
    entry = _spread_cache.get(cache_key)

    # 🚀 ФІКС #6: TTL Кешу (5 хвилин)
    if not entry:
        return await call.answer("❌ Спред застарів.", show_alert=True)

    alert, ts = entry
    if time.time() - ts > 300:
        _spread_cache.pop(cache_key, None)
        return await call.answer("❌ Спред застарів (таймаут 5 хв).", show_alert=True)

    await state.update_data(cache_key=cache_key)
    await state.set_state(AutoTradeStates.waiting_amount)

    min_usdt, max_usdt = _calc_usdt_limits(alert)

    text = (
        f"⚡ <b>Авто-Трейд: Taker → Taker</b>\n\n"
        f"🛒 Купівля: {alert.buy_order.exchange} по {alert.buy_order.price}\n"
        f"💸 Продаж: {alert.sell_order.exchange} по {alert.sell_order.price}\n\n"
        f"Доступні ліміти: <b>від {min_usdt:.1f} до {max_usdt:.1f} USDT</b>\n\n"
        f"👇 Введи суму в <b>USDT</b> (або напиши <code>max</code>):"
    )
    await call.message.answer(text, reply_markup=back_to_main_kb())
    await call.answer()


@router.message(AutoTradeStates.waiting_amount)
async def on_auto_trade_amount(message: Message, state: FSMContext) -> None:
    data = await state.get_data()
    entry = _spread_cache.get(data.get("cache_key"))

    if not entry:
        await state.clear()
        return await message.answer("❌ Спред застарів.", reply_markup=back_to_main_kb())

    alert, _ = entry
    min_usdt, max_usdt = _calc_usdt_limits(alert)
    raw_input = message.text.strip().lower()

    try:
        amount_usdt = max_usdt if raw_input == "max" else float(raw_input.replace(",", "."))
        # 🚀 ФІКС #4: Жорсткі межі (+- 0.001 для компенсації похибки float у Python)
        if not (min_usdt - 0.001 <= amount_usdt <= max_usdt + 0.001):
            raise ValueError
    except ValueError:
        return await message.answer(
            f"❌ Невірна сума. Введи число від <b>{min_usdt:.1f}</b> до <b>{max_usdt:.1f}</b> (або 'max'):")

    # 🚀 ФІКС #2: Чесний розрахунок профіту з врахуванням комісій
    network, network_fee_usdt = NetworkFeeEngine.get_optimal_network(alert.buy_order.exchange,
                                                                      alert.sell_order.exchange)
    expected_profit = NetworkFeeEngine.calc_profit(
        amount_usdt,
        float(alert.buy_order.price),
        float(alert.sell_order.price),
        network_fee_usdt
    )

    await state.update_data(amount_usdt=amount_usdt)
    await state.set_state(AutoTradeStates.waiting_confirm)

    action = data.get("single_action", "BUY")
    icon = "🛒" if action == "BUY" else "💸"
    label = "Купівля" if action == "BUY" else "Продаж"

    builder = InlineKeyboardBuilder()
    builder.row(
        InlineKeyboardButton(text="✅ Підтвердити", callback_data="trade:confirm"),
        InlineKeyboardButton(text="❌ Скасувати", callback_data="trade:cancel"),
    )

    fee_text = f"(Комісія мережі: {network_fee_usdt}$) " if network_fee_usdt > 0 else ""

    await message.answer(
        f"📋 <b>Підтвердження угоди</b>\n\n"
        f"💰 Об'єм:   <code>{amount_usdt:.2f} USDT</code>\n"
        f"🛒 Купівля: {alert.buy_order.exchange} по {alert.buy_order.price}\n"
        f"💸 Продаж: {alert.sell_order.exchange} по {alert.sell_order.price}\n"
        f"✨ Профіт:  ~<b>{expected_profit:.0f} ₴</b> {fee_text}\n\n"
        f"⚠️ <i>Натисни ✅ для запуску TradeWorker (DRY_RUN_MODE).</i>",
        reply_markup=builder.as_markup(),
    )


@router.callback_query(F.data.in_({"trade:confirm", "trade:cancel"}))
async def on_auto_trade_confirm(call: CallbackQuery, state: FSMContext) -> None:
    if call.data == "trade:cancel":
        await state.clear()
        with suppress(TelegramBadRequest):
            await call.message.edit_text("🚫 Авто-трейд скасовано.", reply_markup=back_to_main_kb())
        return await call.answer("Скасовано.")

    data = await state.get_data()
    await state.clear()

    cache_key = data.get("cache_key")
    amount_usdt = data.get("amount_usdt")
    entry = _spread_cache.pop(cache_key, None)

    if not entry or not amount_usdt or not _trade_worker:
        return await call.answer("❌ Дані недоступні або застаріли.", show_alert=True)

    alert = entry[0]

    with suppress(TelegramBadRequest):
        await call.message.edit_text("⏳ <b>Виконується T→T угода…</b>\nПеревіряю LLM та створюю сесії в БД.",
                                     reply_markup=None)

    buy_leg = {
        "exchange": alert.buy_order.exchange,
        "ad_id": getattr(alert.buy_order, "ad_id", getattr(alert.buy_order, "order_id", "")),
        "price": float(alert.buy_order.price),
        "merchant_id": getattr(alert.buy_order, "merchant_id", "")
    }
    sell_leg = {
        "exchange": alert.sell_order.exchange,
        "ad_id": getattr(alert.sell_order, "ad_id", getattr(alert.sell_order, "order_id", "")),
        "price": float(alert.sell_order.price),
        "merchant_id": getattr(alert.sell_order, "merchant_id", "")
    }

    try:
        success = await _trade_worker.execute_tt_route(
            buy_leg=buy_leg,
            sell_leg=sell_leg,
            amount_usdt=amount_usdt,
            owner_user_id=call.from_user.id
        )
        result = "✅ <b>Угода успішно виконана!</b>\nПеревірте статус через /trades" if success else "❌ <b>Помилка.</b> Спред втік або LLM заблокувала."
    except Exception as e:
        logger.error("trade:confirm error: %s", e, exc_info=True)
        result = f"❌ <b>Критична помилка:</b> <code>{str(e)[:200]}</code>"

    with suppress(TelegramBadRequest):
        await call.message.edit_text(result, reply_markup=back_to_main_kb())
    await call.answer()


@router.message(Command("trades"))
async def cmd_trades(message: Message) -> None:
    if not getattr(_trade_worker, "_db", None):
        return await message.answer("❌ БД не підключена.")

    # 🚀 ФІКС #3: Використовуємо метод БД замість прямого SQL
    rows = await _trade_worker._db.get_recent_trade_sessions(limit=5)

    if not rows:
        return await message.answer("📭 Немає активних сесій.")

    lines = ["📊 <b>Твої активні сесії:</b>\n"]
    for row in rows:
        lines.append(
            f"🔹 <b>#{row['id']}</b> | <code>{row['strategy']}</code> | Статус: <b>{row['session_status']}</b> (Fee: {row['network_fee']}$, Профіт: {row['gross_profit']:.0f} ₴)")
    await message.answer("\n".join(lines))


# =========================================================================
# 🚀 БЛОК A: SINGLE-LEG TRADING (Купити / Продати окремо)
# =========================================================================

# Кеш для single-leg (cache_key → order data dict)
_single_leg_cache: dict[str, dict] = {}


class SingleLegStates(StatesGroup):
    waiting_amount = State()
    waiting_confirm = State()


@router.callback_query(F.data.startswith("sl:b:"))
async def on_single_buy_start(call: CallbackQuery, state: FSMContext) -> None:
    if not _single_leg_executor:
        return await call.answer("❌ SingleLegExecutor не підключено!", show_alert=True)

    cache_key = call.data[3:]  # "b:{sl_buy_key}"
    data = _single_leg_cache.get(cache_key)
    if not data or time.time() - data.get("ts", 0) > 300:
        return await call.answer("❌ Алерт застарів (>5 хв).", show_alert=True)

    price = data["price"]
    min_usdt = data["min_limit"] / price if price > 0 else 0
    max_usdt = data["max_limit"] / price if price > 0 else 0

    await state.update_data(
        single_action="BUY",
        ad_id=data["ad_id"],
        exchange=data["exchange"],
        price=price,
        merchant_id=data["merchant_id"],
        payment_method=data.get("bank", ""),
        min_usdt=min_usdt,
        max_usdt=max_usdt,
    )
    await state.set_state(SingleLegStates.waiting_amount)

    text = (
        f"🛒 <b>Купівля (Single-Leg)</b>\n\n"
        f"Біржа: <b>{data['exchange']}</b>\n"
        f"Ціна: <code>{price:.4f}</code> UAH\n"
        f"Ліміти: <b>{min_usdt:.1f} — {max_usdt:.1f} USDT</b>\n\n"
        f"👇 Введи суму в <b>USDT</b> (або <code>max</code>):"
    )
    await call.message.answer(text, reply_markup=back_to_main_kb())
    await call.answer()


@router.callback_query(F.data.startswith("sl:s:"))
async def on_single_sell_start(call: CallbackQuery, state: FSMContext) -> None:
    if not _single_leg_executor:
        return await call.answer("❌ SingleLegExecutor не підключено!", show_alert=True)

    cache_key = call.data[3:]  # "s:{sl_sell_key}"
    data = _single_leg_cache.get(cache_key)
    if not data or time.time() - data.get("ts", 0) > 300:
        return await call.answer("❌ Алерт застарів (>5 хв).", show_alert=True)

    price = data["price"]
    min_usdt = data["min_limit"] / price if price > 0 else 0
    max_usdt = data["max_limit"] / price if price > 0 else 0

    await state.update_data(
        single_action="SELL",
        ad_id=data["ad_id"],
        exchange=data["exchange"],
        price=price,
        merchant_id=data["merchant_id"],
        payment_method=data.get("bank", ""),
        min_usdt=min_usdt,
        max_usdt=max_usdt,
    )
    await state.set_state(SingleLegStates.waiting_amount)

    text = (
        f"💸 <b>Продаж (Single-Leg)</b>\n\n"
        f"Біржа: <b>{data['exchange']}</b>\n"
        f"Ціна: <code>{price:.4f}</code> UAH\n"
        f"Ліміти: <b>{min_usdt:.1f} — {max_usdt:.1f} USDT</b>\n\n"
        f"👇 Введи суму в <b>USDT</b> (або <code>max</code>):"
    )
    await call.message.answer(text, reply_markup=back_to_main_kb())
    await call.answer()


@router.message(SingleLegStates.waiting_amount)
async def on_single_leg_amount(message: Message, state: FSMContext) -> None:
    data = await state.get_data()
    min_usdt = data.get("min_usdt", 0)
    max_usdt = data.get("max_usdt", 0)
    raw = message.text.strip().lower()

    try:
        amount = max_usdt if raw == "max" else float(raw.replace(",", "."))
        if not (min_usdt - 0.001 <= amount <= max_usdt + 0.001):
            raise ValueError
    except ValueError:
        return await message.answer(
            f"❌ Невірна сума. Введи число від <b>{min_usdt:.1f}</b> до <b>{max_usdt:.1f}</b> (або 'max'):"
        )

    await state.update_data(amount_usdt=amount)
    await state.set_state(SingleLegStates.waiting_confirm)

    action = data.get("single_action", "BUY")
    icon = "🛒" if action == "BUY" else "💸"
    label = "Купівля" if action == "BUY" else "Продаж"

    builder = InlineKeyboardBuilder()
    builder.row(
        InlineKeyboardButton(text="✅ Підтвердити", callback_data="single:confirm"),
        InlineKeyboardButton(text="❌ Скасувати", callback_data="single:cancel"),
    )

    fiat_amount = amount * data.get("price", 0)
    await message.answer(
        f"{icon} <b>Підтвердження {label}</b>\n\n"
        f"Біржа: <b>{data.get('exchange')}</b>\n"
        f"Об'єм: <code>{amount:.2f} USDT</code> (~{fiat_amount:.0f} ₴)\n"
        f"Ціна: <code>{data.get('price', 0):.4f}</code>\n\n"
        f"⚠️ <i>Натисни ✅ для виконання.</i>",
        reply_markup=builder.as_markup(),
    )


@router.callback_query(F.data.in_({"single:confirm", "single:cancel"}))
async def on_single_leg_confirm(call: CallbackQuery, state: FSMContext) -> None:
    if call.data == "single:cancel":
        await state.clear()
        with suppress(TelegramBadRequest):
            await call.message.edit_text("🚫 Single-Leg скасовано.", reply_markup=back_to_main_kb())
        return await call.answer("Скасовано.")

    data = await state.get_data()
    await state.clear()

    if not _single_leg_executor:
        return await call.answer("❌ SingleLegExecutor не підключено!", show_alert=True)

    action = data.get("single_action", "BUY")
    amount_usdt = data.get("amount_usdt", 0)

    with suppress(TelegramBadRequest):
        await call.message.edit_text(
            f"⏳ <b>Виконується {action}…</b>\nПеревіряю LLM, створюю ордер.",
            reply_markup=None,
        )

    try:
        if action == "BUY":
            result = await _single_leg_executor.execute_single_buy(
                exchange=data.get("exchange", ""),
                ad_id=data.get("ad_id", ""),
                price=data.get("price", 0),
                amount_usdt=amount_usdt,
                merchant_id=data.get("merchant_id", ""),
                owner_user_id=call.from_user.id,
                payment_method=data.get("payment_method", ""),
            )
        else:
            result = await _single_leg_executor.execute_single_sell(
                exchange=data.get("exchange", ""),
                ad_id=data.get("ad_id", ""),
                price=data.get("price", 0),
                amount_usdt=amount_usdt,
                merchant_id=data.get("merchant_id", ""),
                owner_user_id=call.from_user.id,
                payment_method=data.get("payment_method", ""),
            )

        if result["success"]:
            text = (
                f"✅ <b>{action} виконано!</b>\n"
                f"Ордер: <code>{result['order_id']}</code>\n"
                f"Trade ID: #{result['trade_id']}"
            )
            if result.get("warning"):
                text += f"\n\n⚠️ {result['warning']}"
        else:
            text = f"❌ <b>Помилка {action}:</b> {result.get('error', 'Unknown')}"
            if result.get("warning"):
                text += f"\n\n⚠️ {result['warning']}"

    except Exception as e:
        logger.error("single_leg confirm error: %s", e, exc_info=True)
        text = f"❌ <b>Критична помилка:</b> <code>{str(e)[:200]}</code>"

    with suppress(TelegramBadRequest):
        await call.message.edit_text(text, reply_markup=back_to_main_kb())
    await call.answer()


# =========================================================================
# 📊 /stats — Статистика торгівлі
# =========================================================================

@router.message(Command("stats"))
async def cmd_stats(message: Message) -> None:
    """Вхід у статистику через команду /stats (1-й рівень)"""
    if not _db:
        return await message.answer("❌ БД не підключена.")

    text = "📊 <b>Аналітичний центр Arbix Quantum</b>\n\nОберіть джерело даних:"
    await message.answer(text, reply_markup=keyboards.stats_source_kb(), parse_mode="HTML")


# =========================================================================
# 🩺 /sessions — Стан auth-сесій
# =========================================================================

@router.message(Command("sessions"))
async def cmd_sessions(message: Message) -> None:
    if not _db:
        return await message.answer("❌ БД не підключена.")

    from core.workers.session_manager import SESSION_TTL
    sessions = await _db.get_all_auth_sessions()

    if not sessions:
        return await message.answer("📭 Жодних auth-сесій не знайдено.")

    lines = ["🩺 <b>Auth-сесії (Browser Sessions):</b>\n"]
    import time as _time
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
            status = f"🟢 OK ({remaining_h:.0f}г лишилось)"

        lines.append(
            f"{'🟢' if is_active else '🔴'} <b>{exchange}</b>: {status}\n"
            f"  Вік: {age_h:.1f}г / TTL: {ttl_h:.0f}г"
        )

    await message.answer("\n".join(lines))