# notifications/telegram_notifier.py
import asyncio
import logging
from dataclasses import dataclass
from datetime import datetime

from aiogram import Bot
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.exceptions import TelegramRetryAfter
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton

from config import settings
from exchanges.base import Order

logger = logging.getLogger(__name__)

EXCHANGE_ICONS = {
    "Binance":   "🟡",
    "Bybit":     "🟣",
    "OKX":       "🟢",
    "MEXC":      "🔵",
    "Wallet":    "👛",
    "CryptoBot": "🤖",
}

BANKS_MAP = {
    "43": "Monobank",
    "14": "PrivatBank",
    "64": "ПУМБ",
    "48": "А-Банк",
}

BANKS_SHORT = {
    "43": "Mono",
    "14": "Privat",
    "64": "ПУМБ",
    "48": "А-Банк",
}


# ── Dataclass ────────────────────────────────────────────────────────────────
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


# ── Helpers ───────────────────────────────────────────────────────────────────
def _profile_link(exchange: str, merchant_id: str, merchant_name: str) -> str:
    if not merchant_id:
        return merchant_name
    links = {
        "Binance": f"https://p2p.binance.com/en/advertiserDetail?advertiserNo={merchant_id}",
        "Bybit":   f"https://www.bybit.com/fiat/trade/otc/profile/{merchant_id}",
        "OKX":     f"https://www.okx.com/p2p/profile/{merchant_id}",
        "MEXC":    f"https://www.mexc.com/uk-UA/p2p/merchant/{merchant_id}",
    }
    url = links.get(exchange)
    if url:
        return f"<a href='{url}'>{merchant_name}</a>"
    if exchange in ["Wallet", "CryptoBot"]:
        return f"<b>{merchant_name}</b>"
    return merchant_name


def _verified_badge(order: Order) -> str:
    return " ✅" if getattr(order, "is_verified", False) else ""


def _risk_badge(order: Order, short: bool = False) -> str:
    flag = getattr(order, "risk_flag", "")
    if flag in ("", "OK"):
        return ""

    badges = {
        "TRIANGLE":          ("🚨", "<b>РИЗИК: ТРИКУТНИК</b>"),
        "CASINO":            ("🚨", "<b>РИЗИК: КАЗИНО/ПРОЦЕСИНГ</b>"),
        "SUSPICIOUS":        ("⚠️", "<b>ПІДОЗРІЛІ УМОВИ</b>"),
        "LOW_STATS":         ("⚠️", "<b>МАЛО УГОД / НИЗЬКИЙ %</b>"),
        "SUSPICIOUS_LIMITS": ("⚠️", "<b>АНОМАЛЬНІ ЛІМІТИ</b>"),
        "EMPTY_TERMS":       ("💬", "<i>Умови не вказані</i>"),
    }

    flags = [f.strip() for f in flag.split(",")]
    if short:
        return "".join(badges[f][0] for f in flags if f in badges)

    lines = []
    has_text_risk = False
    for f in flags:
        if f not in badges:
            continue
        icon, label = badges[f]
        lines.append(f"{icon} {label}")
        if f in ("TRIANGLE", "CASINO", "SUSPICIOUS"):
            has_text_risk = True

    if not lines:
        return ""

    result = "\n".join(lines) + "\n"
    trade_terms = getattr(order, "trade_terms", "")
    if has_text_risk and trade_terms:
        safe = trade_terms.replace("\n", " ")[:80]
        if len(trade_terms) > 80:
            safe += "…"
        result += f"📝 <i>Умови:</i> <code>{safe}</code>\n"
    return result


