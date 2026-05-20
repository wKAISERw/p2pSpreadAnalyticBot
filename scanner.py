# scanner.py
# =============================================================================
# БОЙОВА ВЕРСІЯ v2.0 (Рефакторинг: AlertDispatcher + ProviderFactory)
# =============================================================================
from __future__ import annotations
from state import state
import asyncio
import logging
import time
from dataclasses import dataclass
from typing import Optional

from config import settings
from config.banks import BankRegistry, DEFAULT_BANK_CODES, BANK_NAMES
from config.runtime import runtime_config
from bot.commands import update_stats, is_muted
from bot.notifier import TelegramNotifier, SpreadAlert
from core.engine.cross_matcher import CrossMatchingEngine
from core.engine.risk_engine import RiskEngine
from core.engine.stability import SpreadStabilityFilter
from core.engine.exchange_manager import exchange_manager
from core.engine.taker_scanner import TakerScanner
from core.engine.maker_ad_monitor import MakerAdMonitor
from core.engine.price_advisor import PriceAdvisor
from core.storage.merchant_db import MerchantDB
from core.utils.circuit_breaker import CircuitBreaker
from core.utils.dedup_cache import TTLCache
from core.workers.llm_worker import LLMWorkerPool
from core.workers.review_fetcher import ReviewFetcher
from exchanges.binance import BinanceExchange
from exchanges.bybit import BybitExchange
from exchanges.cryptobot_userbot import CryptoBotUserbot
from exchanges.mexc import MexcExchange
from exchanges.okx import OkxExchange
from exchanges.wallet import WalletExchange
from filters.merchant_filter import MerchantFilter
from filters.limit_filter import set_max_capital
from infrastructure.api.binance_account import BinanceAccountClient
from infrastructure.api.bybit_account import BybitAccountClient
from infrastructure.api.mexc_account import MEXCAccountClient
from infrastructure.api.okx_account import OKXAccountClient
from infrastructure.http.base_client import BaseHttpClient
from infrastructure.http.binance_client import BinanceClient
from infrastructure.http.bybit_p2p_client import BybitP2PClient
from infrastructure.http.mexc_client import MexcClient
from infrastructure.http.okx_client import OkxClient
from infrastructure.http.wallet_client import WalletClient
from core.workers.session_manager import SessionManager


logger = logging.getLogger("Scanner")


def safe_float(val) -> float:
    """Безпечне перетворення в float. Визначено на рівні модуля (не в циклі)."""
    try:
        return float(val)
    except (TypeError, ValueError):
        return 0.0


# ═══════════════════════════════════════════════════════════════════════════════
# AlertDispatcher — персоналізована розсилка алертів
# ═══════════════════════════════════════════════════════════════════════════════

# ═══════════════════════════════════════════════════════════════════════════════
# AlertDispatcher — персоналізована розсилка алертів з урахуванням наявних карт
# ═══════════════════════════════════════════════════════════════════════════════

