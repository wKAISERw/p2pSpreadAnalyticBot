# bot/commands.py
"""
Telegram Command Center — команди бота.

Команди:
  /start      — привітання
  /status     — стан сканера (цикл, черги, боти)
  /connect    — підключення API ключів біржі
  /disconnect — видалення API ключів
  /keys       — список підключених бірж
  /balance    — баланс на всіх підключених біржах
  /settings   — перегляд і зміна налаштувань
  /ban        — ручний бан мерчанта
  /help       — список команд

Архітектура: команди реєструються в TelegramNotifier через setup_commands().
Стан зберігається в _conversation_state для multi-step флоу (/connect).
"""
from __future__ import annotations

import asyncio
import logging
from typing import TYPE_CHECKING, Optional

from aiogram import Router
from aiogram.filters import Command
from aiogram.types import Message, InlineKeyboardMarkup, InlineKeyboardButton, CallbackQuery
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup

from config import settings
from config.runtime import runtime_config, ALLOWED_KEYS

if TYPE_CHECKING:
    from core.storage.merchant_db import MerchantDB
    from infrastructure.api.bybit_account import BybitAccountClient
    from infrastructure.api.binance_account import BinanceAccountClient
    from infrastructure.api.okx_account import OKXAccountClient
    from infrastructure.api.mexc_account import MEXCAccountClient

logger = logging.getLogger("Commands")

router = Router()


# ── FSM стани для /connect ─────────────────────────────────────────────────
class ConnectStates(StatesGroup):
    waiting_exchange = State()
    waiting_api_key = State()
    waiting_api_secret = State()
    waiting_passphrase = State()  # тільки для OKX


# ── Посилання на глобальні об'єкти (заповнюються з scanner.py) ─────────────
_db: Optional["MerchantDB"] = None
_account_clients: dict = {}  # {"Bybit": BybitAccountClient, ...}
_scanner_stats: dict = {
    "cycles": 0,
    "last_cycle_ms": 0,
    "bots_detected_today": 0,
    "spreads_found_today": 0,
}


def setup(db, account_clients: dict) -> None:
    """
    Ініціалізація команд.
    Викликається з scanner.py після старту всіх компонентів.

    db: MerchantDB instance
    account_clients: {"Bybit": BybitAccountClient, "Binance": BinanceAccountClient, ...}
    """
    global _db, _account_clients
    _db = db
    _account_clients = account_clients
    logger.info("Commands: initialized with %d account clients", len(account_clients))


def update_stats(**kwargs) -> None:
    """Оновлює статистику сканера. Викликається з scanner.py."""
    _scanner_stats.update(kwargs)


# ── /start ─────────────────────────────────────────────────────────────────
@router.message(Command("start"))
async def cmd_start(message: Message) -> None:
    # Реєструємо юзера при першому старті
    if _db:
        await _db.register_user(
            user_id=message.from_user.id,
            chat_id=message.chat.id,
        )

    text = (
        "👋 <b>ARBIX QUANTUM</b>\n\n"
        "P2P арбітражний сканер з антифрод захистом.\n\n"
        "📋 <b>Команди:</b>\n"
        "/status — стан сканера\n"
        "/balance — баланси на біржах\n"
        "/connect — підключити API ключі біржі\n"
        "/disconnect — відключити біржу\n"
        "/keys — підключені біржі\n"
        "/settings — налаштування\n"
        "/ban — заблокувати мерчанта\n"
        "/help — детальна допомога\n"
    )
    await message.answer(text)


# ── /help ──────────────────────────────────────────────────────────────────
@router.message(Command("help"))
async def cmd_help(message: Message) -> None:
    text = (
        "📖 <b>Детальна допомога</b>\n\n"
        "<b>/connect [біржа]</b>\n"
        "Підключити API ключі для біржі.\n"
        "Підтримувані: Binance, Bybit, OKX, MEXC\n"
        "Ключі зберігаються зашифровано.\n\n"
        "<b>/balance</b>\n"
        "Показує баланс USDT/UAH на всіх підключених біржах.\n\n"
        "<b>/settings [ключ] [значення]</b>\n"
        "Змінити налаштування без перезапуску.\n"
        f"Доступні ключі: {', '.join(sorted(ALLOWED_KEYS))}\n\n"
        "<b>/ban [exchange] [merchant_id]</b>\n"
        "Ручний бан мерчанта в чорний список.\n\n"
        "<b>/status</b>\n"
        "Поточний стан сканера, черги LLM, кількість знайдених ботів."
    )
    await message.answer(text)


