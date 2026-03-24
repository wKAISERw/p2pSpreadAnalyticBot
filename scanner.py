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

logger = logging.getLogger("Scanner")


# ═══════════════════════════════════════════════════════════════════════════════
# AlertDispatcher — персоналізована розсилка алертів
# ═══════════════════════════════════════════════════════════════════════════════

class AlertDispatcher:
    """
    Відповідає ТІЛЬКИ за одне: взяти SpreadAlert і розіслати його
    правильним людям з правильними фільтрами.

    Кешує список активних юзерів з TTL щоб не смикати БД на кожному алерті.
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

    def _user_wants(self, user: dict, opp: dict) -> bool:
        """
        Персональний фільтр юзера:
          1. Коридор сум: min_amount ≤ entry ≤ capital
          2. Мінімальний спред
          3. Банки перетинаються
          4. Фільтри мерчанта (min_orders, min_rate з merchant_filters_json)
        """
        entry = float(opp["actual_entry_uah"])

        # 1. Капітальний коридор
        if entry > float(user["capital"]):
            return False
        min_amount = float(user.get("min_amount", 0.0))
        if min_amount > 0 and entry < min_amount:
            return False

        # 2. Мінімальний спред
        if float(opp["net_spread_pct"]) < float(user["min_spread"]):
            return False

        # 3. Банки
        user_banks = set(user["bank_codes"])
        opp_buy_banks = set(opp.get("buy_banks_fit") or [])
        opp_sell_banks = set(opp.get("sell_banks_fit") or [])
        if not (opp_buy_banks & user_banks) or not (opp_sell_banks & user_banks):
            return False

        # 4. Персональні фільтри мерчанта
        mf = user.get("merchant_filters") or {}
        if mf:
            min_orders = float(mf.get("min_orders", 0))
            min_rate = float(mf.get("min_rate", 0.0))
            buy_o = opp["buy_order"]
            sell_o = opp["sell_order"]
            if min_orders > 0:
                if buy_o.month_order_count < min_orders: return False
                if sell_o.month_order_count < min_orders: return False
            if min_rate > 0:
                if buy_o.finish_rate_pct < min_rate: return False
                if sell_o.finish_rate_pct < min_rate: return False

        return True

    async def dispatch(self, alert: SpreadAlert, opp: dict) -> None:
        """Відправляє алерт всім підходящим юзерам."""
        users = await self._get_users()
        if users:
            for user in users:
                if self._user_wants(user, opp):
                    try:
                        await self._notifier.send_to_user(user["chat_id"], alert)
                    except Exception as e:
                        # Юзер заблокував бота або інша помилка — не зупиняємо розсилку іншим
                        logger.warning("dispatch failed for user %s: %s", user.get("user_id"), e)
        else:
            # Fallback: single-user (ніхто не написав /start)
            await self._notifier.push(alert)


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
    max_age = getattr(settings, "db_snapshot_max_age_h", 168)
    while True:
        await asyncio.sleep(interval_hours * 3600)
        try:
            deleted = await db.prune_snapshots(max_age_hours=max_age)
            if deleted > 0:
                logger.info("🧹 DB Maintenance: видалено %d старих снапшотів", deleted)
        except asyncio.CancelledError:
            break
        except Exception as e:
            logger.error("Помилка під час DB Maintenance: %s", e)


# ═══════════════════════════════════════════════════════════════════════════════
# run_scanner — тільки оркестрація
# ═══════════════════════════════════════════════════════════════════════════════

async def run_scanner(notifier: TelegramNotifier, stop_event: asyncio.Event) -> None:
    # ── Ініціалізація ──────────────────────────────────────────────────────
    merchant_db = MerchantDB()
    await merchant_db.start()
    await merchant_db.load_blacklist_from_file()

    runtime_config._db = merchant_db
    await runtime_config.load()

    # Завантажуємо credentials один раз через ProviderFactory
    all_creds = await merchant_db.get_all_credentials()
    account_clients = await _load_credentials(merchant_db)

    notifier.bind_db(merchant_db)
    notifier.bind_commands(merchant_db, account_clients.as_dict())

    llm_pool = LLMWorkerPool(merchant_db)
    await llm_pool.start()

    review_fetcher = ReviewFetcher(
        merchant_db,
        review_ttl_hours=getattr(settings, "review_ttl_hours", 24.0),
    )
    await review_fetcher.start()

    risk_engine = RiskEngine(db=merchant_db, llm_pool=llm_pool)
    merchant_filter = MerchantFilter(risk_mode=getattr(settings, "risk_mode", "WARNING"))
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
            _bind_http_credentials(all_creds, b_client, bn_client, o_client)
            review_fetcher.bind_clients(binance=bn_client, bybit=b_client, okx=o_client)

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
                timeout = 3.0 if cfg["name"] == "Wallet" else 7.0
                try:
                    return await asyncio.wait_for(
                        cfg["cb"].call(cfg["instance"].fetch_both_multi(amounts=amounts, banks=banks)),
                        timeout=timeout,
                    )
                except asyncio.TimeoutError:
                    logger.warning("🐌 %s занадто довго відповідає!", cfg["name"])
                    cfg["cb"].record_failure()
                    raise
                except Exception as e:
                    if type(e).__name__ == "CircuitBreakerOpenException":
                        logger.warning("📉 Degraded Mode: %s ВІДКЛЮЧЕНА", cfg["name"])
                    raise

            # ── Головний цикл ──────────────────────────────────────────────
            _runtime_last_load: float = 0.0
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
                        current_spread = min(float(u["min_spread"]) for u in active_users)
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
                        all_cycle_orders.extend(b_orders)
                        all_cycle_orders.extend(s_orders)

                        for o in b_orders:
                            if merchant_filter.passed(o):
                                if o.merchant_id:
                                    review_fetcher.schedule(o.exchange, o.merchant_id)
                                for bank_code in o.bank_codes:
                                    if bank_code in buy_grouped:
                                        buy_grouped[bank_code].append(o)

                        for o in s_orders:
                            if merchant_filter.passed(o):
                                if o.merchant_id:
                                    review_fetcher.schedule(o.exchange, o.merchant_id)
                                for bank_code in o.bank_codes:
                                    if bank_code in sell_grouped:
                                        sell_grouped[bank_code].append(o)

                    if all_cycle_orders:
                        asyncio.ensure_future(merchant_db.add_snapshots_batch(all_cycle_orders))

                    last_cycle_time[0] = time.monotonic()

                    raw_opportunities = matcher.match(buy_grouped, sell_grouped)
                    opportunities = matcher.group(raw_opportunities, BANK_NAMES)
                    latency = time.monotonic() - start_time

                    _cycle_counter += 1
                    update_stats(
                        cycles=_cycle_counter,
                        last_cycle_ms=latency * 1000,
                        llm_queue=llm_pool._queue.qsize() if hasattr(llm_pool, "_queue") else 0,
                        review_queue=review_fetcher._queue.qsize() if hasattr(review_fetcher, "_queue") else 0,
                        cb_status={
                            cfg["name"]: cfg["cb"].state.value
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
                    # ----------------------------

                    logger.info(
                        "🔄 Цикл: %.2fs | Бірж: %d | Маршрутів: %d | Сирих: %d | Згруповано: %d",
                        latency, len(ex_configs),
                        len(ex_configs) * len(target_banks),
                        len(raw_opportunities), len(opportunities),
                    )

                    sent_count = 0
                    for opp in opportunities:
                        buy_o = opp["buy_order"]
                        sell_o = opp["sell_order"]

                        if not getattr(buy_o, "_risk_analyzed", False):
                            risk_engine.analyze(buy_o);
                            buy_o._risk_analyzed = True
                        if not getattr(sell_o, "_risk_analyzed", False):
                            risk_engine.analyze(sell_o);
                            sell_o._risk_analyzed = True

                        # 2. Формуємо об'єкт для React ДО фільтрів дедуплікації і лімітів алертів.
                        # Це гарантує, що ордер буде на сайті рівно стільки, скільки він реально висить в стакані.
                        def safe_float(val):
                            try:
                                return float(val)
                            except:
                                return 0.0



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
                        )

                        # --- ДОДАНО ДЛЯ ФРОНТЕНДУ ---
                        def safe_float(val):
                            try:
                                return float(val)
                            except:
                                return 0.0

                        frontend_opp = {
                            "id": f"{getattr(buy_o, 'id', 'b')}-{getattr(sell_o, 'id', 's')}-{int(time.time())}",
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
                        if sent_count >= current_max_alerts:
                            continue

                        if "BLOCK" in (getattr(buy_o, "risk_flag", "") or ""):
                            continue
                        if "BLOCK" in (getattr(sell_o, "risk_flag", "") or ""):
                            continue

                        dedup_key = f"spread:{matcher._merge_key(opp)}"
                        if dedup_cache.seen(dedup_key):
                            continue

                        if not stability_filter.check(
                                buy_o.exchange, sell_o.exchange,
                                str(buy_o.price), str(sell_o.price),
                                buy_o.merchant_name, sell_o.merchant_name,
                        ):
                            continue

                        dedup_cache.mark(dedup_key)
                        sent_count += 1

                        if not is_muted():
                            await dispatcher.dispatch(alert, opp)
                    state.opportunities = current_frontend_opps[:50]
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
        await review_fetcher.stop()
        await llm_pool.stop()
        await merchant_db.stop()