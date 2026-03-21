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

from config import settings
from config.runtime import runtime_config, ALLOWED_KEYS
from bot.keyboards import (
    main_menu_kb, settings_menu_kb, keys_menu_kb,
    back_to_main_kb, global_settings_kb, back_to_settings_kb,
    banks_selection_kb, back_to_keys_kb, exchange_connect_kb,
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
    waiting_spread = State()


class GlobalSettingStates(StatesGroup):
    waiting_value = State()


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


def setup(db, account_clients: dict) -> None:
    global _db, _account_clients
    _db = db
    _account_clients = account_clients


def update_stats(**kwargs) -> None:
    _scanner_stats.update(kwargs)


# ── /start (ГОЛОВНИЙ ДАШБОРД) ──────────────────────────────────────────────
async def _generate_dashboard_text(user_id: int) -> tuple[str, bool]:
    user_capital = "5100.0"
    user_spread = "0.50"
    if _db:
        active_users = await _db.get_active_users()
        for u in active_users:
            if u["user_id"] == user_id:
                user_capital = f"{u['capital']:.1f}"
                user_spread = f"{u['min_spread']:.2f}"
                break

    is_active = runtime_config.get("is_scanner_active", "false") == "true"
    status_text = "🟢 <b>АКТИВНИЙ (Парсинг іде)</b>" if is_active else "🔴 <b>ЗУПИНЕНИЙ (Пауза)</b>"

    text = (
        "👋 <b>ARBIX QUANTUM | Особистий кабінет</b>\n\n"
        f"Статус ядра: {status_text}\n\n"
        "🛠 <b>Твої персональні фільтри:</b>\n"
        f"├ Робочий капітал: <b>{user_capital} ₴</b>\n"
        f"└ Мінімальний спред: <b>{user_spread}%</b>\n\n"
        "<i>👇 Використовуй меню нижче для управління:</i>"
    )
    return text, is_active


@router.message(Command("start"))
async def cmd_start(message: Message, state: FSMContext) -> None:
    await state.clear()
    if _db:
        await _db.register_user(user_id=message.from_user.id, chat_id=message.chat.id)

    text, is_active = await _generate_dashboard_text(message.from_user.id)
    await message.answer(text, reply_markup=main_menu_kb(is_active, is_muted()))


# ── /help ──────────────────────────────────────────────────────────────────
@router.message(Command("help"))
async def cmd_help(message: Message) -> None:
    text = (
        "📖 <b>Детальна допомога</b>\n\n"
        "<b>/connect [біржа]</b>\nПідключити персональні API ключі.\n\n"
        "<b>/balance</b>\nПоказує баланс на твоїх біржах.\n\n"
        "<b>/settings</b>\nЗмінити глобальні налаштування сканера.\n\n"
        "<b>/ban [exchange] [merchant_id]</b>\nРучний бан мерчанта.\n\n"
        "<b>/status</b>\nПоточний стан сканера."
    )
    await message.answer(text)


# ── /status ────────────────────────────────────────────────────────────────
@router.message(Command("status"))
async def cmd_status(message: Message) -> None:
    if not _db:
        return await message.answer("❌ База даних не ініціалізована")

    connected = []
    for exchange, client in _account_clients.items():
        if getattr(client, "is_authenticated", False):
            connected.append(f"✅ {exchange}")
        else:
            connected.append(f"❌ {exchange}")

    capital = runtime_config.get("working_capital_uah", settings.working_capital_uah)
    spread = runtime_config.get("min_spread_pct", settings.min_spread_pct)
    risk = runtime_config.get("risk_mode", settings.risk_mode)

    llm_q = _scanner_stats.get("llm_queue", 0)
    rev_q = _scanner_stats.get("review_queue", 0)
    cb_st = _scanner_stats.get("cb_status", {})
    cb_lines = [f"  {'🟢' if v == 'CLOSED' else '🔴'} {k}: {v}" for k, v in cb_st.items()]
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
            f"🛡 Ризик: <code>{risk}</code>\n\n"
            "🔌 <b>API:</b>\n" + "\n".join(connected) +
            ("\n\n⚡ <b>Circuit Breakers:</b>\n" + "\n".join(cb_lines) if cb_lines else "")
    )
    await message.answer(text)


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
SUPPORTED_EXCHANGES = ["Binance", "Bybit", "OKX", "MEXC"]


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
    await state.set_state(ConnectStates.waiting_api_key)
    await call.message.edit_text(f"🔑 <b>Підключення {exchange}</b>\n\nВведи API Key:")
    await call.answer()


