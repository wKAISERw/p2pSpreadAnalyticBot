# notifications/telegram_notifier.py
import asyncio
import logging
from dataclasses import dataclass
from decimal import Decimal

from aiogram import Bot
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.exceptions import TelegramRetryAfter
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton

from config import settings
from exchanges.base import Order

logger = logging.getLogger(__name__)


@dataclass
class SpreadAlert:
    buy_order: Order
    sell_order: Order
    spread_pct: float
    profit_uah: float


def _risk_badge(order: Order) -> str:
    """Повертає рядок з попередженням про ризик ТА причиною (коментарем)."""
    # Якщо ордер не має ризику або прапорець пустий
    if getattr(order, "risk_flag", "") in ["", "OK"]:
        return ""

    badges = {
        "TRIANGLE": "🚨 <b>РИЗИК: ТРИКУТНИК</b>",
        "CASINO": "🚨 <b>РИЗИК: КАЗИНО/ПРОЦЕСИНГ</b>",
        "SUSPICIOUS": "⚠️ <b>ПІДОЗРІЛІ УМОВИ</b>",
        "LOW_STATS": "⚠️ <b>МАЛО УГОД / НИЗЬКИЙ %</b>",
        "SUSPICIOUS_LIMITS": "⚠️ <b>АНОМАЛЬНІ ЛІМІТИ</b>",
        "EMPTY_TERMS": "💬 <i>Умови не вказані</i>",
    }

    flag = getattr(order, "risk_flag", "")
    flag_text = badges.get(flag, "")

    if not flag_text:
        return ""

    reason = f"{flag_text}\n"

    # Якщо ризик текстовий — показуємо, на яких саме словах він попався
    trade_terms = getattr(order, "trade_terms", "")
    if flag in ["TRIANGLE", "CASINO", "SUSPICIOUS"] and trade_terms:
        # Обрізаємо довгі тексти до 100 символів, щоб не спамити
        safe_terms = trade_terms.replace('\n', ' ')[:100]
        if len(trade_terms) > 100:
            safe_terms += "..."
        reason += f"📝 <i>Текст мерчанта:</i> <code>{safe_terms}</code>\n"

    return reason