class AlertDispatcher:
    """
    Відповідає за персоналізовану розсилку алертів.
    Автоматично коригує банк угоди під наявні АКТИВНІ картки користувача
    та перевіряє стан експериментальних фіч.
    """

    def __init__(self, db: MerchantDB, notifier: TelegramNotifier, cache_ttl: float = 5.0):
        self._db = db
        self._notifier = notifier
        self._cache_ttl = cache_ttl
        self._users_cache: list[dict] = []
        self._cache_loaded_at: float = 0.0

    async def _get_users(self) -> list[dict]:
        """Повертає active_users з кешем TTL=5s — не запит на кожний алерт."""
        if time.monotonic() - self._cache_loaded_at >= self._cache_ttl:
            self._users_cache = await self._db.get_active_users()
            self._cache_loaded_at = time.monotonic()
        return self._users_cache

    def _user_wants(self, user: dict, opp: dict) -> tuple[bool, str]:
        """
        Персональний фільтр юзера. Повертає (True, "") або (False, причина).
        """
        mode = user.get("scanner_mode", "SPREAD")
        if mode != "SPREAD":
            return False, f"mode={mode}"

        entry = float(opp["actual_entry_uah"])

        # 1. Капітальний коридор
        if entry > float(user["capital"]):
            return False, f"entry {entry:.0f} > capital {user['capital']}"
        min_amount = float(user.get("min_amount", 0.0))
        if min_amount > 0 and entry < min_amount:
            return False, f"entry {entry:.0f} < min_amount {min_amount:.0f}"

        # 2. Мінімальний спред
        if float(opp["net_spread_pct"]) < float(user["min_spread"]):
            return False, f"spread {opp['net_spread_pct']:.2f}% < min {user['min_spread']}"

        # 🚀 3. БОЙОВА СТІЙКА НОРМАЛІЗАЦІЯ БАНКІВ (Рятує від каші в SQLite)
            # 🚀 СУПЕР-ФІКС: Повне вирівнювання будь-яких вкладених структур та бруду з SQLite
        def _clean_and_normalize_banks(banks_input) -> set[str]:
            if not banks_input:
                return set()
            import re

            # Перетворюємо будь-яку структуру (списки, сети, JSON рядки) у єдиний сирий рядок
            input_str = str(banks_input)
            # Миттєво витягуємо суто чисті слова та цифри
            raw_strings = re.findall(r'[a-zA-Z0-9а-яА-ЯіІёЁєЄїЇґҐ]+', input_str)

            normalized = set()
            name_map = {
                    "43": "monobank", "mono": "monobank", "monobank": "monobank", "моно": "monobank",
                    "монобанк": "monobank",
                    "14": "privatbank", "privat": "privatbank", "privatbank": "privatbank", "приват": "privatbank",
                    "приватбанк": "privatbank",
                    "64": "pumb", "pumb": "pumb", "пумб": "pumb",
                    "48": "a-bank", "abank": "a-bank", "a-bank": "a-bank", "абанк": "a-bank", "а-банк": "a-bank",
                    "553": "izibank", "izi": "izibank", "izibank": "izibank", "ізі": "izibank", "ізібанк": "izibank",
                    "328": "sense", "sense": "sense", "sensebank": "sense", "сенс": "sense", "сенсбанк": "sense"
            }
            for s in raw_strings:
                s_low = s.lower()
                if s_low in name_map:
                    normalized.add(name_map[s_low])
                else:
                    normalized.add(s_low)
            return normalized

        # Очищуємо та приводимо до єдиного виду налаштування користувача й дані спреду
        user_buy_normalized = _clean_and_normalize_banks(user.get("buy_bank_codes") or user.get("bank_codes"))
        user_sell_normalized = _clean_and_normalize_banks(user.get("sell_bank_codes") or user.get("bank_codes"))
        opp_buy_normalized = _clean_and_normalize_banks(opp.get("buy_banks_fit"))
        opp_sell_normalized = _clean_and_normalize_banks(opp.get("sell_banks_fit"))

        if not (opp_buy_normalized & user_buy_normalized):
            return False, f"buy banks no match: user={user_buy_normalized} opp={opp_buy_normalized}"
        if not (opp_sell_normalized & user_sell_normalized):
            return False, f"sell banks no match: user={user_sell_normalized} opp={opp_sell_normalized}"

        # 4. Персональні фільтри мерчанта (Твоя існуюча логіка)
        from config.defaults import MIN_ORDERS as _DEF_ORDERS, MIN_COMPLETION as _DEF_RATE
        mf = user.get("merchant_filters") or {}
        emf = user.get("exchange_merchant_filters") or {}
        buy_o = opp["buy_order"]
        sell_o = opp["sell_order"]

        for order_obj in (buy_o, sell_o):
            ex_name = getattr(order_obj, "exchange", "")
            side_label = "buy" if order_obj is buy_o else "sell"
            ex_filters = emf.get(ex_name, {})
            min_orders = float(
                ex_filters.get("min_orders", 0) or mf.get("min_orders", 0) or _DEF_ORDERS.get(ex_name, 0))
            min_rate = float(ex_filters.get("min_rate", 0.0) or mf.get("min_rate", 0.0) or _DEF_RATE.get(ex_name, 0.0))

            if min_orders > 0 and order_obj.month_order_count < min_orders:
                return False, f"{side_label} merchant orders {order_obj.month_order_count} < {min_orders}"
            if min_rate > 0 and order_obj.finish_rate_pct < min_rate:
                return False, f"{side_label} merchant rate {order_obj.finish_rate_pct:.1f}% < {min_rate}"

        return True, ""
    async def dispatch(self, alert: SpreadAlert, opp: dict) -> None:
        """Відправляє алерт всім підходящим юзерам з динамічною підміною банку під картки."""
        users = await self._get_users()
        if not users:
            logger.info("📬 Dispatch fallback → queue (немає зареєстрованих юзерів)")
            await self._notifier.push(alert)
            return

        logger.info("🔍 Аналіз розсилки для %d юзерів (Спред: %.2f%%)...", len(users), opp["net_spread_pct"])
        matched_count = 0

        import copy

        for user in users:
            uid = user.get("user_id")
            chat_id = user.get("chat_id")

            if opp.get("is_asymmetric"):
                is_asym_active = await self._db.get_feature_status(uid, "asymmetric_spread")
                if not is_asym_active:
                    logger.debug("  └─ ⏭ ПРОПУЩЕНО → Юзер: %s | Фіча asymmetric_spread вимкнена", uid)
                    continue

            is_sniper = False
            for r in user.get("sniper_rules", []):
                req_ex = r.get("exchange", "")
                req_dir = r.get("direction", "")
                min_spd = float(r.get("min_spread", 0))
                min_vol = float(r.get("min_volume", 0))

                if float(opp["net_spread_pct"]) >= min_spd and float(opp["actual_entry_uah"]) >= min_vol:
                    if req_dir == "BUY" and opp["sell_order"].exchange.upper() == req_ex.upper():
                        is_sniper = True
                        break
                    elif req_dir == "SELL" and opp["buy_order"].exchange.upper() == req_ex.upper():
                        is_sniper = True
                        break

            wants, skip_reason = self._user_wants(user, opp)

            if wants or is_sniper:
                matched_count += 1
                match_type = "🎯 SNIPER" if is_sniper else "✅ SPREAD"

                # 🧠 АВТОМАТИЧНА АДАПТАЦІЯ БАНКУ ПІД НАЯВНИЙ ПЛАСТИК
                try:
                    user_cards = await self._db.get_cards(owner_id=uid, status="active")
                    # Отримуємо назви банків твоїх живих карт: {"monobank"}
                    user_card_names = {str(c["bank_name"]).lower() for c in user_cards}
                except Exception as e:
                    logger.warning("Помилка отримання карток користувача %s: %s", uid, e)
                    user_card_names = set()

                user_buy_list = user.get("buy_bank_codes") or user.get("bank_codes") or []
                user_sell_list = user.get("sell_bank_codes") or user.get("bank_codes") or []

                # 🚀 Переводимо всі коди налаштувань та ордера в текстові назви ("43" -> "monobank")
                user_buy_names = {str(BankRegistry.get_name(b)).lower() for b in user_buy_list}
                user_sell_names = {str(BankRegistry.get_name(b)).lower() for b in user_sell_list}
                opp_buy_names = {str(BankRegistry.get_name(b)).lower() for b in (opp.get("buy_banks_fit") or [])}
                opp_sell_names = {str(BankRegistry.get_name(b)).lower() for b in (opp.get("sell_banks_fit") or [])}

                allowed_buy_names = opp_buy_names & user_buy_names
                allowed_sell_names = opp_sell_names & user_sell_names

                # Тепер перетин текстових назв працює бездоганно!
                cards_buy_match = allowed_buy_names & user_card_names
                cards_sell_match = allowed_sell_names & user_card_names

                # Зворотний мапінг назв у коди для збереження сумісності з калькулятором лімітів
                NAME_TO_CODE = {"monobank": "43", "privatbank": "14", "pumb": "64", "a-bank": "48", "izibank": "553", "sense": "328"}

                final_buy_name = list(cards_buy_match)[0] if cards_buy_match else (
                    list(allowed_buy_names)[0] if allowed_buy_names else "monobank")
                final_sell_name = list(cards_sell_match)[0] if cards_sell_match else (
                    list(allowed_sell_names)[0] if allowed_sell_names else "monobank")

                chosen_buy = NAME_TO_CODE.get(final_buy_name, "43")
                chosen_sell = NAME_TO_CODE.get(final_sell_name, "43")

                # Безпечна копія інстансу алерта для юзера
                local_alert = copy.copy(alert)
                local_alert.buy_bank = chosen_buy
                local_alert.sell_bank = chosen_sell

                # Прокидаємо експериментальні деталі в об'єкт алерта
                local_alert.is_asymmetric = opp.get("is_asymmetric", False)
                local_alert.asymmetric_details = opp.get("asymmetric_details")

                try:
                    await self._notifier.send_to_user(chat_id, local_alert, is_sniper_match=is_sniper)
                    logger.info(
                        "  └─ %s ВІДПРАВЛЕНО → Юзер: %s | Картки скориговано: %s ➔ %s",
                        match_type, uid, chosen_buy.upper(), chosen_sell.upper()
                    )
                except Exception as e:
                    logger.warning("  └─ ❌ Помилка відправки юзеру %s: %s", uid, e)
            else:
                logger.debug(
                    "  └─ ⏭ ПРОПУЩЕНО → Юзер: %s | Причина: %s",
                    uid, skip_reason
                )

        logger.info(
            "📬 Підсумок розсилки: %d/%d юзерів отримали зв'язку.",
            matched_count, len(users)
        )