def _liquidity_bar(amount: float, step: float = 1000.0, total: int = 5) -> str:
    """█░░░░ — шкала об'єму угоди."""
    filled = min(total, int(amount // step))
    return "█" * filled + "░" * (total - filled)


# ── TelegramNotifier ──────────────────────────────────────────────────────────
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
        self._worker_task = asyncio.create_task(
            self._worker_loop(), name="tg-notifier-worker"
        )
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

    # ── Worker (каскадний вивід) ──────────────────────────────────────────────
    async def _worker_loop(self) -> None:
        while True:
            try:
                batch = await self._collect_batch()
                if not batch:
                    continue

                # Сортуємо: найкращий спред першим
                batch.sort(key=lambda a: a.spread_pct, reverse=True)

                # Відправляємо кожен спред окремою карткою
                for i, alert in enumerate(batch):
                    await self._send_single(alert)
                    if i < len(batch) - 1:
                        await asyncio.sleep(0.8)  # пауза між повідомленнями

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

    # ── Детальна картка ───────────────────────────────────────────────────────
    async def _send_single(self, alert: SpreadAlert) -> None:
        emoji  = "🔥" if alert.spread_pct >= 1.0 else "💡"
        b_icon = EXCHANGE_ICONS.get(alert.buy_order.exchange, "◽️")
        s_icon = EXCHANGE_ICONS.get(alert.sell_order.exchange, "◽️")
        b_bank = BANKS_MAP.get(alert.buy_bank, alert.buy_bank)
        s_bank = BANKS_MAP.get(alert.sell_bank, alert.sell_bank)
        bar    = _liquidity_bar(alert.deal_amount_uah)

        buy_risk  = _risk_badge(alert.buy_order)
        sell_risk = _risk_badge(alert.sell_order)

        text = (
            f"⚡️ <b>ARBIX QUANTUM</b>  {emoji} <b>{alert.spread_pct:.2f}%</b>\n"
            f"<code>{'─' * 28}</code>\n"
            f"Профіт:   <code>+{alert.profit_uah:.2f} ₴</code>\n"
            f"Угода:    <code>{alert.deal_amount_uah:.0f} ₴</code>  💧 <code>{bar}</code>\n"
            f"          <i>макс. доступно у мерчанта</i>\n"
            f"          <i>(ліміт мерчанта)</i>\n"
            f"Маршрут:  {b_icon} {alert.buy_order.exchange} → {s_icon} {alert.sell_order.exchange}\n"
            f"Час:      <code>{alert.timestamp.strftime('%H:%M:%S')}</code>\n"
            f"<code>{'─' * 28}</code>\n"
            f"\n"
            f"🛒 <b>КУПУЄМО</b>  {b_icon} {alert.buy_order.exchange}\n"
            f"Мерчант:  {_profile_link(alert.buy_order.exchange, alert.buy_order.merchant_id, alert.buy_order.merchant_name)}{_verified_badge(alert.buy_order)}\n"
            f"Рейтинг:  <code>{alert.buy_order.finish_rate_pct:.1f}%</code>  "
            f"Угод: <code>{alert.buy_order.month_order_count}</code>\n"
            f"Банк:     {b_bank}\n"
            f"Курс:     <code>{alert.buy_order.price} ₴</code>\n"
            f"Ліміти:   <code>{alert.buy_order.min_limit} – {alert.buy_order.max_limit} ₴</code>\n"
            + (buy_risk if buy_risk else "")
            + f"\n"
            f"💸 <b>ПРОДАЄМО</b>  {s_icon} {alert.sell_order.exchange}\n"
            f"Мерчант:  {_profile_link(alert.sell_order.exchange, alert.sell_order.merchant_id, alert.sell_order.merchant_name)}{_verified_badge(alert.sell_order)}\n"
            f"Рейтинг:  <code>{alert.sell_order.finish_rate_pct:.1f}%</code>  "
            f"Угод: <code>{alert.sell_order.month_order_count}</code>\n"
            f"Банк:     {s_bank}\n"
            f"Курс:     <code>{alert.sell_order.price} ₴</code>\n"
            f"Ліміти:   <code>{alert.sell_order.min_limit} – {alert.sell_order.max_limit} ₴</code>\n"
            + (sell_risk if sell_risk else "")
            + f"<code>{'─' * 28}</code>"
        )

        keyboard = InlineKeyboardMarkup(inline_keyboard=[[
            InlineKeyboardButton(text="🛒 Купити", url=alert.buy_order.link),
            InlineKeyboardButton(text="💸 Продати", url=alert.sell_order.link),
        ]])

        await self._send_with_retry(text, keyboard)

    # ── Компактний дашборд ────────────────────────────────────────────────────
    async def _send_batch(self, batch: list[SpreadAlert]) -> None:
        """Підсумок усіх знайдених маршрутів за цикл."""
        medals = ["1️⃣", "2️⃣", "3️⃣", "4️⃣", "5️⃣"]
        lines = [
            f"📋 <b>ПІДСУМОК: АКТУАЛЬНІ МАРШРУТИ</b>  "
            f"<code>[{batch[0].timestamp.strftime('%H:%M:%S')}]</code>\n"
            f"<code>{'─' * 28}</code>\n"
        ]

        for i, a in enumerate(batch[:5]):
            b_icon  = EXCHANGE_ICONS.get(a.buy_order.exchange, "◽️")
            s_icon  = EXCHANGE_ICONS.get(a.sell_order.exchange, "◽️")
            b_short = BANKS_SHORT.get(a.buy_bank, a.buy_bank)
            s_short = BANKS_SHORT.get(a.sell_bank, a.sell_bank)
            risks   = _risk_badge(a.buy_order, short=True) + _risk_badge(a.sell_order, short=True)
            b_nick  = _profile_link(a.buy_order.exchange, a.buy_order.merchant_id, a.buy_order.merchant_name)
            s_nick  = _profile_link(a.sell_order.exchange, a.sell_order.merchant_id, a.sell_order.merchant_name)

            lines.append(
                f"{medals[i]} <b>{a.spread_pct:.2f}%</b>  "
                f"<code>+{a.profit_uah:.0f} ₴</code>"
                f"{'  ' + risks if risks else ''}\n"
                f"  {b_icon} {b_nick} <code>{a.buy_order.price}</code> {b_short}\n"
                f"  {s_icon} {s_nick} <code>{a.sell_order.price}</code> {s_short}\n"
                f"  📐 <code>{a.buy_order.min_limit}–{a.buy_order.max_limit}</code> "
                f"→ <code>{a.sell_order.min_limit}–{a.sell_order.max_limit} ₴</code>\n"
                f"  <a href='{a.buy_order.link}'>Купити</a>  ·  "
                f"<a href='{a.sell_order.link}'>Продати</a>\n"
                f"<code>{'─' * 28}</code>\n"
            )

        text = "".join(lines)
        for chunk in self._split_message(text):
            await self._send_with_retry(chunk)

    # ── Helpers ───────────────────────────────────────────────────────────────
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
                    disable_web_page_preview=True,
                )
                return
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