# ── /status ────────────────────────────────────────────────────────────────
@router.message(Command("status"))
async def cmd_status(message: Message) -> None:
    if not _db:
        return await message.answer("❌ База даних не ініціалізована")

    # Підключені біржі
    connected = []
    for exchange, client in _account_clients.items():
        if getattr(client, "is_authenticated", False):
            connected.append(f"✅ {exchange}")
        else:
            connected.append(f"❌ {exchange}")

    # Runtime settings
    capital = runtime_config.get("working_capital_uah", settings.working_capital_uah)
    spread = runtime_config.get("min_spread_pct", settings.min_spread_pct)
    risk = runtime_config.get("risk_mode", settings.risk_mode)

    text = (
            "📊 <b>Стан сканера</b>\n\n"
            f"⚡ Останній цикл: <code>{_scanner_stats.get('last_cycle_ms', 0):.0f}ms</code>\n"
            f"🔄 Циклів всього: <code>{_scanner_stats.get('cycles', 0)}</code>\n"
            f"🤖 Ботів сьогодні: <code>{_scanner_stats.get('bots_detected_today', 0)}</code>\n"
            f"📈 Спредів сьогодні: <code>{_scanner_stats.get('spreads_found_today', 0)}</code>\n\n"
            f"💼 Капітал: <code>{capital} ₴</code>\n"
            f"📉 Поріг спреду: <code>{spread}%</code>\n"
            f"🛡 Режим ризику: <code>{risk}</code>\n\n"
            "🔌 <b>Біржі:</b>\n" + "\n".join(connected)
    )
    await message.answer(text)


# ── /keys ──────────────────────────────────────────────────────────────────
@router.message(Command("keys"))
async def cmd_keys(message: Message) -> None:
    if not _db:
        return await message.answer("❌ База даних не ініціалізована")

    all_creds = await _db.get_all_credentials()
    if not all_creds:
        return await message.answer(
            "🔑 Жодних API ключів не підключено.\n"
            "Використай /connect щоб додати."
        )

    lines = ["🔑 <b>Підключені API ключі:</b>\n"]
    for exchange, creds in all_creds.items():
        key_preview = creds["api_key"][:8] + "..." if creds["api_key"] else "?"
        label = f" ({creds['label']})" if creds.get("label") else ""
        lines.append(f"✅ <b>{exchange}</b>{label}: <code>{key_preview}</code>")

    await message.answer("\n".join(lines))


# ── /connect ───────────────────────────────────────────────────────────────
SUPPORTED_EXCHANGES = ["Binance", "Bybit", "OKX", "MEXC"]


@router.message(Command("connect"))
async def cmd_connect(message: Message, state: FSMContext) -> None:
    parts = message.text.split(maxsplit=1)

    if len(parts) > 1:
        exchange = parts[1].strip().capitalize()
        if exchange not in SUPPORTED_EXCHANGES:
            return await message.answer(
                f"❌ Невідома біржа: {exchange}\n"
                f"Підтримувані: {', '.join(SUPPORTED_EXCHANGES)}"
            )
        await state.update_data(exchange=exchange)
        await state.set_state(ConnectStates.waiting_api_key)
        return await message.answer(
            f"🔑 <b>Підключення {exchange}</b>\n\n"
            f"Введи API Key:"
        )

    # Показуємо кнопки вибору біржі
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=ex, callback_data=f"connect:{ex}")]
        for ex in SUPPORTED_EXCHANGES
    ])
    await message.answer("🔌 Виберіть біржу для підключення:", reply_markup=kb)


@router.callback_query(lambda c: c.data and c.data.startswith("connect:"))
async def on_connect_exchange(call: CallbackQuery, state: FSMContext) -> None:
    exchange = call.data.split(":")[1]
    await state.update_data(exchange=exchange)
    await state.set_state(ConnectStates.waiting_api_key)
    await call.message.edit_text(
        f"🔑 <b>Підключення {exchange}</b>\n\n"
        f"Введи API Key:\n"
        f"<i>(повідомлення буде видалено після збереження)</i>"
    )
    await call.answer()