# ═══════════════════════════════════════════════════════════════════════════════
# ProviderFactory — ініціалізація клієнтів і credentials
# ═══════════════════════════════════════════════════════════════════════════════

@dataclass
class AccountClients:
    bybit: BybitAccountClient
    binance: BinanceAccountClient
    okx: OKXAccountClient
    mexc: MEXCAccountClient

    def as_dict(self) -> dict:
        return {
            "Bybit": self.bybit, "Binance": self.binance,
            "OKX": self.okx, "MEXC": self.mexc,
        }


async def _load_credentials(db: MerchantDB) -> AccountClients:
    """
    Завантажує зашифровані credentials з БД і ініціалізує account клієнтів.
    Повертає AccountClients — навіть якщо credentials немає (порожні клієнти).
    """
    creds = await db.get_all_credentials()

    bybit_acc = BybitAccountClient()
    binance_acc = BinanceAccountClient()
    okx_acc = OKXAccountClient()
    mexc_acc = MEXCAccountClient()

    if "Bybit" in creds:
        bybit_acc.set_credentials(creds["Bybit"]["api_key"], creds["Bybit"]["api_secret"])
        logger.info("✅ Bybit API credentials завантажено")
    if "Binance" in creds:
        binance_acc.set_credentials(creds["Binance"]["api_key"], creds["Binance"]["api_secret"])
        logger.info("✅ Binance API credentials завантажено")
    if "OKX" in creds:
        okx_acc.set_credentials(
            creds["OKX"]["api_key"], creds["OKX"]["api_secret"],
            creds["OKX"].get("passphrase", ""),
        )
        logger.info("✅ OKX API credentials завантажено")
    if "MEXC" in creds:
        mexc_acc.set_credentials(creds["MEXC"]["api_key"], creds["MEXC"]["api_secret"])
        logger.info("✅ MEXC API credentials завантажено")

    return AccountClients(bybit_acc, binance_acc, okx_acc, mexc_acc)


def _bind_http_credentials(
        creds: dict,
        b_client: BybitP2PClient,
        bn_client: BinanceClient,
        o_client: OkxClient,
        w_client: WalletClient = None,
) -> None:
    """Прив'язує ті самі credentials до HTTP клієнтів (для ReviewFetcher)."""
    if "Bybit" in creds:
        b_client.set_credentials(creds["Bybit"]["api_key"], creds["Bybit"]["api_secret"])
    if "Binance" in creds:
        bn_client.set_credentials(creds["Binance"]["api_key"], creds["Binance"]["api_secret"])
    if "OKX" in creds:
        o_client.set_credentials(
            creds["OKX"]["api_key"], creds["OKX"]["api_secret"],
            creds["OKX"].get("passphrase", ""),
        )
    if "Wallet" in creds and w_client:
        w_client.set_credentials(creds["Wallet"]["api_key"])


# ═══════════════════════════════════════════════════════════════════════════════
# Watchdog і DB Maintenance (без змін — вже чисто)
# ═══════════════════════════════════════════════════════════════════════════════

async def _watchdog(
        last_cycle_time: list[float],
        interval: float = getattr(settings, "watchdog_interval", 30.0),
) -> None:
    while True:
        await asyncio.sleep(interval)
        elapsed = time.monotonic() - last_cycle_time[0]
        if elapsed > interval * 2:
            logger.error("🚨 [WATCHDOG] Головний цикл не відповідає %.0fs!", elapsed)


async def _db_maintenance_loop(
        db: MerchantDB,
        interval_hours: float = getattr(settings, "db_maint_interval_h", 1.0),
) -> None:
    # 24 години історії повністю достатньо для детекції ботів
    max_age = getattr(settings, "db_snapshot_max_age_h", 24)
    while True:
        await asyncio.sleep(interval_hours * 3600)
        try:
            deleted = await db.prune_snapshots(max_age_hours=max_age)
            if deleted > 0:
                logger.info("🧹 DB Maintenance: видалено %d старих снапшотів", deleted)
            # Чистка старих пропозицій сканера (7 днів)
            prop_deleted = await db.cleanup_old_proposals(retention_days=7)
            if prop_deleted > 0:
                logger.info("🧹 DB Maintenance: видалено %d старих пропозицій", prop_deleted)
        except asyncio.CancelledError:
            break
        except Exception as e:
            logger.error("Помилка під час DB Maintenance: %s", e)


# ═══════════════════════════════════════════════════════════════════════════════
# run_scanner — тільки оркестрація
# ═══════════════════════════════════════════════════════════════════════════════

