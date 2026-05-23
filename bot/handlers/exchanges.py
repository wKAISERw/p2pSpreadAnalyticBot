# bot/handlers/exchanges.py
# API keys, connect/disconnect, exchange toggles, cooldowns, health.

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
    ConnectStates, ExchangeCooldownStates, SETTING_DESCRIPTIONS, _mute_until, _scanner_stats, _KEY_LABELS,
    GlobalSettingStates, SettingStates
)
from bot.keyboards import exchanges_status_kb, global_settings_kb, back_to_settings_kb, back_to_main_kb, \
    exchange_cooldown_kb, main_menu_kb, exchange_toggle_kb, exchange_connect_kb, banks_selection_kb, exchange_down_kb, \
    back_to_status_kb, settings_menu_kb, keys_menu_kb
from config.runtime import runtime_config
from config.banks import DEFAULT_BANK_CODES, BANK_NAMES
from config import settings

router = Router()
logger = logging.getLogger(__name__)

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
