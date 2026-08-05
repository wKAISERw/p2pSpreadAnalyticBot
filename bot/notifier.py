# notifications/telegram_notifier.py
import asyncio
import logging
from datetime import datetime
from html import escape

from aiogram import Bot, Dispatcher, Router
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.exceptions import TelegramRetryAfter
from aiogram.types import InlineKeyboardMarkup, BotCommand

from config import settings
from bot.handlers import core as bot_commands
from bot.card_notifier import CardNotifier
from core.storage.merchant_db import MerchantDB
from exchanges.base import Order

# Ре-експортуємо SpreadAlert
from bot.alert_builder import SpreadAlert

logger = logging.getLogger(__name__)


class TelegramNotifier:
    def __init__(
            self,
            send_interval: float = 2.0,
            group_window: float = 1.5,
            batch_size: int = 5,
            max_queue_size: int = 100,
    ):
        self._bot = Bot(
            token=settings.telegram_bot_token,
            default=DefaultBotProperties(parse_mode=ParseMode.HTML),
        )

        self._dp = Dispatcher()
        self._router = Router()
        self._dp.include_router(self._router)
        self._dp.include_router(bot_commands.router)

        # Register global error handler to suppress rate limit tracebacks
        @self._dp.errors()
        async def global_error_handler(event):
            if isinstance(event.exception, TelegramRetryAfter):
                logger.warning("Telegram rate limit hit: %s. Suppressing traceback.", event.exception)
                return True
            return False

        self._db: MerchantDB | None = None

        # Single-user: глобальний chat_id з settings
        # Multi-user: _send_with_retry отримує chat_id явно
        self._chat_id = settings.telegram_chat_id
        self._send_interval = send_interval
        self._group_window = group_window
        self._batch_size = batch_size
        self._queue: asyncio.Queue[SpreadAlert] = asyncio.Queue(maxsize=max_queue_size)
        self._worker_task: asyncio.Task | None = None
        self._polling_task: asyncio.Task | None = None
        self._redraw_locks: dict[tuple[str, str], asyncio.Lock] = {}
        self._last_send_time: dict[int, float] = {}
        self._sent_timestamps: dict[int, list[float]] = {}
        self._last_flood_warning_time: dict[int, float] = {}
        self._display_settings_cache: dict[int, tuple[dict, float]] = {}

    def bind_db(self, db: MerchantDB):
        """Зв'язує нотифікатор з базою даних для обробки ручних скарг."""
        self._db = db
        self.card_notifier = CardNotifier(db, self._bot)

    def bind_commands(self, db: MerchantDB, account_clients: dict, trade_worker=None, single_leg_executor=None, maker_monitor=None, session_manager=None) -> None:
        """Оновлює змінні модуля. Router вже підключений в __init__."""
        bot_commands.setup(db, account_clients, trade_worker, notifier=self, single_leg_executor=single_leg_executor, maker_monitor=maker_monitor, session_manager=session_manager)
        self._taker_cache = bot_commands._taker_order_cache

    async def _get_display_settings(self, chat_id: int) -> dict:
        now = asyncio.get_event_loop().time()
        if chat_id in self._display_settings_cache:
            cached_val, cached_time = self._display_settings_cache[chat_id]
            if now - cached_time < 5.0:  # Кеш на 5 секунд
                return cached_val

        default_auto_cooldown = {
            "window_seconds": 5.0,
            "tiers": [
                {"threshold": 10, "delay": 0.0},
                {"threshold": 15, "delay": 0.3},
                {"threshold": 20, "delay": 0.8},
                {"threshold": 9999, "delay": 1.5}
            ]
        }
        defaults = {
            "show_ai_terms_summary": True, "show_full_terms": True,
            "show_ai_logic": True, "show_bank_details": True,
            "show_llm_summary": True, "show_card_recommendation": True,
            "alert_cooldown": -1.0, "group_active_alerts": True,
            "auto_cooldown_json": default_auto_cooldown
        }

        if not self._db:
            return defaults
        
        settings_dict = await self._db.get_user_display_settings(chat_id)
        if not settings_dict:
            settings_dict = defaults
        else:
            # Переконуємось, що всі нові ключі існують
            for k, v in defaults.items():
                if k not in settings_dict:
                    settings_dict[k] = v
        
        self._display_settings_cache[chat_id] = (settings_dict, now)
        return settings_dict

    @staticmethod
    def _serialize_order(order: Order) -> dict:
        """Серіалізує Order у JSON-сумісний dict, зберігаючи списки та tuple-поля коректно."""
        result = {}
        for k, v in order.__dict__.items():
            if k == "regex_warn_flags":
                # list[tuple[str, str]] → list[list[str, str]] (JSON-safe)
                result[k] = [list(item) if isinstance(item, (tuple, list)) else [str(item), ""] for item in (v or [])]
            elif k in ("bank_codes", "regex_score", "composite_score", "review_score",
                       "review_neg_pct", "review_fetched", "positive_rate",
                       "month_order_count", "finish_rate_pct", "is_verified",
                       "account_age_days"):
                result[k] = v  # зберігаємо оригінальний тип
            elif isinstance(v, (int, float)) and not isinstance(v, bool):
                result[k] = float(v)
            elif isinstance(v, (list, dict, bool)) or v is None:
                result[k] = v
            else:
                result[k] = str(v)
        return result

    def _serialize_alert(self, alert: SpreadAlert) -> dict:
        """Серіалізує SpreadAlert у JSON-сумісний dict."""
        return {
            "buy_order": self._serialize_order(alert.buy_order),
            "sell_order": self._serialize_order(alert.sell_order),
            "spread_pct": alert.spread_pct,
            "profit_uah": alert.profit_uah,
            "deal_amount_uah": alert.deal_amount_uah,
            "buy_bank": alert.buy_bank,
            "sell_bank": alert.sell_bank,
            "buy_banks_all": alert.buy_banks_all,
            "sell_banks_all": alert.sell_banks_all,
            "buy_banks_fit": alert.buy_banks_fit,
            "sell_banks_fit": alert.sell_banks_fit,
            "route_variants": alert.route_variants,
            "route_type": alert.route_type,
            "route_pairs": getattr(alert, "route_pairs", None),
            "is_asymmetric": getattr(alert, "is_asymmetric", False),
            "asymmetric_details": getattr(alert, "asymmetric_details", None),
        }

    async def send_to_user(self, chat_id: int, alert: "SpreadAlert", is_sniper_match: bool = False) -> None:
        """
        Multi-user: відправляє алерт в конкретний chat_id.
        chat_id передається явним параметром — без мутації self._chat_id.
        Повністю concurrency-safe: кожен виклик незалежний.
        """
        try:
            display = await self._get_display_settings(chat_id)
            sent_ids = await self._send_single(alert, chat_id=chat_id, display_settings=display, is_sniper_match=is_sniper_match)
            
            if sent_ids and self._db:
                alert_dict = self._serialize_alert(alert)
                
                # Save mapping for both merchants to allow triggering updates on either of them
                if alert.buy_order.merchant_id:
                    await self._db.save_sent_alert(
                        alert.buy_order.exchange,
                        alert.buy_order.merchant_id,
                        chat_id,
                        sent_ids,
                        alert_dict,
                        display,
                        is_sniper_match
                    )
                if alert.sell_order.merchant_id:
                    await self._db.save_sent_alert(
                        alert.sell_order.exchange,
                        alert.sell_order.merchant_id,
                        chat_id,
                        sent_ids,
                        alert_dict,
                        display,
                        is_sniper_match
                    )

                # 📈 Prometheus: count sent alerts
                try:
                    from core.analytics.metrics import alerts_sent_total
                    alerts_sent_total.labels(
                        route_type=alert_dict.get("route_type", "UNKNOWN")
                    ).inc()
                except Exception:
                    pass
        except Exception as e:
            logger.error("send_to_user [%d]: %s", chat_id, e)

    async def _send_single(self, alert: SpreadAlert, chat_id: int | None = None, display_settings: dict | None = None,
                           is_sniper_match: bool = False) -> list[int]:
        from bot.alert_builder import send_single
        return await send_single(self, alert, chat_id=chat_id, display_settings=display_settings, is_sniper_match=is_sniper_match)

    async def redraw_alerts_for_merchant(self, exchange: str, merchant_id: str) -> None:
        """
        Знаходить алерти, відправлені за останні 30 хвилин для цього мерчанта,
        і перемальовує (редагує) їх за новими даними з бази даних.
        """
        if not self._db:
            return
        
        lock = self._redraw_locks.setdefault((exchange, merchant_id), asyncio.Lock())
        async with lock:
            try:
                recent_alerts = await self._db.get_recent_sent_alerts(exchange, merchant_id, max_age_seconds=1800)
                if not recent_alerts:
                    # Retry once after a 2.5s delay to resolve in-flight Telegram HTTP request race condition
                    await asyncio.sleep(2.5)
                    recent_alerts = await self._db.get_recent_sent_alerts(exchange, merchant_id, max_age_seconds=1800)
                    if not recent_alerts:
                        return
                
                logger.info("🔄 Перемальовка %d алертів для мерчанта %s [%s]", len(recent_alerts), merchant_id, exchange)
                
                from bot.alert_builder import send_single, SpreadAlert
                from exchanges.base import Order
                from decimal import Decimal
                
                for item in recent_alerts:
                    chat_id = item["chat_id"]
                    message_ids = item["message_ids"]
                    alert_dict = item["alert_dict"]
                    display = item["display_settings"]
                    is_sniper = item["is_sniper_match"]
                    
                    if alert_dict.get("is_taker", False):
                        # Reconstruct Taker order
                        order_dict = alert_dict["order"]
                        taker_mode = alert_dict["taker_mode"]
                        
                        # Convert required fields to Decimal
                        for field in ("price", "available_amount", "min_limit", "max_limit"):
                            if field in order_dict:
                                order_dict[field] = Decimal(str(order_dict[field]))

                        order_dict["month_order_count"] = int(float(order_dict.get("month_order_count", 0)))
                        order_dict["finish_rate_pct"] = float(order_dict.get("finish_rate_pct", 100.0))
                        order_dict["is_verified"] = str(order_dict.get("is_verified", "False")) in ("True", "1", "true")
                        order_dict["account_age_days"] = int(float(order_dict.get("account_age_days", 0)))
                        order_dict["composite_score"] = int(float(order_dict.get("composite_score", 0)))
                        order_dict["review_score"] = int(float(order_dict.get("review_score", 0)))
                        order_dict["review_neg_pct"] = float(order_dict.get("review_neg_pct", 0.0))
                        order_dict["review_fetched"] = str(order_dict.get("review_fetched", "False")) in ("True", "1", "true")
                        order_dict["positive_rate"] = float(order_dict.get("positive_rate", 0.0))

                        # Normalize regex_warn_flags
                        raw_warn = order_dict.get("regex_warn_flags")
                        if isinstance(raw_warn, str):
                            order_dict["regex_warn_flags"] = []
                        elif isinstance(raw_warn, list):
                            normalized = []
                            for item_warn in raw_warn:
                                if isinstance(item_warn, (list, tuple)) and len(item_warn) >= 2:
                                    normalized.append((str(item_warn[0]), str(item_warn[1])))
                                elif isinstance(item_warn, str):
                                    normalized.append((item_warn, ""))
                            order_dict["regex_warn_flags"] = normalized
                        else:
                            order_dict["regex_warn_flags"] = []

                        import inspect
                        valid_fields = set(inspect.signature(Order).parameters.keys())
                        order_obj = Order(**{k: v for k, v in order_dict.items() if k in valid_fields})
                        for k, v in order_dict.items():
                            if k not in valid_fields:
                                setattr(order_obj, k, v)

                        from bot.taker_builder import send_taker_single
                        await send_taker_single(
                            self,
                            order_obj,
                            taker_mode,
                            chat_id=chat_id,
                            display_settings=display,
                            edit_message_ids=message_ids
                        )
                        
                        # Update serialization in DB to match latest state
                        updated_dict = {
                            "is_taker": True,
                            "taker_mode": taker_mode,
                            "order": self._serialize_order(order_obj),
                        }
                        await self._db.update_sent_alert_dict(chat_id, message_ids, updated_dict)

                        # 📈 Prometheus
                        try:
                            from core.analytics.metrics import alerts_redrawn_total
                            alerts_redrawn_total.labels(exchange=exchange).inc()
                        except Exception:
                            pass
                        continue
                    
                    # Reconstruct order objects from raw dict
                    buy_dict = alert_dict["buy_order"]
                    sell_dict = alert_dict["sell_order"]
                    
                    # Convert required fields to Decimal
                    for o_dict in (buy_dict, sell_dict):
                        for field in ("price", "available_amount", "min_limit", "max_limit"):
                            if field in o_dict:
                                o_dict[field] = Decimal(str(o_dict[field]))

                        # Handle conversion of other numeric fields
                        o_dict["month_order_count"] = int(float(o_dict.get("month_order_count", 0)))
                        o_dict["finish_rate_pct"] = float(o_dict.get("finish_rate_pct", 100.0))
                        o_dict["is_verified"] = str(o_dict.get("is_verified", "False")) in ("True", "1", "true")
                        o_dict["account_age_days"] = int(float(o_dict.get("account_age_days", 0)))
                        o_dict["composite_score"] = int(float(o_dict.get("composite_score", 0)))
                        o_dict["review_score"] = int(float(o_dict.get("review_score", 0)))
                        o_dict["review_neg_pct"] = float(o_dict.get("review_neg_pct", 0.0))
                        o_dict["review_fetched"] = str(o_dict.get("review_fetched", "False")) in ("True", "1", "true")
                        o_dict["positive_rate"] = float(o_dict.get("positive_rate", 0.0))

                        # Normalize regex_warn_flags: може бути str (старий формат) або list[list/tuple]
                        raw_warn = o_dict.get("regex_warn_flags")
                        if isinstance(raw_warn, str):
                            # Старий формат: str(list) — відновити неможливо, просто скидаємо
                            o_dict["regex_warn_flags"] = []
                        elif isinstance(raw_warn, list):
                            # list[list[str]] → list[tuple[str, str]]
                            normalized = []
                            for item in raw_warn:
                                if isinstance(item, (list, tuple)) and len(item) >= 2:
                                    normalized.append((str(item[0]), str(item[1])))
                                elif isinstance(item, str):
                                    normalized.append((item, ""))
                            o_dict["regex_warn_flags"] = normalized
                        else:
                            o_dict["regex_warn_flags"] = []

                    import inspect
                    valid_fields = set(inspect.signature(Order).parameters.keys())
                    buy_order = Order(**{k: v for k, v in buy_dict.items() if k in valid_fields})
                    for k, v in buy_dict.items():
                        if k not in valid_fields:
                            setattr(buy_order, k, v)
                    sell_order = Order(**{k: v for k, v in sell_dict.items() if k in valid_fields})
                    for k, v in sell_dict.items():
                        if k not in valid_fields:
                            setattr(sell_order, k, v)
                    
                    alert = SpreadAlert(
                        buy_order=buy_order,
                        sell_order=sell_order,
                        spread_pct=alert_dict["spread_pct"],
                        profit_uah=alert_dict["profit_uah"],
                        deal_amount_uah=alert_dict["deal_amount_uah"],
                        buy_bank=alert_dict["buy_bank"],
                        sell_bank=alert_dict["sell_bank"],
                        buy_banks_all=alert_dict.get("buy_banks_all"),
                        sell_banks_all=alert_dict.get("sell_banks_all"),
                        buy_banks_fit=alert_dict.get("buy_banks_fit"),
                        sell_banks_fit=alert_dict.get("sell_banks_fit"),
                        route_variants=alert_dict.get("route_variants"),
                        route_type=alert_dict.get("route_type", "UNKNOWN"),
                        route_pairs=alert_dict.get("route_pairs"),
                    )
                    alert.is_asymmetric = alert_dict.get("is_asymmetric", False)
                    alert.asymmetric_details = alert_dict.get("asymmetric_details")
                    
                    # Trigger edit
                    await send_single(
                        self,
                        alert,
                        chat_id=chat_id,
                        display_settings=display,
                        is_sniper_match=is_sniper,
                        edit_message_ids=message_ids
                    )

                    # Update the alert serialization in DB to match latest state
                    updated_dict = self._serialize_alert(alert)
                    await self._db.update_sent_alert_dict(chat_id, message_ids, updated_dict)

                    # 📈 Prometheus
                    try:
                        from core.analytics.metrics import alerts_redrawn_total
                        alerts_redrawn_total.labels(exchange=exchange).inc()
                    except Exception:
                        pass
            except Exception as e:
                logger.error("Error in redraw_alerts_for_merchant: %s", e, exc_info=True)

    async def _send_batch(self, batch: list[SpreadAlert]) -> None:
        from bot.alert_builder import send_batch
        await send_batch(self, batch)

    async def send_taker_to_user(self, chat_id: int, orders: list[Order], mode: str,
                                 group: bool | None = None) -> None:
        from bot.taker_builder import send_taker_to_user
        await send_taker_to_user(self, chat_id, orders, mode, group=group)

    async def _send_taker_single(self, order: Order, mode: str, chat_id: int | None = None, display_settings: dict | None = None) -> None:
        from bot.taker_builder import send_taker_single
        await send_taker_single(self, order, mode, chat_id=chat_id, display_settings=display_settings)

    async def send_maker_buy_suggestion(self, chat_id: int, advice: dict) -> None:
        from bot.maker_builder import send_maker_buy_suggestion
        await send_maker_buy_suggestion(self, chat_id, advice)

    async def send_maker_sell_update(self, chat_id: int, advice: dict) -> None:
        from bot.maker_builder import send_maker_sell_update
        await send_maker_sell_update(self, chat_id, advice)

    async def send_maker_order_alert(self, chat_id: int, order_info: dict, counterparty: Order, exchange: str) -> None:
        from bot.maker_builder import send_maker_order_alert
        await send_maker_order_alert(self, chat_id, order_info, counterparty, exchange)

    async def start(self) -> None:
        # 🚀 СТВОРЮЄМО СИСТЕМНЕ МЕНЮ КНОПКОЮ (Повний список)
        commands = [
            BotCommand(command="start", description="▶️ Запустити мій сканер (Дашборд)"),
            BotCommand(command="stop", description="🛑 Зупинити мій сканер"),
            BotCommand(command="cards", description="💳 Дашборд моїх карток"),
            BotCommand(command="report", description="📊 Звіт по оборотах карток"),
            BotCommand(command="set_bank_limits", description="⚙️ Налаштування банківських лімітів"),
            BotCommand(command="active", description="📡 Активні спреди зараз"),
            BotCommand(command="mode", description="🎯 Режим сканування"),
            BotCommand(command="ad", description="📢 Створити P2P оголошення"),
            BotCommand(command="ads", description="📝 Мої активні оголошення"),
            BotCommand(command="orders", description="📋 Мої поточні угоди"),
            BotCommand(command="features", description="🛠 Експериментальні функції"),
            BotCommand(command="balance", description="💰 Перевірити баланси"),
            BotCommand(command="keys", description="🔑 Підключені API Ключі"),
            BotCommand(command="connect", description="🔌 Підключити біржу"),
            BotCommand(command="disconnect", description="❌ Відключити біржу"),
            BotCommand(command="stats", description="📊 Статистика торгівлі"),
            BotCommand(command="sessions", description="🩺 Стан auth-сесій"),
            BotCommand(command="trades", description="📋 Активні торгові сесії"),
            BotCommand(command="status", description="📊 Системний статус сканера"),
            BotCommand(command="settings", description="⚙️ Глобальні налаштування"),
            BotCommand(command="ban", description="🚫 Ручний бан мерчанта"),
            BotCommand(command="users", description="👥 Користувачі (адмін)"),
            BotCommand(command="help", description="📖 Довідка"),
        ]
        await self._bot.set_my_commands(commands)

        self._worker_task = asyncio.create_task(
            self._worker_loop(), name="tg-notifier-worker"
        )
        self._polling_task = asyncio.create_task(
            self._dp.start_polling(self._bot), name="tg-polling"
        )
        logger.info("TelegramNotifier запущено (з інтерактивними кнопками та Меню)")

    async def stop(self) -> None:
        if self._polling_task:
            self._polling_task.cancel()
        try:
            await asyncio.wait_for(self._queue.join(), timeout=10.0)
        except asyncio.TimeoutError:
            logger.warning("Timeout при зупинці нотифікатора.")
        if self._worker_task:
            self._worker_task.cancel()
        await self._bot.session.close()
        logger.info("TelegramNotifier зупинено")

    async def push(self, alert: SpreadAlert) -> None:
        try:
            self._queue.put_nowait(alert)
        except asyncio.QueueFull:
            try:
                dropped = self._queue.get_nowait()
                self._queue.task_done()
                logger.warning("Черга переповнена — скинуто алерт %.2f%%", dropped.spread_pct)
            except asyncio.QueueEmpty:
                pass
            await self._queue.put(alert)

    async def _worker_loop(self) -> None:
        while True:
            try:
                batch = await self._collect_batch()
                if not batch:
                    continue

                batch.sort(key=lambda a: a.spread_pct, reverse=True)
                for i, alert in enumerate(batch):
                    await self._send_single(alert)
                    if i < len(batch) - 1:
                        await asyncio.sleep(0.8)

                for _ in batch:
                    self._queue.task_done()

                await asyncio.sleep(self._send_interval)

            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error("Помилка воркера нотифікатора: %s", e, exc_info=True)
                await asyncio.sleep(1.0)

    async def _collect_batch(self) -> list[SpreadAlert]:
        batch: list[SpreadAlert] = []
        try:
            first = await self._queue.get()
            batch.append(first)
        except asyncio.CancelledError:
            return []

        deadline = asyncio.get_event_loop().time() + self._group_window
        while len(batch) < self._batch_size:
            remaining = deadline - asyncio.get_event_loop().time()
            if remaining <= 0:
                break
            try:
                alert = await asyncio.wait_for(self._queue.get(), timeout=remaining)
                batch.append(alert)
            except asyncio.TimeoutError:
                break
        return batch

    async def _apply_throttling(self, chat_id: int) -> None:
        """
        Застосовує затримку (cooldown) перед відправкою повідомлення користувачу.
        У режимі AUTO розраховує динамічний кд за 5-секундним ковзним вікном.
        """
        if not chat_id:
            return
        
        display = await self._get_display_settings(chat_id)
        alert_cooldown = float(display.get("alert_cooldown", -1.0))
        
        now = asyncio.get_event_loop().time()
        
        # Визначаємо затримку
        if alert_cooldown < 0.0:  # АВТО режим
            auto_config = display.get("auto_cooldown_json") or {}
            window_sec = float(auto_config.get("window_seconds", 5.0))
            tiers = auto_config.get("tiers", [])
            
            # Очищуємо старі таймстемпи
            timestamps = self._sent_timestamps.setdefault(chat_id, [])
            while timestamps and now - timestamps[0] > window_sec:
                timestamps.pop(0)
            
            sent_count = len(timestamps)
            
            # Визначаємо затримку відповідно до порогів
            cooldown = 0.0
            if tiers:
                # tiers сортовані за зростанням threshold
                sorted_tiers = sorted(tiers, key=lambda t: t.get("threshold", 9999))
                for t in sorted_tiers:
                    if sent_count < t.get("threshold", 9999):
                        cooldown = float(t.get("delay", 0.0))
                        break
            else:
                # Дефолтна логіка: <10 -> 0.0, 10-15 -> 0.3, 15-20 -> 0.8, >=20 -> 1.5
                if sent_count < 10:
                    cooldown = 0.0
                elif sent_count < 15:
                    cooldown = 0.3
                elif sent_count < 20:
                    cooldown = 0.8
                else:
                    cooldown = 1.5
        else:
            cooldown = alert_cooldown
            
        if cooldown > 0.0:
            last_time = self._last_send_time.get(chat_id, 0.0)
            elapsed = now - last_time
            if elapsed < cooldown:
                await asyncio.sleep(cooldown - elapsed)

    def _record_sent_timestamp(self, chat_id: int) -> None:
        if not chat_id:
            return
        now = asyncio.get_event_loop().time()
        self._last_send_time[chat_id] = now
        self._sent_timestamps.setdefault(chat_id, []).append(now)

    async def send_batch_to_user(self, chat_id: int, batch: list[SpreadAlert]) -> None:
        """Відправляє батч алертів конкретному користувачу."""
        from bot.alert_builder import send_batch
        await send_batch(self, batch, chat_id=chat_id)

    async def _send_with_retry(
            self,
            text: str,
            keyboard: InlineKeyboardMarkup | None = None,
            max_attempts: int = 3,
            disable_notification: bool = False,
            chat_id: int | None = None,
            reply_to_message_id: int | None = None,
    ):
        target_chat = chat_id or self._chat_id
        for attempt in range(max_attempts):
            try:
                await self._apply_throttling(target_chat)
                msg = await self._bot.send_message(
                    chat_id=target_chat,
                    text=text,
                    reply_markup=keyboard,
                    disable_web_page_preview=True,
                    disable_notification=disable_notification,
                    reply_to_message_id=reply_to_message_id,
                )
                logger.debug("✅ TG sent → chat_id=%s (len=%d)", target_chat, len(text))
                self._record_sent_timestamp(target_chat)
                return msg
            except TelegramRetryAfter as e:
                logger.warning("⚠️ TG Send Flood. Sleeping for %.1fs", e.retry_after)
                await asyncio.sleep(e.retry_after + 0.5)
                self._last_send_time[target_chat] = asyncio.get_event_loop().time()
                
                # Сповіщення про флуд-ліміт (не частіше раз на хвилину)
                now = asyncio.get_event_loop().time()
                last_warn = self._last_flood_warning_time.get(target_chat, 0.0)
                if now - last_warn > 60.0:
                    self._last_flood_warning_time[target_chat] = now
                    try:
                        warning_text = (
                            f"⚠️ <b>Увага! Бот отримав обмеження флуду від Telegram (429).</b>\n\n"
                            f"Через занадто різкий пік повідомлень відправку призупинено на <b>{e.retry_after:.1f} сек</b>.\n\n"
                            f"💡 <b>Порада:</b> Ви можете скоригувати параметри авто-затримки:\n"
                            f"⚙️ <code>/settings</code> ➔ 🎛 <b>Фільтри</b> ➔ 🖥 <b>Налаштування виводу</b> ➔ ⏱ <b>Авто-затримка</b>"
                        )
                        await self._bot.send_message(chat_id=target_chat, text=warning_text)
                    except Exception as warn_err:
                        logger.error("Failed to send flood warning message: %s", warn_err)
            except Exception as e:
                logger.error("Помилка відправки в Telegram: %s", e)
                if attempt == max_attempts - 1:
                    raise
                await asyncio.sleep(2.0)

    async def _edit_with_retry(
            self,
            message_id: int,
            text: str,
            keyboard: InlineKeyboardMarkup | None = None,
            max_attempts: int = 3,
            chat_id: int | None = None,
    ):
        target_chat = chat_id or self._chat_id
        for attempt in range(max_attempts):
            try:
                await self._apply_throttling(target_chat)
                msg = await self._bot.edit_message_text(
                    chat_id=target_chat,
                    message_id=message_id,
                    text=text,
                    reply_markup=keyboard,
                    disable_web_page_preview=True,
                )
                logger.debug("✅ TG edited → chat_id=%s msg_id=%d", target_chat, message_id)
                self._record_sent_timestamp(target_chat)
                return msg
            except TelegramRetryAfter as e:
                logger.warning("⚠️ TG Edit Flood. Sleeping for %.1fs", e.retry_after)
                await asyncio.sleep(e.retry_after + 0.5)
                self._last_send_time[target_chat] = asyncio.get_event_loop().time()
                
                # Сповіщення про флуд-ліміт (не частіше раз на хвилину)
                now = asyncio.get_event_loop().time()
                last_warn = self._last_flood_warning_time.get(target_chat, 0.0)
                if now - last_warn > 60.0:
                    self._last_flood_warning_time[target_chat] = now
                    try:
                        warning_text = (
                            f"⚠️ <b>Увага! Бот отримав обмеження флуду від Telegram (429).</b>\n\n"
                            f"Через занадто різкий пік повідомлень відправку призупинено на <b>{e.retry_after:.1f} сек</b>.\n\n"
                            f"💡 <b>Порада:</b> Ви можете скоригувати параметри авто-затримки:\n"
                            f"⚙️ <code>/settings</code> ➔ 🎛 <b>Фільтри</b> ➔ 🖥 <b>Налаштування виводу</b> ➔ ⏱ <b>Авто-затримка</b>"
                        )
                        await self._bot.send_message(chat_id=target_chat, text=warning_text)
                    except Exception as warn_err:
                        logger.error("Failed to send flood warning message: %s", warn_err)
            except Exception as e:
                if "message is not modified" in str(e):
                    logger.debug("ℹ️ TG Edit: Message is not modified. Ignoring.")
                    return None
                logger.error("Помилка редагування в Telegram (attempt %d): %s", attempt, e)
                if attempt == max_attempts - 1:
                    raise
                await asyncio.sleep(2.0)

    @staticmethod
    def _split_message(text: str, limit: int = 4096) -> list[str]:
        if len(text) <= limit:
            return [text]
        chunks, current = [], ""
        for line in text.split("\n"):
            if len(current) + len(line) + 1 > limit:
                if current:
                    chunks.append(current)
                current = line
            else:
                current = current + "\n" + line if current else line
        if current:
            chunks.append(current)
        return chunks