async def run_scanner(notifier: TelegramNotifier, stop_event: asyncio.Event, shared_db=None) -> None:
    # ── Ініціалізація ──────────────────────────────────────────────────────
    # Використовуємо shared_db з main.py якщо є — уникаємо дублювання екземплярів
    _owns_db = shared_db is None
    merchant_db = shared_db if shared_db is not None else MerchantDB()
    if _owns_db:
        await merchant_db.start()
        await merchant_db.load_blacklist_from_file()

    runtime_config._db = merchant_db
    await runtime_config.load()

    # Завантажуємо credentials один раз через ProviderFactory
    all_creds = await merchant_db.get_all_credentials()
    account_clients = await _load_credentials(merchant_db)

    from core.engine.trade_worker import TradeWorker
    from core.engine.strategy_manager import StrategyManager

    # 🚀 Блок A: Single-Leg Executor
    from core.engine.single_leg_executor import SingleLegExecutor
    single_leg_executor = SingleLegExecutor(merchant_db)

    notifier.bind_db(merchant_db)
    # bind_commands відкладено до ініціалізації MakerAdMonitor (після risk_engine)

    llm_pool = LLMWorkerPool(merchant_db)
    await llm_pool.start()

    review_fetcher = ReviewFetcher(
        merchant_db,
        review_ttl_hours=getattr(settings, "review_ttl_hours", 24.0),
    )
    await review_fetcher.start()
    # 🚀 ДОДАНО: Запуск фонового менеджера сесій
    session_manager = SessionManager(merchant_db)

    # 🚀 Блок C: Підключаємо TG-сповіщення для session health
    async def _session_notify(msg: str, user_id: int = 0, exchange: str = "") -> None:
        try:
            from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton
            kb = None
            if exchange:
                kb = InlineKeyboardMarkup(inline_keyboard=[
                    [InlineKeyboardButton(text="📲 Оновити через скрипт-закладку", callback_data=f"intercept:{exchange}")]
                ])

            # Відправляємо конкретному юзеру або всім адмінам
            target_chat = user_id if user_id else None
            await notifier._send_with_retry(msg, keyboard=kb, chat_id=target_chat)
        except Exception as e:
            logger.error("Session notify error: %s", e)

    session_manager.set_notify_callback(_session_notify)
    await session_manager.start()

    # 🚀 ExchangeManager — управління доступністю бірж
    await exchange_manager.load_from_config(runtime_config)

    async def _exchange_down_notify(exchange_name: str) -> None:
        """Сповіщення юзеру коли біржа впала (CircuitBreaker → OPEN)."""
        try:
            from bot.keyboards import exchange_down_kb
            text = (
                f"⚠️ <b>{exchange_name} API не відповідає!</b>\n\n"
                f"CircuitBreaker спрацював — біржа тимчасово недоступна.\n"
                f"Поки вона в пошуку — сканер гальмує на таймаутах.\n\n"
                f"💡 <b>Рекомендація:</b> вимкнути біржу з пошуку"
            )
            kb = exchange_down_kb(exchange_name)
            await notifier._send_with_retry(text, keyboard=kb)
        except Exception as e:
            logger.error("Exchange notify error: %s", e)

    exchange_manager.set_notify_callback(_exchange_down_notify)

    risk_engine = RiskEngine(db=merchant_db, llm_pool=llm_pool, review_fetcher=review_fetcher)
    merchant_filter = MerchantFilter(risk_mode=getattr(settings, "risk_mode", "WARNING"))

    # 🚀 MakerAdMonitor — моніторинг вхідних ордерів на мейкер-оголошення
    maker_monitor = MakerAdMonitor(db=merchant_db, risk_engine=risk_engine)

    async def _maker_notify_cb(chat_id: int, order_info: dict, counterparty_order, exchange: str):
        """Callback для TG-нотифікації про вхідний maker-ордер."""
        try:
            await notifier.send_maker_order_alert(chat_id, order_info, counterparty_order, exchange)
        except Exception as e:
            logger.error("Maker notify callback error: %s", e)

    maker_monitor.set_notify_callback(_maker_notify_cb)
    await maker_monitor.seed_seen_orders()

    # Створюємо TradeWorker першим (бо в нього всередині створюється RouteExecutor)
    trade_worker = TradeWorker(merchant_db)

    # Створюємо StrategyManager і передаємо йому RouteExecutor
    strategy_manager = StrategyManager(merchant_db, maker_monitor, trade_worker._executor)

    # Інжектимо зв'язок назад у TradeWorker
    trade_worker._strategy_manager = strategy_manager

    # Передаємо maker_monitor в bot_commands через notifier.bind_commands
    notifier.bind_commands(
        merchant_db, account_clients.as_dict(), trade_worker,
        single_leg_executor=single_leg_executor,
        maker_monitor=maker_monitor,
    )
    stability_filter = SpreadStabilityFilter(
        required_hits=getattr(settings, "stability_hits", 2),
        ttl_seconds=getattr(settings, "stability_ttl", 15.0),
    )
    dedup_cache = TTLCache(
        ttl_seconds=getattr(settings, "dedup_ttl_seconds", 60.0),
        max_size=getattr(settings, "dedup_max_size", 1000),
    )
    matcher = CrossMatchingEngine(
        max_capital_uah=settings.working_capital_uah,
        min_trade_uah=getattr(settings, "search_amount_uah", 1000.0),
        min_spread_pct=settings.min_spread_pct,
        safety_buffer_pct=getattr(settings, "safety_buffer_pct", 0.3),
    )
    target_banks = {code: BANK_NAMES[code] for code in DEFAULT_BANK_CODES if code in BANK_NAMES}
    dispatcher = AlertDispatcher(merchant_db, notifier)
    taker_scanner = TakerScanner()
    taker_dedup = TTLCache(
        ttl_seconds=getattr(settings, "taker_dedup_ttl", 90.0),
        max_size=500,
    )
    maker_dedup = TTLCache(
        ttl_seconds=getattr(settings, "maker_dedup_ttl", 300.0),  # 5 хвилин
        max_size=100,
    )

    # Таймінги
    cb_fails = getattr(settings, "cb_failure_threshold", 3)
    cb_timeout = getattr(settings, "cb_recovery_timeout", 60.0)
    cycle_min_sleep = getattr(settings, "cycle_min_sleep", 0.5)
    cycle_max_sleep = getattr(settings, "cycle_max_sleep", 3.0)
    cycle_error_sleep = getattr(settings, "cycle_error_sleep", 10.0)

    last_cycle_time = [time.monotonic()]
    watchdog_task = asyncio.create_task(_watchdog(last_cycle_time))
    maintenance_task = asyncio.create_task(_db_maintenance_loop(merchant_db))

    logger.info("🚀 Запуск Cross-Exchange Сканера (Bybit + OKX + Wallet + Binance + MEXC)...")
    logger.info(
        "💼 Дефолт капітал: %s ₴ | Поріг: %s%% | (Персональні налаштування завантажуються з БД)",
        settings.working_capital_uah,
        settings.min_spread_pct,
    )

    try:
        async with (
            BybitP2PClient() as b_client,
            OkxClient() as o_client,
            WalletClient() as w_client,
            BinanceClient() as bn_client,
            MexcClient() as m_client,
        ):
            # Прив'язуємо credentials до HTTP клієнтів
            _bind_http_credentials(all_creds, b_client, bn_client, o_client, w_client)
            review_fetcher.bind_clients(binance=bn_client, bybit=b_client, okx=o_client, mexc=m_client)

            cb_bybit = CircuitBreaker(failure_threshold=cb_fails, recovery_timeout=cb_timeout)
            cb_okx = CircuitBreaker(failure_threshold=cb_fails, recovery_timeout=cb_timeout)
            cb_wallet = CircuitBreaker(failure_threshold=cb_fails, recovery_timeout=cb_timeout)
            cb_binance = CircuitBreaker(failure_threshold=cb_fails, recovery_timeout=cb_timeout)
            cb_mexc = CircuitBreaker(failure_threshold=cb_fails, recovery_timeout=cb_timeout)

            ex_configs = [
                {"name": "Bybit", "instance": BybitExchange(b_client), "cb": cb_bybit},
                {"name": "OKX", "instance": OkxExchange(o_client), "cb": cb_okx},
                {"name": "Wallet", "instance": WalletExchange(w_client), "cb": cb_wallet},
                {"name": "Binance", "instance": BinanceExchange(bn_client), "cb": cb_binance},
                {"name": "MEXC", "instance": MexcExchange(m_client), "cb": cb_mexc},
            ]

            cb_userbot = CryptoBotUserbot(
                api_id=settings.telegram_api_id,
                api_hash=settings.telegram_api_hash,
                session_name="cryptobot_session",
                update_interval=getattr(settings, "cb_userbot_interval", 45.0),
                banks=list(target_banks.keys()),
            )
            await cb_userbot.start()

            async def safe_fetch(cfg: dict, amounts: list, banks: list):
                name = cfg["name"]

                # 🚀 Пропускаємо вимкнені біржі — нульовий overhead
                if not exchange_manager.is_enabled(name):
                    return ([], [])

                timeout = 3.0 if name == "Wallet" else 7.0
                try:
                    result = await asyncio.wait_for(
                        cfg["cb"].call(cfg["instance"].fetch_both_multi(amounts=amounts, banks=banks)),
                        timeout=timeout,
                    )
                    exchange_manager.reset_failures(name)
                    return result
                except asyncio.TimeoutError:
                    logger.warning("🐌 %s занадто довго відповідає!", name)
                    cfg["cb"].record_failure()
                    raise
                except Exception as e:
                    if "Circuit is OPEN" in str(e):
                        logger.warning("📉 Degraded Mode: %s ВІДКЛЮЧЕНА", name)
                        # Сповіщуємо юзера з пропозицією вимкнути
                        asyncio.create_task(
                            exchange_manager.on_circuit_open(name, runtime_config)
                        )
                    raise

            # Health-check функція для ExchangeManager
            async def _health_check(exchange_name: str) -> bool:
                """Пробний запит до біржі — повертає True якщо відповідає."""
                cfg_map = {c["name"]: c for c in ex_configs}
                cfg = cfg_map.get(exchange_name)
                if not cfg:
                    return False
                try:
                    result = await asyncio.wait_for(
                        cfg["instance"].fetch_both_multi(
                            amounts=[1000.0], banks=list(target_banks.keys())
                        ),
                        timeout=5.0,
                    )
                    return bool(result and (result[0] or result[1]))
                except Exception:
                    return False

            exchange_manager.set_health_check(_health_check)

            # ── Safe Boot Flow ──────────────────────────────────────────────
            await runtime_config.load()

            # Перевірялка "здоров'я" при старті
            require_sessions = runtime_config.get("require_sessions", "true") == "true"
            if require_sessions:
                invalid_exchanges = await session_manager.get_invalid_sessions(["Binance", "Bybit", "OKX"])
                if invalid_exchanges:
                    # Ставимо сканер на паузу
                    await runtime_config.set("is_scanner_active", "false")

                    # Сповіщаємо адмінів
                    ex_list = ", ".join(invalid_exchanges)
                    msg = (
                        f"🛑 <b>Safe Boot: Сканер на паузі</b>\n\n"
                        f"У тебе протухли сесії для: <b>{ex_list}</b>.\n"
                        f"Без них RiskEngine матиме менше даних для аналізу мерчантів.\n\n"
                        f"👉 Онови сесії, після чого запусти сканер знову."
                    )
                    from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton
                    kb = InlineKeyboardMarkup(inline_keyboard=[
                        [InlineKeyboardButton(text="🏃‍♂️ Поїхали без сесій (Ігнор)", callback_data="boot_ignore_sessions")],
                        [InlineKeyboardButton(text="📲 Оновити через скрипт-закладки", callback_data="menu:global_settings")]
                    ])
                    await notifier._send_with_retry(msg, keyboard=kb)
                    logger.warning(f"Safe Boot: відкладено старт через недійсні сесії ({ex_list}).")
                else:
                    # Сесії валідні або відключена перевірка — піднімаємо покинуті ордери (Регідратація)
                    await strategy_manager.hydrate_active_trades()
            else:
                await strategy_manager.hydrate_active_trades()

            # ── Головний цикл ──────────────────────────────────────────────
            _runtime_last_load: float = time.monotonic()
            _cycle_counter: int = 0

            while not stop_event.is_set():
                try:
                    start_time = time.monotonic()

                    # Runtime config — не частіше ніж раз на 10s
                    if time.monotonic() - _runtime_last_load >= 10.0:
                        await runtime_config.load()
                        _runtime_last_load = time.monotonic()

                    # 🚀 ФІКС: Перевірка чи сканер на паузі
                    is_active = runtime_config.get("is_scanner_active", "false") == "true"
                    if not is_active:
                        last_cycle_time[0] = time.monotonic()
                        await asyncio.sleep(3.0)
                        continue

                    current_max_alerts = int(
                        runtime_config.get("max_alerts_per_cycle", getattr(settings, "max_alerts_per_cycle", 4)))

                    active_users = await merchant_db.get_active_users()
                    if active_users:
                        current_capital = max(float(u["capital"]) for u in active_users)
                        current_spread  = min(float(u["min_spread"]) for u in active_users)
                        # Динамічні банки — union всіх активних юзерів (загальні + buy + sell)
                        _all_banks: set[str] = set()
                        for _u in active_users:
                            _all_banks.update(_u["bank_codes"])
                            _all_banks.update(_u.get("buy_bank_codes") or [])
                            _all_banks.update(_u.get("sell_bank_codes") or [])
                        target_banks = {c: BANK_NAMES[c] for c in _all_banks if c in BANK_NAMES}
                        if not target_banks:  # fallback якщо банки порожні
                            target_banks = {c: BANK_NAMES[c] for c in DEFAULT_BANK_CODES if c in BANK_NAMES}
                    else:
                        current_capital = settings.working_capital_uah
                        current_spread = settings.min_spread_pct

                    matcher.max_capital_uah = current_capital
                    matcher.min_spread_pct = current_spread
                    set_max_capital(current_capital)

                    _grid: set[float] = {1000.0, 2500.0}
                    for u in active_users:
                        cap = float(u["capital"])
                        mn = float(u.get("min_amount", 0.0)) or 1000.0
                        _grid.add(mn)
                        _grid.add(cap)
                        _grid.add(round((mn + cap) / 2, -2))
                    if not active_users:
                        _grid.update(getattr(settings, "search_amounts_uah", [1000.0, 2500.0, 5100.0]))
                    search_amounts = sorted(_grid)

                    results = await asyncio.gather(
                        *(safe_fetch(cfg, search_amounts, list(target_banks.keys())) for cfg in ex_configs),
                        return_exceptions=True,
                    )

                    cb_buy, cb_sell = cb_userbot.get_orders()
                    if cb_buy or cb_sell:
                        results.append((cb_buy, cb_sell))

                    all_cycle_orders = []
                    buy_grouped = {b: [] for b in target_banks}
                    sell_grouped = {b: [] for b in target_banks}

                    for res in results:
                        if isinstance(res, Exception):
                            continue
                        b_orders, s_orders = res

                        # Тегуємо side для снапшотів і PriceAdvisor
                        for o in b_orders:
                            o.side = "buy"
                        for o in s_orders:
                            o.side = "sell"

                        all_cycle_orders.extend(b_orders)
                        all_cycle_orders.extend(s_orders)

                        for o in b_orders:
                            if merchant_filter.passed(o):
                                # schedule() прибрано — відгуки тягнуться ТІЛЬКИ
                                # для мерчантів у реальному спреді (lazy в risk_engine)
                                for bank_code in o.bank_codes:
                                    if bank_code in buy_grouped:
                                        buy_grouped[bank_code].append(o)

                        for o in s_orders:
                            if merchant_filter.passed(o):
                                for bank_code in o.bank_codes:
                                    if bank_code in sell_grouped:
                                        sell_grouped[bank_code].append(o)

                    if all_cycle_orders:
                        asyncio.ensure_future(merchant_db.add_snapshots_batch(all_cycle_orders))

                    last_cycle_time[0] = time.monotonic()

                    raw_opportunities = matcher.match(buy_grouped, sell_grouped, experimental_mode=True)
                    opportunities = matcher.group(raw_opportunities, BANK_NAMES)
                    latency = time.monotonic() - start_time

                    _cycle_counter += 1
                    update_stats(
                        cycles=_cycle_counter,
                        last_cycle_ms=latency * 1000,
                        llm_queue=llm_pool._queue.qsize() if hasattr(llm_pool, "_queue") else 0,
                        review_queue=review_fetcher._queue.qsize() if hasattr(review_fetcher, "_queue") else 0,
                        cb_status={
                            cfg["name"]: "DISABLED" if not exchange_manager.is_enabled(cfg["name"])
                            else cfg["cb"].state.value
                            for cfg in ex_configs
                        },
                    )

                    # --- ДОДАНО ДЛЯ ФРОНТЕНДУ ---
                    state.stats["totalScanned"] += len(all_cycle_orders)
                    state.stats["opportunitiesFound"] += len(opportunities)
                    state.stats["avgSpread"] = round(
                        sum(float(o["net_spread_pct"]) for o in opportunities) / len(opportunities)
                        if opportunities else 0.0,
                        2
                    )

                    # 1. Створюємо новий пустий список для актуальних ордерів
                    current_frontend_opps = []
                    current_cycle_alerts = []
                    # ----------------------------

                    active_ex_count = sum(
                        1 for cfg in ex_configs if exchange_manager.is_enabled(cfg["name"])
                    )
                    logger.info(
                        "🔄 Цикл: %.2fs | Бірж: %d | Маршрутів: %d | Сирих: %d | Згруповано: %d",
                        latency, active_ex_count,
                        active_ex_count * len(target_banks),
                        len(raw_opportunities), len(opportunities),
                    )

                    sent_count = 0
                    # ── Аналіз ризику для всіх ордерів у спреді ─────────────
                    # analyze_for_spread() ЧЕКАЄ завершення аналізу (await) —
                    # кешований LLM вердикт буде в алерті, а не тільки в наступному циклі.
                    _analyzed_ids: set[tuple[str, str]] = set()
                    _to_analyze: list = []
                    for opp in opportunities:
                        for order in [opp["buy_order"], opp["sell_order"]]:
                            key = (order.exchange, order.merchant_id)
                            if key not in _analyzed_ids:
                                _analyzed_ids.add(key)
                                _to_analyze.append(order)
                    if _to_analyze:
                        await risk_engine.analyze_for_spread(_to_analyze)

                    for opp in opportunities:
                        buy_o = opp["buy_order"]
                        sell_o = opp["sell_order"]

                        b_rec, _, b_reason, _, _ = await merchant_db.get_trade_recommendation_full(buy_o.exchange, buy_o.merchant_id)
                        s_rec, _, s_reason, _, _ = await merchant_db.get_trade_recommendation_full(sell_o.exchange, sell_o.merchant_id)
                        # 2. Формуємо об'єкт для React ДО фільтрів дедуплікації і лімітів алертів.
                        # Це гарантує, що ордер буде на сайті рівно стільки, скільки він реально висить в стакані.
                        logger.warning(
                            "🚨 СПРЕД! %s | %s ➔ %s | %s | Net: %.2f%% | Профіт: %.2f ₴",
                            opp.get("route_type", "?"),
                            buy_o.exchange, sell_o.exchange,
                            ", ".join(opp.get("route_variants", [])[:4]),
                            opp["net_spread_pct"], opp["net_profit"],
                        )

                        alert = SpreadAlert(
                            buy_order=buy_o,
                            sell_order=sell_o,
                            spread_pct=opp["net_spread_pct"],
                            profit_uah=opp["net_profit"],
                            deal_amount_uah=opp["actual_entry_uah"],
                            buy_bank=opp.get("buy_bank", ""),
                            sell_bank=opp.get("sell_bank", ""),
                            buy_banks_all=opp.get("buy_banks_all"),
                            sell_banks_all=opp.get("sell_banks_all"),
                            buy_banks_fit=opp.get("buy_banks_fit"),
                            sell_banks_fit=opp.get("sell_banks_fit"),
                            route_variants=opp.get("route_variants"),
                            route_type=opp.get("route_type", "UNKNOWN"),
                            buy_rec=b_rec,
                            sell_rec=s_rec,
                            buy_reason=b_reason,
                            sell_reason=s_reason,
                        )

                        # 🚀 ПРОКИДАЄМО ДАНІ ЕКСПЕРИМЕНТУ В КОРЕНЕВІ ПАРАМЕТРИ АЛЕРТА
                        alert.is_asymmetric = opp.get("is_asymmetric", False)
                        alert.asymmetric_details = opp.get("asymmetric_details")

                        # 🚀 Пропозиція зберігається ПІСЛЯ фільтрів (see below)

                        # --- ДОДАНО ДЛЯ ФРОНТЕНДУ ---
                        frontend_opp = {
                            "id": f"{getattr(buy_o, 'id', 'b')}-{getattr(sell_o, 'id', 's')}",  # ← ФІКС flickering
                            "timestamp": int(time.time() * 1000),
                            "buyOrder": {
                                "id": getattr(buy_o, "id", "b1"),
                                "price": safe_float(getattr(buy_o, "price", 0)),
                                "availableAmount": safe_float(getattr(buy_o, "available_amount", getattr(buy_o, "qty",
                                                                                                         getattr(buy_o,
                                                                                                                 "amount",
                                                                                                                 0)))),
                                "minLimit": safe_float(getattr(buy_o, "min_limit", getattr(buy_o, "min_amount",
                                                                                           getattr(buy_o,
                                                                                                   "min_order_amount",
                                                                                                   0)))),
                                "maxLimit": safe_float(getattr(buy_o, "max_limit", getattr(buy_o, "max_amount",
                                                                                           getattr(buy_o,
                                                                                                   "max_order_amount",
                                                                                                   0)))),
                                "merchantId": getattr(buy_o, "merchant_id", ""),
                                "merchantName": getattr(buy_o, "merchant_name", "Unknown"),
                                "orderCount": int(
                                    safe_float(getattr(buy_o, "order_count", getattr(buy_o, "month_order_count", 0)))),
                                "finishRate": safe_float(getattr(buy_o, "finish_rate", getattr(buy_o, "finish_rate_pct",
                                                                                               getattr(buy_o,
                                                                                                       "month_finish_rate",
                                                                                                       0)))),
                                "exchange": getattr(buy_o, "exchange", "Unknown"),
                                "link": getattr(buy_o, "link", "#"),
                                "bankCodes": getattr(buy_o, "bank_codes", []),
                                "riskScore": getattr(buy_o, "risk_score", 0),
                                "riskFlag": getattr(buy_o, "risk_flag", "OK"),
                                "isVerified": getattr(buy_o, "is_verified", False)
                            },
                            "sellOrder": {
                                "id": getattr(sell_o, "id", "s1"),
                                "price": safe_float(getattr(sell_o, "price", 0)),
                                "availableAmount": safe_float(getattr(sell_o, "available_amount", getattr(sell_o, "qty",
                                                                                                          getattr(
                                                                                                              sell_o,
                                                                                                              "amount",
                                                                                                              0)))),
                                "minLimit": safe_float(getattr(sell_o, "min_limit", getattr(sell_o, "min_amount",
                                                                                            getattr(sell_o,
                                                                                                    "min_order_amount",
                                                                                                    0)))),
                                "maxLimit": safe_float(getattr(sell_o, "max_limit", getattr(sell_o, "max_amount",
                                                                                            getattr(sell_o,
                                                                                                    "max_order_amount",
                                                                                                    0)))),
                                "merchantId": getattr(sell_o, "merchant_id", ""),
                                "merchantName": getattr(sell_o, "merchant_name", "Unknown"),
                                "orderCount": int(safe_float(
                                    getattr(sell_o, "order_count", getattr(sell_o, "month_order_count", 0)))),
                                "finishRate": safe_float(getattr(sell_o, "finish_rate",
                                                                 getattr(sell_o, "finish_rate_pct",
                                                                         getattr(sell_o, "month_finish_rate", 0)))),
                                "exchange": getattr(sell_o, "exchange", "Unknown"),
                                "link": getattr(sell_o, "link", "#"),
                                "bankCodes": getattr(sell_o, "bank_codes", []),
                                "riskScore": getattr(sell_o, "risk_score", 0),
                                "riskFlag": getattr(sell_o, "risk_flag", "OK"),
                                "isVerified": getattr(sell_o, "is_verified", False)
                            },
                            "netSpread": safe_float(opp.get("net_spread_pct", 0)),
                            "dealAmount": safe_float(
                                opp.get("actual_entry_uah", opp.get("deal_amount", opp.get("volume", 0)))),
                            "netProfit": safe_float(opp.get("net_profit", 0)),
                            "buyBank": opp.get("buy_bank", ""),
                            "sellBank": opp.get("sell_bank", ""),
                            "routeType": opp.get("route_type", "UNKNOWN")
                        }
                        current_frontend_opps.append(frontend_opp)
                        current_cycle_alerts.append(alert)
                        if sent_count >= current_max_alerts:
                            logger.debug("⏭ Скіп: max_alerts (%d)", current_max_alerts)
                            continue

                        if "BLOCK" in (getattr(buy_o, "risk_flag", "") or ""):
                            logger.debug("⏭ Скіп: buy BLOCK [%s]", buy_o.merchant_name)
                            continue
                        if "BLOCK" in (getattr(sell_o, "risk_flag", "") or ""):
                            logger.debug("⏭ Скіп: sell BLOCK [%s]", sell_o.merchant_name)
                            continue

                        dedup_key = f"spread:{matcher._merge_key(opp)}"
                        if dedup_cache.seen(dedup_key):
                            logger.debug("⏭ Скіп: dedup [%s→%s]", buy_o.merchant_name, sell_o.merchant_name)
                            continue

                        if not stability_filter.check(
                                buy_o.exchange, sell_o.exchange,
                                str(buy_o.price), str(sell_o.price),
                                buy_o.merchant_name, sell_o.merchant_name,
                        ):
                            logger.debug("⏭ Скіп: stability [%s→%s]", buy_o.merchant_name, sell_o.merchant_name)
                            continue

                        dedup_cache.mark(dedup_key)
                        sent_count += 1

                        # 🚀 Зберігаємо пропозицію ПІСЛЯ всіх фільтрів (dedup, stability, BLOCK)
                        _was_sent = not is_muted()
                        asyncio.create_task(merchant_db.save_proposal(
                            buy_exchange=buy_o.exchange,
                            sell_exchange=sell_o.exchange,
                            buy_merchant=buy_o.merchant_name,
                            sell_merchant=sell_o.merchant_name,
                            spread_pct=opp["net_spread_pct"],
                            profit_uah=opp["net_profit"],
                            deal_amount=opp["actual_entry_uah"],
                            route_type=opp.get("route_type", "UNKNOWN"),
                            buy_bank=opp.get("buy_bank", ""),
                            sell_bank=opp.get("sell_bank", ""),
                            was_sent=_was_sent,
                        ))

                        if not is_muted():
                            logger.info("📤 Dispatch алерт: %s→%s %.2f%%", buy_o.merchant_name, sell_o.merchant_name, opp["net_spread_pct"])
                            # Fire-and-forget з логуванням помилок
                            task = asyncio.create_task(dispatcher.dispatch(alert, opp))
                            task.add_done_callback(
                                lambda t: logger.error("💥 dispatch task error: %s", t.exception()) if t.exception() else None
                            )
                        else:
                            logger.debug("⏭ Скіп: muted")
                    state.opportunities = current_frontend_opps[:50]
                    state.current_alerts = current_cycle_alerts[:50]

                    # ── Тейкер-шлях: алерти для TAKER_BUY / TAKER_SELL юзерів ──
                    if active_users and not is_muted():
                        taker_users = [
                            u for u in active_users
                            if u.get("scanner_mode") in ("TAKER_BUY", "TAKER_SELL")
                        ]
                        for t_user in taker_users:
                            try:
                                t_orders = taker_scanner.find_orders_for_user(
                                    t_user, buy_grouped, sell_grouped,
                                )
                                if not t_orders:
                                    continue
                                # Dedup: не спамимо тим самим ордером щоцикл
                                t_mode = t_user["scanner_mode"]
                                fresh = []
                                for o in t_orders:
                                    dk = f"taker:{t_user['user_id']}:{o.id}"
                                    if not taker_dedup.seen(dk):
                                        taker_dedup.mark(dk)
                                        fresh.append(o)
                                if fresh:
                                    logger.info(
                                        "📤 Taker dispatch → user %s | %s | %d ордерів",
                                        t_user["user_id"], t_mode, len(fresh),
                                    )
                                    asyncio.create_task(
                                        notifier.send_taker_to_user(
                                            t_user["chat_id"], fresh, t_mode,
                                        )
                                    )
                            except Exception as e:
                                logger.warning(
                                    "Taker dispatch error user %s: %s",
                                    t_user.get("user_id"), e,
                                )

                    # ── Мейкер-шлях: підказки для MAKER_BUY / MAKER_SELL юзерів ──
                    if active_users and not is_muted():
                        # Збираємо sell_book_top — найкраща (найнижча) ціна продажу зі стакану
                        # Це потрібно для PriceAdvisor
                        _sell_prices: list[float] = []
                        for bank_code, orders in sell_grouped.items():
                            for o in orders:
                                try:
                                    _sell_prices.append(float(o.price))
                                except (TypeError, ValueError):
                                    pass
                        sell_book_top = min(_sell_prices) if _sell_prices else 0.0

                        # Збираємо buy_book_top — найкраща (найвища) ціна купівлі зі стакану
                        _buy_prices: list[float] = []
                        for bank_code, orders in buy_grouped.items():
                            for o in orders:
                                try:
                                    _buy_prices.append(float(o.price))
                                except (TypeError, ValueError):
                                    pass
                        buy_book_top = max(_buy_prices) if _buy_prices else 0.0

                        # ── MAKER_SELL: розрахунок рекомендованої ціни продажу ──
                        maker_sell_users = [
                            u for u in active_users
                            if u.get("scanner_mode") == "MAKER_SELL"
                               and float(u.get("maker_buy_price", 0)) > 0
                        ]
                        for ms_user in maker_sell_users:
                            try:
                                uid = ms_user["user_id"]
                                dk = f"maker_sell:{uid}"
                                if maker_dedup.seen(dk):
                                    continue

                                buy_price = float(ms_user["maker_buy_price"])
                                capital = float(ms_user["capital"])
                                amount_usdt = capital / buy_price if buy_price > 0 else 100.0

                                advice = PriceAdvisor.suggest_sell_price(
                                    buy_price=buy_price,
                                    amount_usdt=amount_usdt,
                                    min_margin=float(ms_user.get("target_margin", 0.003)),
                                )

                                # Перевіряємо: чи sell_book_top вигідний для мейкера
                                if sell_book_top > 0:
                                    advice["sell_book_top"] = sell_book_top
                                    advice["book_vs_min"] = round(sell_book_top - advice["min_sell_price"], 4)

                                maker_dedup.mark(dk)
                                logger.info(
                                    "📤 Maker SELL advice → user %s | buy=%.2f min_sell=%.4f book_top=%.2f",
                                    uid, buy_price, advice["min_sell_price"], sell_book_top,
                                )
                                asyncio.create_task(
                                    notifier.send_maker_sell_update(
                                        ms_user["chat_id"], advice,
                                    )
                                )
                            except Exception as e:
                                logger.warning("Maker SELL error user %s: %s", ms_user.get("user_id"), e)

                        # ── MAKER_BUY: аналіз ринку + рекомендація ціни купівлі ──
                        maker_buy_users = [
                            u for u in active_users
                            if u.get("scanner_mode") == "MAKER_BUY"
                        ]
                        for mb_user in maker_buy_users:
                            try:
                                uid = mb_user["user_id"]
                                dk = f"maker_buy:{uid}"
                                if maker_dedup.seen(dk):
                                    continue

                                if sell_book_top <= 0:
                                    continue  # Немає даних по стакану

                                target_margin = float(mb_user.get("target_margin", 0.005))
                                capital = float(mb_user["capital"])

                                advice = PriceAdvisor.suggest_buy_price(
                                    sell_book_top=sell_book_top,
                                    amount_usdt=capital / sell_book_top if sell_book_top > 0 else 100.0,
                                    target_margin=target_margin,
                                )

                                # Додаємо контекст стакану
                                if buy_book_top > 0:
                                    advice["buy_book_top"] = buy_book_top

                                maker_dedup.mark(dk)
                                logger.info(
                                    "📤 Maker BUY advice → user %s | sell_top=%.2f max_buy=%.4f margin=%.1f%%",
                                    uid, sell_book_top, advice["max_buy_price"], target_margin * 100,
                                )
                                asyncio.create_task(
                                    notifier.send_maker_buy_suggestion(
                                        mb_user["chat_id"], advice,
                                    )
                                )
                            except Exception as e:
                                logger.warning("Maker BUY error user %s: %s", mb_user.get("user_id"), e)

                    cycle_elapsed = time.monotonic() - start_time
                    adaptive_sleep = max(cycle_min_sleep, min(cycle_max_sleep, cycle_max_sleep - cycle_elapsed))
                    await asyncio.sleep(adaptive_sleep)

                except Exception as e:
                    logger.error("❌ Помилка в циклі сканування: %s", e, exc_info=True)
                    await asyncio.sleep(cycle_error_sleep)

    finally:
        watchdog_task.cancel()
        maintenance_task.cancel()
        if "cb_userbot" in locals():
            await cb_userbot.stop()
        maker_monitor.stop_all()
        await session_manager.stop()
        await review_fetcher.stop()
        await llm_pool.stop()
        if _owns_db:
            await merchant_db.stop()