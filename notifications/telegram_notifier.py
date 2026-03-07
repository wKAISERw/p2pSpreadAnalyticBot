# notifications/telegram_notifier.py
import asyncio
import logging
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal

from aiogram import Bot
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.exceptions import TelegramRetryAfter
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton

from config import settings
from exchanges.base import Order

logger = logging.getLogger(__name__)

# Словник іконок бірж для швидкого візуального сприйняття
EXCHANGE_ICONS = {
    "Binance": "🟡",
    "Bybit": "🟣",
    "OKX": "🟢",
    "MEXC": "🔵",
    "Wallet": "👛",
    "CryptoBot": "🤖"
}


@dataclass
class SpreadAlert:
    buy_order: Order
    sell_order: Order
    spread_pct: float
    profit_uah: float
    deal_amount_uah: float
    buy_bank: str
    sell_bank: str
    timestamp: datetime = None

    def __post_init__(self):
        if self.timestamp is None:
            self.timestamp = datetime.now()


def _profile_link(exchange: str, merchant_id: str, merchant_name: str) -> str:
    if not merchant_id:
        return merchant_name

    links = {
        "Binance": f"https://p2p.binance.com/en/advertiserDetail?advertiserNo={merchant_id}",
        "Bybit": f"https://www.bybit.com/fiat/trade/otc/profile/{merchant_id}",
        "OKX": f"https://www.okx.com/p2p/profile/{merchant_id}",
        "MEXC": f"https://www.mexc.com/uk-UA/p2p/merchant/{merchant_id}", # Оновлений формат
    }

    url = links.get(exchange)
    if url:
        return f"<a href='{url}'>{merchant_name}</a>"

    # Для Wallet/CryptoBot додаємо іконку додатка замість лінка
    if exchange in ["Wallet", "CryptoBot"]:
        return f"<b>{merchant_name}</b> 📱"

    return merchant_name


