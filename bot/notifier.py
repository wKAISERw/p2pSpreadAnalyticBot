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

    def bind_db(self, db: MerchantDB):
        """Зв'язує нотифікатор з базою даних для обробки ручних скарг."""
        self._db = db
        self.card_notifier = CardNotifier(db, self._bot)

    def bind_commands(self, db: MerchantDB, account_clients: dict, trade_worker=None, single_leg_executor=None, maker_monitor=None, session_manager=None) -> None:
        """Оновлює змінні модуля. Router вже підключений в __init__."""
        bot_commands.setup(db, account_clients, trade_worker, notifier=self, single_leg_executor=single_leg_executor, maker_monitor=maker_monitor, session_manager=session_manager)
        self._taker_cache = bot_commands._taker_order_cache

    async def _get_display_settings(self, chat_id: int) -> dict:
        if not self._db:
            return {
                "show_ai_terms_summary": True, "show_full_terms": True,
                "show_ai_logic": True, "show_bank_details": True,
                "show_llm_summary": True, "show_card_recommendation": True
            }
        settings_dict = await self._db.get_user_display_settings(chat_id)
        if not settings_dict:
            return {
                "show_ai_terms_summary": True, "show_full_terms": True,
                "show_ai_logic": True, "show_bank_details": True,
                "show_llm_summary": True, "show_card_recommendation": True
            }
        return settings_dict

    async def send_to_user(self, chat_id: int, alert: "SpreadAlert", is_sniper_match: bool = False) -> None:
        """
        Multi-user: відправляє алерт в конкретний chat_id.
        chat_id передається явним параметром — без мутації self._chat_id.
        Повністю concurrency-safe: кожен виклик незалежний.
        """
        try:
            display = await self._get_display_settings(chat_id)
            await self._send_single(alert, chat_id=chat_id, display_settings=display, is_sniper_match=is_sniper_match)
        except Exception as e:
            logger.error("send_to_user [%d]: %s", chat_id, e)

    async def _send_single(self, alert: SpreadAlert, chat_id: int | None = None, display_settings: dict | None = None,
                           is_sniper_match: bool = False) -> None:
        from bot.alert_builder import send_single
        await send_single(self, alert, chat_id=chat_id, display_settings=display_settings, is_sniper_match=is_sniper_match)

    async def _send_batch(self, batch: list[SpreadAlert]) -> None:
        from bot.alert_builder import send_batch
        await send_batch(self, batch)

    async def send_taker_to_user(self, chat_id: int, orders: list[Order], mode: str) -> None:
        from bot.taker_builder import send_taker_to_user
        await send_taker_to_user(self, chat_id, orders, mode)

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
                msg = await self._bot.send_message(
                    chat_id=target_chat,
                    text=text,
                    reply_markup=keyboard,
                    disable_web_page_preview=True,
                    disable_notification=disable_notification,
                    reply_to_message_id=reply_to_message_id,
                )
                logger.debug("✅ TG sent → chat_id=%s (len=%d)", target_chat, len(text))
                return msg
            except TelegramRetryAfter as e:
                await asyncio.sleep(e.retry_after + 0.5)
            except Exception as e:
                logger.error("Помилка відправки в Telegram: %s", e)
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