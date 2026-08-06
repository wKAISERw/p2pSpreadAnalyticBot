
# bot/handlers/core.py
# Globals, setup, FSM states, shared helpers.

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

class GlobalProxy:
    __slots__ = ("_var_name",)
    def __init__(self, var_name: str):
        object.__setattr__(self, "_var_name", var_name)
        
    def _get_target(self):
        return globals()[object.__getattribute__(self, "_var_name")]
        
    def __getattr__(self, name):
        target = self._get_target()
        if target is None:
            raise AttributeError(f"Global object '{object.__getattribute__(self, '_var_name')}' is not initialized")
        return getattr(target, name)

    def __setattr__(self, name, value):
        target = self._get_target()
        if target is None:
            raise AttributeError(f"Global object '{object.__getattribute__(self, '_var_name')}' is not initialized")
        setattr(target, name, value)

    def __bool__(self) -> bool:
        return self._get_target() is not None

    def __str__(self) -> str:
        target = self._get_target()
        return str(target) if target is not None else "None"

    def __repr__(self) -> str:
        target = self._get_target()
        return repr(target) if target is not None else "None"

    def __getitem__(self, item):
        target = self._get_target()
        if target is None:
            raise KeyError(f"Global object '{object.__getattribute__(self, '_var_name')}' is not initialized")
        return target[item]

    def __setitem__(self, key, value):
        target = self._get_target()
        if target is None:
            raise KeyError(f"Global object '{object.__getattribute__(self, '_var_name')}' is not initialized")
        target[key] = value

    def __len__(self) -> int:
        target = self._get_target()
        if target is None:
            return 0
        return len(target)

    def __contains__(self, item) -> bool:
        target = self._get_target()
        return item in target if target is not None else False

    def __iter__(self):
        target = self._get_target()
        if target is None:
            return iter([])
        return iter(target)

_trade_worker_impl = None
_single_leg_executor_impl = None
_maker_monitor_impl = None
_session_manager_impl = None

_trade_worker = GlobalProxy("_trade_worker_impl")
_single_leg_executor = GlobalProxy("_single_leg_executor_impl")
_maker_monitor = GlobalProxy("_maker_monitor_impl")
_session_manager = GlobalProxy("_session_manager_impl")
_active_repricers: dict = {}  # ad_id → asyncio.Task (AdRepricer)

# ── Кеші для Single-Leg / Spread кнопок (заповнюються з notifier.py) ──────
_single_leg_cache: dict = {}  # "b:<key>" / "s:<key>" → {ad_id, exchange, price, ...}
_spread_cache: dict = {}  # "<key>" → (SpreadAlert, timestamp)
from core.utils.cache import TTLCache as _TTLCache
_taker_order_cache: _TTLCache = _TTLCache(ttl_seconds=300.0, max_size=500)
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


class QRStates(StatesGroup):
    waiting_for_code = State()


class SessionStates(StatesGroup):
    waiting_for_cookies = State()


class SettingStates(StatesGroup):
    waiting_capital = State()
    waiting_min_amount = State()
    waiting_spread = State()
    waiting_spread_min = State()
    waiting_spread_max = State()
    waiting_cold_card_limit = State()


class GlobalSettingStates(StatesGroup):
    waiting_value = State()


class MerchantFilterStates(StatesGroup):
    waiting_min_orders = State()
    waiting_min_rate = State()
    waiting_ex_min_orders = State()
    waiting_ex_min_rate = State()
    waiting_min_account_age = State()
    waiting_min_positive_rate = State()
    waiting_ex_min_account_age = State()
    waiting_ex_min_positive_rate = State()
    waiting_max_offline = State()
    waiting_ex_max_offline = State()


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
    waiting_amount = State()  # 1 — обʼєм USDT
    waiting_price_strategy = State()  # 2 — стратегія ціни (inline-меню)
    waiting_price_to = State()  # 3 — max/exact ціна (або range-max)
    waiting_price_from = State()  # 3b — range-min ціна
    waiting_limit_min = State()  # 4 — мін ліміт UAH
    waiting_limit_max = State()  # 4b — макс ліміт UAH
    waiting_banks = State()  # 5 — банки (inline-чекбокси)
    waiting_speed = State()  # 6 — швидкість


class TakerSellSettingsStates(StatesGroup):
    waiting_amount_type = State() # UAH or USDT
    waiting_amount = State()  # крок 1 — об'єм
    waiting_buy_price = State()  # крок 2 — ціна купівлі
    waiting_exchange = State()  # крок 3 — біржа (Network Fee)
    waiting_profit = State()  # крок 4 — спред % (з breakeven підказкою)
    waiting_speed = State()  # крок 5 — час важливий?


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
_db_impl = None
_bot_impl = None
_notifier_impl = None
_account_clients_impl: dict = {}