def _risk_badge(order: Order, short: bool = False) -> str:
    """Класичні бейджі з правильними іконками (без зайвих кружечків)."""
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

    short_badges = {
        "TRIANGLE": "🚨",
        "CASINO": "🚨",
        "SUSPICIOUS": "⚠️",
        "LOW_STATS": "⚠️",
        "SUSPICIOUS_LIMITS": "⚠️",
        "EMPTY_TERMS": "💬",
    }

    flags = getattr(order, "risk_flag", "").split(",")
    reasons = []
    short_icons = []
    has_text_risk = False

    for f in flags:
        f = f.strip()
        if f in badges:
            reasons.append(badges[f])
            short_icons.append(short_badges[f])
        if f in ["TRIANGLE", "CASINO", "SUSPICIOUS"]:
            has_text_risk = True

    if not reasons:
        return ""

    if short:
        return "".join(short_icons)

    reason_str = "\n".join(reasons) + "\n"

    trade_terms = getattr(order, "trade_terms", "")
    if has_text_risk and trade_terms:
        safe_terms = trade_terms.replace('\n', ' ')[:80]
        if len(trade_terms) > 80:
            safe_terms += "..."
        reason_str += f"📝 <i>Текст:</i> <code>{safe_terms}</code>\n"

    return reason_str


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

    async def start(self) -> None:
        self._worker_task = asyncio.create_task(self._worker_loop(), name="tg-notifier-worker")
        logger.info("TelegramNotifier запущено")

    async def stop(self) -> None:
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
                self._queue.get_nowait()
                self._queue.task_done()
            except asyncio.QueueEmpty:
                pass
            await self._queue.put(alert)

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

    async def _send_single(self, alert: SpreadAlert) -> None:
        """Повернення до класичного чистого дизайну з додаванням нової інфи."""
        emoji = "🔥" if alert.spread_pct >= 1.0 else "💡"

        banks_map = {"43": "Monobank", "14": "PrivatBank", "64": "ПУМБ", "48": "А-Банк"}
        b_bank = banks_map.get(alert.buy_bank, alert.buy_bank)
        s_bank = banks_map.get(alert.sell_bank, alert.sell_bank)

        buy_profile = _profile_link(alert.buy_order.exchange, alert.buy_order.merchant_id,
                                    alert.buy_order.merchant_name)
        sell_profile = _profile_link(alert.sell_order.exchange, alert.sell_order.merchant_id,
                                     alert.sell_order.merchant_name)
        # Розрахунок шкали ліквідності (на основі суми угоди)
        capacity = float(alert.deal_amount_uah)
        # 5 поділок: кожна по 1000 грн (або налаштуй під свій капітал)
        bars_count = min(5, int(capacity // 1000))
        liquidity_bar = "█" * bars_count + "░" * (5 - bars_count)
        time_str = alert.timestamp.strftime("%H:%M")

        # Класична структура, що легко читається
        text = (
            f"{emoji} Спред: <b>{alert.spread_pct:.2f}%</b>\n\n"
            f"💰 Профіт: <b>{alert.profit_uah:.2f} ₴</b>\n"
            f"💼 Угода: {alert.deal_amount_uah:.0f} ₴ (з {settings.working_capital_uah:.0f} ₴)\n"
            f"🔄 Маршрут: {alert.buy_order.exchange} ({b_bank}) ➔ {alert.sell_order.exchange} ({s_bank})\n\n"

            f"🛒 <b>КУПУЄМО</b>\n"
            f"Курс: <code>{alert.buy_order.price}</code> ₴\n"
            f"Мерчант: {buy_profile}{_verified_badge(alert.buy_order)} ({alert.buy_order.finish_rate_pct:.1f}% | {alert.buy_order.month_order_count} угод)\n"
            f"Ліміти: {alert.buy_order.min_limit}–{alert.buy_order.max_limit} ₴\n"
            f"{_risk_badge(alert.buy_order)}"

            f"\n💸 <b>ПРОДАЄМО</b>\n"
            f"Курс: <code>{alert.sell_order.price}</code> ₴\n"
            f"Мерчант: {sell_profile}{_verified_badge(alert.sell_order)} ({alert.sell_order.finish_rate_pct:.1f}% | {alert.sell_order.month_order_count} угод)\n"
            f"Ліміти: {alert.sell_order.min_limit}–{alert.sell_order.max_limit} ₴\n"
            f"{_risk_badge(alert.sell_order)}"

            f"\n⏱ <i>{time_str}</i>"
        )

        keyboard = InlineKeyboardMarkup(inline_keyboard=[[
            InlineKeyboardButton(text=f"🛒 Купити", url=alert.buy_order.link),
            InlineKeyboardButton(text=f"💸 Продати", url=alert.sell_order.link),
        ]])

        await self._send_with_retry(text, keyboard)

    async def _send_batch(self, batch: list[SpreadAlert]) -> None:
        """Мульти-вивід: клікабельні профілі, прямі лінки та чіткі назви мерчантів."""
        sorted_batch = sorted(batch, key=lambda a: a.spread_pct, reverse=True)
        # Використовуємо цифри, вони виглядають професійніше в списку
        numbers = ["1️⃣", "2️⃣", "3️⃣", "4️⃣", "5️⃣"]
        banks_map = {"43": "Mono", "14": "Privat", "64": "ПУМБ", "48": "А-Банк"}

        lines = ["🔥 <b>ТОП СПРЕДИ (Multi-Route)</b>\n"]
        lines.append("━━━━━━━━━━━━━━━\n")

        for i, a in enumerate(sorted_batch[:5]):
            num = numbers[i]
            buy = a.buy_order
            sell = a.sell_order

            b_bank = banks_map.get(a.buy_bank, a.buy_bank)
            s_bank = banks_map.get(a.sell_bank, a.sell_bank)

            # Генеруємо клікабельні імена мерчантів
            b_nick = _profile_link(buy.exchange, buy.merchant_id, buy.merchant_name)
            s_nick = _profile_link(sell.exchange, sell.merchant_id, sell.merchant_name)

            # Значки ризику (🔴🟡💬)
            risks = _risk_badge(buy, short=True) + _risk_badge(sell, short=True)
            risk_str = f" {risks}" if risks else ""

            # Формуємо блок маршруту
            lines.append(
                f"{num} <b>{a.spread_pct:.2f}% | +{a.profit_uah:.0f} ₴</b>{risk_str}\n"
                f"🔄 {buy.exchange} ➜ {sell.exchange} ({b_bank} ➜ {s_bank})\n"
                f"👤 {b_nick} ➜ {s_nick}\n"
                f"💼 <b>{a.deal_amount_uah:.0f} ₴</b> | <code>{buy.price}</code> ➜ <code>{sell.price}</code>\n"
                f"🔗 <a href='{buy.link}'>Купити</a> | <a href='{sell.link}'>Продати</a>\n"
            )
            lines.append("────────────────\n")

        text = "".join(lines)

        # Вимикаємо прев'ю, щоб посилання не створювали зайвого візуального шуму
        await self._send_with_retry(text)

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
                    disable_web_page_preview=True  # Вимикаємо прев'ю лінок, щоб не розтягувати повідомлення
                )
                return
            except TelegramRetryAfter as e:
                wait = e.retry_after + 0.5
                await asyncio.sleep(wait)
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