@router.message(ConnectStates.waiting_api_key)
async def on_api_key(message: Message, state: FSMContext) -> None:
    api_key = message.text.strip()
    with suppress(Exception): await message.delete()
    if len(api_key) < 10:
        return await message.answer("❌ API Key занадто короткий. Спробуй ще раз:")
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
    await message.answer(_generate_settings_text(), reply_markup=global_settings_kb(_KEY_LABELS))


@router.callback_query(F.data == "menu:global_settings")
async def on_global_settings_menu(call: CallbackQuery, state: FSMContext) -> None:
    await state.clear()
    with suppress(TelegramBadRequest):
        await call.message.edit_text(_generate_settings_text(), reply_markup=global_settings_kb(_KEY_LABELS))
    await call.answer()


@router.callback_query(F.data.startswith("gset:"))
async def on_gset_click(call: CallbackQuery, state: FSMContext) -> None:
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
        await message.answer("❌ Формат невірний. Введи число (наприклад: 5100.0 або просто 5100)")
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
        await call.message.edit_text(text, reply_markup=main_menu_kb(is_active, is_muted()))
    await call.answer()


@router.callback_query(F.data.in_(["scanner:start", "scanner:stop"]))
async def on_scanner_toggle(call: CallbackQuery) -> None:
    new_state = "true" if call.data == "scanner:start" else "false"
    await runtime_config.set("is_scanner_active", new_state)
    text, is_active = await _generate_dashboard_text(call.from_user.id)
    with suppress(TelegramBadRequest):
        await call.message.edit_text(text, reply_markup=main_menu_kb(is_active, is_muted()))
    action = "ЗАПУЩЕНО! Парсинг почався" if is_active else "ЗУПИНЕНО! Парсинг на паузі"
    await call.answer(f"✅ Сканер {action}!", show_alert=True)


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


@router.callback_query(F.data == "set:banks")
async def on_set_banks(call: CallbackQuery) -> None:
    """Показує меню вибору банків з галочками."""
    current_banks: list[str] = []
    if _db:
        users = await _db.get_active_users()
        for u in users:
            if u["user_id"] == call.from_user.id:
                current_banks = u["bank_codes"]
                break
    if not current_banks:
        current_banks = DEFAULT_BANK_CODES

    with suppress(TelegramBadRequest):
        await call.message.edit_text(
            "🏦 <b>Вибір банків</b>\n\n"
            "Обери банки для яких шукати спреди.\n"
            "<i>Натискай для вмикання/вимикання:</i>",
            reply_markup=banks_selection_kb(BANK_NAMES, current_banks),
        )
    await call.answer()


@router.callback_query(F.data.startswith("bank:toggle:"))
async def on_bank_toggle(call: CallbackQuery, state: FSMContext) -> None:
    """Перемикає банк і оновлює повідомлення з галочками."""
    code = call.data.split(":", 2)[2]

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
    await state.update_data(selected_banks=selected)

    with suppress(TelegramBadRequest):
        await call.message.edit_reply_markup(
            reply_markup=banks_selection_kb(BANK_NAMES, selected)
        )
    await call.answer()


@router.callback_query(F.data == "bank:save")
async def on_bank_save(call: CallbackQuery, state: FSMContext) -> None:
    """Зберігає вибрані банки в БД."""
    data = await state.get_data()
    selected = data.get("selected_banks", DEFAULT_BANK_CODES)
    await state.clear()

    if _db:
        conn = getattr(_db, "db", None) or getattr(_db, "_db", _db)
        await conn.execute(
            "UPDATE scanner_users SET bank_codes = ? WHERE user_id = ?",
            (",".join(selected), call.from_user.id),
        )
        await conn.commit()

    bank_names = [BANK_NAMES.get(c, c) for c in selected]
    with suppress(TelegramBadRequest):
        await call.message.edit_text(
            f"✅ <b>Банки збережено!</b>\n\n"
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
            f"Щоб увімкнути — натисни /start або кнопку нижче.",
            reply_markup=back_to_main_kb(),
        )
    await call.answer(f"🔕 Пауза на {hours:.0f} год")

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