_db = GlobalProxy("_db_impl")
_bot = GlobalProxy("_bot_impl")
_notifier = GlobalProxy("_notifier_impl")
_account_clients = GlobalProxy("_account_clients_impl")

_scanner_stats: dict = {
    "cycles": 0,
    "last_cycle_ms": 0,
    "bots_detected_today": 0,
    "spreads_found_today": 0,
    "llm_queue": 0,
    "review_queue": 0,
    "cb_status": {},
    "internet_connected": True,
}
_mute_until: float = 0.0

# Дата, на яку рахуються *_today лічильники (локальний день).
_stats_day: str = ""


def is_muted() -> bool:
    """Перевіряє чи бот на паузі."""
    import time
    return time.monotonic() < _mute_until


def setup(db, account_clients: dict, trade_worker=None, notifier=None, single_leg_executor=None,
          maker_monitor=None, bot=None, session_manager=None) -> None:
    global _db_impl, _account_clients_impl, _trade_worker_impl, _notifier_impl, _single_leg_executor_impl, _maker_monitor_impl, _bot_impl, _session_manager_impl
    if _trade_worker_impl and trade_worker and _trade_worker_impl is not trade_worker:
        logger.warning("setup(): TradeWorker перезаписується!")
    _db_impl = db
    _account_clients_impl = account_clients
    _trade_worker_impl = trade_worker
    if session_manager is not None:
        _session_manager_impl = session_manager

    if notifier is not None:
        _notifier_impl = notifier
        # Автоматичний fallback: якщо bot не передали явно, беремо його з нотифікатора
        if hasattr(notifier, "_bot") and notifier._bot is not None:
            _bot_impl = notifier._bot

    if bot is not None:
        _bot_impl = bot

    if single_leg_executor is not None:
        _single_leg_executor_impl = single_leg_executor
    if maker_monitor is not None:
        _maker_monitor_impl = maker_monitor


def update_stats(**kwargs) -> None:
    """
    Єдина точка запису статистики сканера.

    Раніше метрики жили у двох місцях: `_scanner_stats` (звідки читає Telegram)
    і `state.stats` (звідки читає /api/v1/stats). Писали тільки в перше, тому
    веб-дашборд вічно показував 0 циклів і всі CircuitBreaker'и «CLOSED».
    Тепер обидва сховища оновлюються синхронно.
    """
    _scanner_stats.update(kwargs)
    try:
        from state import state
        state.stats.update(kwargs)
    except Exception:  # pragma: no cover — state імпортується завжди
        pass


def bump_stat(key: str, amount: int = 1) -> None:
    """
    Інкрементує денний лічильник (`*_today`) зі скиданням на новий день.
    До цього `bots_detected_today` і `spreads_found_today` були оголошені,
    читались в /status, але не інкрементувались ніде — тобто завжди 0.
    """
    global _stats_day
    from datetime import date

    today = date.today().isoformat()
    if _stats_day != today:
        _stats_day = today
        for k in _scanner_stats:
            if k.endswith("_today"):
                _scanner_stats[k] = 0

    _scanner_stats[key] = _scanner_stats.get(key, 0) + amount
    try:
        from state import state
        state.stats[key] = _scanner_stats[key]
    except Exception:  # pragma: no cover
        pass


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
                card_settings = await _db.get_user_card_settings(user_id)
                card_module_enabled = card_settings and card_settings.get("card_module_mode") != "off"
                if u.get("capital_mode") == "auto":
                    auto_cap = await _db.get_user_auto_capital(user_id)
                    user_capital = f"{auto_cap:.1f} (Авто)"
                else:
                    manual_cap = float(u['capital'])
                    if card_module_enabled:
                        auto_cap = await _db.get_user_auto_capital(user_id)
                        if auto_cap > 0 and auto_cap < manual_cap:
                            user_capital = f"{manual_cap:.1f} (Обмеж. до {auto_cap:.1f})"
                        else:
                            user_capital = f"{manual_cap:.1f}"
                    else:
                        user_capital = f"{manual_cap:.1f}"
                strategy = u.get("spread_strategy", "min")
                min_sp = u.get("min_spread", 0.5)
                max_sp = u.get("max_spread", 0.0)
                if strategy == "min":
                    user_spread = f"≥ {min_sp:.2f}%"
                elif strategy == "max":
                    user_spread = f"≤ {max_sp:.2f}%"
                elif strategy == "range":
                    user_spread = f"{min_sp:.2f}% – {max_sp:.2f}%"
                elif strategy == "exact":
                    user_spread = f"≈ {min_sp:.2f}%"
                else:
                    user_spread = f"{min_sp:.2f}%"
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