@router.message(ConnectStates.waiting_api_key)
async def on_api_key(message: Message, state: FSMContext) -> None:
    api_key = message.text.strip()
    # Видаляємо повідомлення з ключем одразу
    try:
        await message.delete()
    except Exception:
        pass

    if len(api_key) < 10:
        return await message.answer("❌ API Key занадто короткий. Спробуй ще раз:")

    await state.update_data(api_key=api_key)
    await state.set_state(ConnectStates.waiting_api_secret)
    await message.answer(
        "✅ API Key отримано.\n\n"
        "Тепер введи <b>Secret Key</b>:\n"
        "<i>(повідомлення буде видалено після збереження)</i>"
    )


@router.message(ConnectStates.waiting_api_secret)
async def on_api_secret(message: Message, state: FSMContext) -> None:
    api_secret = message.text.strip()
    try:
        await message.delete()
    except Exception:
        pass

    if len(api_secret) < 10:
        return await message.answer("❌ Secret Key занадто короткий. Спробуй ще раз:")

    data = await state.get_data()
    exchange = data["exchange"]

    # OKX вимагає passphrase
    if exchange == "OKX":
        await state.update_data(api_secret=api_secret)
        await state.set_state(ConnectStates.waiting_passphrase)
        return await message.answer(
            "✅ Secret Key отримано.\n\n"
            "OKX вимагає <b>Passphrase</b>\n"
            "(той що ти задав при створенні ключа):"
        )

    # Зберігаємо credentials
    await _save_credentials(message, state, exchange, data["api_key"], api_secret)


@router.message(ConnectStates.waiting_passphrase)
async def on_passphrase(message: Message, state: FSMContext) -> None:
    passphrase = message.text.strip()
    try:
        await message.delete()
    except Exception:
        pass

    data = await state.get_data()
    await _save_credentials(
        message, state, data["exchange"],
        data["api_key"], data["api_secret"], passphrase
    )


async def _save_credentials(
        message: Message,
        state: FSMContext,
        exchange: str,
        api_key: str,
        api_secret: str,
        passphrase: str = "",
) -> None:
    """Зберігає credentials і оновлює account client."""
    await state.clear()

    if not _db:
        return await message.answer("❌ База даних недоступна")

    # 🚀 ХОТФІКС: Зберігаємо ключі під ID=0, щоб сканер міг їх знайти як системні!
    ok = await _db.save_credentials(
        exchange=exchange,
        api_key=api_key,
        api_secret=api_secret,
        passphrase=passphrase,
        label="system_keys",
        user_id=0,
    )

    if not ok:
        return await message.answer(f"❌ Помилка збереження credentials для {exchange}")

    # Оновлюємо account client
    client = _account_clients.get(exchange)
    if client:
        if exchange == "OKX":
            client.set_credentials(api_key, api_secret, passphrase)
        else:
            client.set_credentials(api_key, api_secret)
        logger.info("Commands: %s credentials updated in account client", exchange)

    await message.answer(
        f"✅ <b>{exchange}</b> успішно підключено (Системні ключі)!\n\n"
        f"Ключ: <code>{api_key[:8]}...</code>\n\n"
        f"Використай /balance щоб перевірити підключення."
    )


# ── /disconnect ────────────────────────────────────────────────────────────
@router.message(Command("disconnect"))
async def cmd_disconnect(message: Message) -> None:
    parts = message.text.split(maxsplit=1)
    if len(parts) < 2:
        # Показуємо кнопки
        if not _db:
            return await message.answer("❌ База даних не ініціалізована")
        all_creds = await _db.get_all_credentials()
        if not all_creds:
            return await message.answer("Немає підключених бірж.")
        kb = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text=f"❌ {ex}", callback_data=f"disconnect:{ex}")]
            for ex in all_creds
        ])
        return await message.answer("Виберіть біржу для відключення:", reply_markup=kb)

    exchange = parts[1].strip().capitalize()
    await _do_disconnect(message, exchange)


@router.callback_query(lambda c: c.data and c.data.startswith("disconnect:"))
async def on_disconnect(call: CallbackQuery) -> None:
    exchange = call.data.split(":")[1]
    await call.message.edit_reply_markup(reply_markup=None)
    await _do_disconnect(call.message, exchange)
    await call.answer()