def _verified_badge(order) -> str:
    return " ✅" if getattr(order, "is_verified", False) else ""


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
        self._chat_id = settings.telegram_chat_id
        self._send_interval = send_interval
        self._group_window = group_window
        self._batch_size = batch_size

        self._queue: asyncio.Queue[SpreadAlert] = asyncio.Queue(maxsize=max_queue_size)
        self._worker_task: asyncio.Task | None = None

    # ------------------------------------------------------------------ #
    # Public API                                                           #
    # ------------------------------------------------------------------ #

    async def start(self) -> None:
        """Запускає фоновий воркер. Викликати один раз при старті."""
        self._worker_task = asyncio.create_task(
            self._worker_loop(), name="tg-notifier-worker"
        )
        logger.info("TelegramNotifier запущено")

    async def stop(self) -> None:
        """Graceful shutdown: чекає поки черга спустіє (макс 10с)."""
        try:
            await asyncio.wait_for(self._queue.join(), timeout=10.0)
        except asyncio.TimeoutError:
            logger.warning("Timeout при зупинці нотифікатора — деякі повідомлення втрачено")
        if self._worker_task:
            self._worker_task.cancel()
        await self._bot.session.close()
        logger.info("TelegramNotifier зупинено")

    async def push(self, alert: SpreadAlert) -> None:
        """Неблокуючий push. При переповненні черги — скидає найстаріший алерт."""
        try:
            self._queue.put_nowait(alert)
        except asyncio.QueueFull:
            try:
                dropped = self._queue.get_nowait()
                self._queue.task_done()
                logger.warning(
                    "Черга переповнена — скинуто старий алерт: спред=%.2f%%",
                    dropped.spread_pct,
                )
            except asyncio.QueueEmpty:
                pass
            await self._queue.put(alert)

    # ------------------------------------------------------------------ #
    # Worker                                                               #
    # ------------------------------------------------------------------ #

    async def _worker_loop(self) -> None:
        while True:
            try:
                batch = await self._collect_batch()
                if not batch:
                    continue

                if len(batch) == 1:
                    await self._send_single(batch[0])
                else:
                    await self._send_batch(batch)

                for _ in batch:
                    self._queue.task_done()

                await asyncio.sleep(self._send_interval)

            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error("Помилка воркера нотифікатора: %s", e, exc_info=True)
                await asyncio.sleep(1.0)

    async def _collect_batch(self) -> list[SpreadAlert]:
        """
        Збирає батч:
        1. Блокується на першому елементі (нескінченно)
        2. Чекає group_window секунд щоб зібрати більше
        3. Виходить раніше якщо досягнуто batch_size
        """
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

    # ------------------------------------------------------------------ #
    # Formatting                                                           #
    # ------------------------------------------------------------------ #

    async def _send_single(self, alert: SpreadAlert) -> None:
        emoji = "🔥" if alert.spread_pct >= 1.0 else "💡"
        text = (
            f"{emoji} <b>Спред: {alert.spread_pct:.2f}%</b>\n\n"
            f"💰 Профіт: <b>{alert.profit_uah:.2f} ₴</b> "
            f"з {settings.working_capital_uah:.0f} ₴\n\n"
            f"🛒 <b>КУПУЄМО</b>\n"
            f"Курс: <code>{alert.buy_order.price}</code> ₴\n"
            f"Мерчант: {alert.buy_order.merchant_name}{_verified_badge(alert.buy_order)} "
            f"({alert.buy_order.finish_rate_pct:.1f}% | "
            f"{alert.buy_order.month_order_count} угод)\n"
            f"Ліміти: {alert.buy_order.min_limit}–{alert.buy_order.max_limit} ₴\n"
            + _risk_badge(alert.buy_order) +
            f"\n💸 <b>ПРОДАЄМО</b>\n"
            f"Курс: <code>{alert.sell_order.price}</code> ₴\n"
            f"Мерчант: {alert.sell_order.merchant_name}{_verified_badge(alert.sell_order)} "
            f"({alert.sell_order.finish_rate_pct:.1f}% | "
            f"{alert.sell_order.month_order_count} угод)\n"
            f"Ліміти: {alert.sell_order.min_limit}–{alert.sell_order.max_limit} ₴\n"
            + _risk_badge(alert.sell_order)
        )

        keyboard = InlineKeyboardMarkup(inline_keyboard=[[
            InlineKeyboardButton(text="🛒 Купити", url=alert.buy_order.link),
            InlineKeyboardButton(text="💸 Продати", url=alert.sell_order.link),
        ]])

        await self._send_with_retry(text, keyboard)

    async def _send_batch(self, batch: list[SpreadAlert]) -> None:
        sorted_batch = sorted(batch, key=lambda a: a.spread_pct, reverse=True)
        lines = [f"📊 <b>Знайдено {len(batch)} спреди — зведення:</b>\n"]

        for i, a in enumerate(sorted_batch, 1):
            emoji = "🔥" if a.spread_pct >= 1.0 else "💡"
            lines.append(
                f"{emoji} <b>#{i} | {a.spread_pct:.2f}%</b> | "
                f"Профіт: {a.profit_uah:.2f}₴\n"
                f"   Купівля <code>{a.buy_order.price}</code> ₴ → "
                f"Продаж <code>{a.sell_order.price}</code> ₴\n"
                f"   <a href='{a.buy_order.link}'>{a.buy_order.merchant_name}</a>"
                + _verified_badge(a.buy_order) +
                f" → "
                f"<a href='{a.sell_order.link}'>{a.sell_order.merchant_name}</a>"
                + _verified_badge(a.sell_order) +
                (_risk_badge(a.buy_order) or _risk_badge(a.sell_order))
            )

        text = "\n".join(lines)

        # Safe Split: якщо текст > 4096 — розбиваємо на частини
        for chunk in self._split_message(text):
            await self._send_with_retry(chunk)

    # ------------------------------------------------------------------ #
    # Helpers                                                              #
    # ------------------------------------------------------------------ #

    async def _send_with_retry(
        self,
        text: str,
        keyboard: InlineKeyboardMarkup | None = None,
        max_attempts: int = 3,
    ) -> None:
        for attempt in range(max_attempts):
            try:
                await self._bot.send_message(
                    chat_id=self._chat_id,
                    text=text,
                    reply_markup=keyboard,
                )
                return
            except TelegramRetryAfter as e:
                wait = e.retry_after + 0.5
                logger.warning(
                    "Telegram rate limit — чекаємо %.1fs (спроба %d/%d)",
                    wait, attempt + 1, max_attempts,
                )
                await asyncio.sleep(wait)
            except Exception as e:
                logger.error("Помилка відправки в Telegram: %s", e)
                if attempt == max_attempts - 1:
                    raise
                await asyncio.sleep(2.0)

    @staticmethod
    def _split_message(text: str, limit: int = 4096) -> list[str]:
        """Розбиває текст на частини не більше limit символів по межах рядків."""
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