async def _do_disconnect(message: Message, exchange: str) -> None:
    if not _db:
        return await message.answer("❌ База даних недоступна")
    ok = await _db.delete_credentials(exchange)
    if ok:
        # Скидаємо credentials в account client
        client = _account_clients.get(exchange)
        if client:
            client.set_credentials("", "")
        await message.answer(f"✅ {exchange} відключено.")
    else:
        await message.answer(f"❌ Не вдалося відключити {exchange}.")


# ── /balance ───────────────────────────────────────────────────────────────
@router.message(Command("balance"))
async def cmd_balance(message: Message) -> None:
    if not _account_clients:
        return await message.answer("❌ Account clients не ініціалізовані")

    msg = await message.answer("⏳ Отримую баланси...")
    lines = ["💰 <b>Баланси на біржах</b>\n"]
    found_any = False

    for exchange, client in _account_clients.items():
        if not getattr(client, "is_authenticated", False):
            lines.append(f"⚪ <b>{exchange}</b>: не підключено")
            continue

        try:
            balances = await client.get_balance()
            if not balances:
                lines.append(f"📭 <b>{exchange}</b>: порожньо або помилка")
                continue

            found_any = True
            lines.append(f"\n🏦 <b>{exchange}</b>:")
            for b in balances:
                coin = b.get("coin", "?")
                free = float(b.get("free", 0))
                total = float(b.get("total", free))
                locked = total - free
                if total > 0:
                    if locked > 0:
                        lines.append(f"  {coin}: <code>{free:.2f}</code> (заблок: <code>{locked:.2f}</code>)")
                    else:
                        lines.append(f"  {coin}: <code>{free:.2f}</code>")
        except Exception as e:
            lines.append(f"❌ <b>{exchange}</b>: {e}")

    if not found_any:
        lines.append("\n<i>Підключи API ключі через /connect</i>")

    await msg.edit_text("\n".join(lines))


# ── /settings ──────────────────────────────────────────────────────────────
@router.message(Command("settings"))
async def cmd_settings(message: Message) -> None:
    parts = message.text.split(maxsplit=2)

    # /settings без аргументів — показати поточні
    if len(parts) == 1:
        lines = ["⚙️ <b>Поточні налаштування</b>\n"]
        for key in sorted(ALLOWED_KEYS):
            val = runtime_config.get(key, getattr(settings, key, "—"))
            lines.append(f"<code>{key}</code>: <b>{val}</b>")
        lines.append(f"\n<i>Змінити: /settings [ключ] [значення]</i>")
        return await message.answer("\n".join(lines))

    if len(parts) < 3:
        return await message.answer(
            "❌ Формат: <code>/settings [ключ] [значення]</code>\n"
            f"Доступні ключі: {', '.join(sorted(ALLOWED_KEYS))}"
        )

    key, value = parts[1].strip(), parts[2].strip()
    if key not in ALLOWED_KEYS:
        return await message.answer(
            f"❌ Ключ <code>{key}</code> недоступний.\n"
            f"Доступні: {', '.join(sorted(ALLOWED_KEYS))}"
        )

    ok = await runtime_config.set(key, value)
    if ok:
        await message.answer(f"✅ <code>{key}</code> = <b>{value}</b>")
    else:
        await message.answer(f"❌ Не вдалося зберегти налаштування.")


# ── /ban ───────────────────────────────────────────────────────────────────
@router.message(Command("ban"))
async def cmd_ban(message: Message) -> None:
    parts = message.text.split(maxsplit=3)
    if len(parts) < 3:
        return await message.answer(
            "❌ Формат: <code>/ban [exchange] [merchant_id]</code>\n"
            "Приклад: <code>/ban Binance s42ee507f2dc</code>"
        )

    exchange = parts[1].strip()
    merchant_id = parts[2].strip()
    reason = parts[3].strip() if len(parts) > 3 else "Ручний бан через бота"

    if not _db:
        return await message.answer("❌ База даних не ініціалізована")

    await _db.add_to_blacklist(exchange, merchant_id, "Unknown", reason, "manual_cmd")
    await message.answer(
        f"⛔ <b>Заблоковано</b>\n"
        f"Біржа: <code>{exchange}</code>\n"
        f"ID: <code>{merchant_id}</code>\n"
        f"Причина: {reason}"
    )