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
from html import escape


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
        "Bybit":   f"https://www.bybit.com/fiat/trade/otc/profile/{merchant_id}",
        "OKX":     f"https://www.okx.com/p2p/profile/{merchant_id}",
        "MEXC":    f"https://www.mexc.com/uk-UA/p2p/merchant/{merchant_id}",
    }
    url = links.get(exchange)
    if url:
        return f"<a href='{url}'>{merchant_name}</a>"
    return f"<b>{merchant_name}</b>"


def _verified_badge(order: Order) -> str:
    return " ✅" if getattr(order, "is_verified", False) else ""


RISK_NAMES_UA = {
    "TRIANGLE":       "трикутник",
    "CASINO":         "казино/ставки",
    "CHAT_FIRST":     "спочатку в чат",
    "EXTERNAL_LINK":  "зовнішній месенджер",
    "NO_COMMENTS":    "заборона коментарів",
    "SUSPICIOUS_BIZ": "ФОП/бізнес-рахунок",
    "APPEAL_PRESSURE":"тиск апеляцією",
    "ANONYMOUS":      "анонімність",
    "THIRD_PARTY_HINT":"третя особа",
    "SOFT_FLAG":      "нестандартні умови",
    "NEW_USERS":      "таргет на новачків",
    "PERFECT_RATING": "підозрілий рейтинг",
    "BADREVIEWS":     "погані відгуки",
    "BLACKLIST":      "чорний список",
    "UNKNOWN_RISK":   "невідомий ризик",
}


def _risk_name_ua(risk_code: str) -> str:
    return RISK_NAMES_UA.get(risk_code, risk_code.lower().replace("_", " "))


def _risk_badge(order: Order, short: bool = False) -> str:
    flag = getattr(order, "risk_flag", "")
    if flag in ("", "OK"):
        return ""

    badges = {
        "TRIANGLE": "🚨 <b>РИЗИК: ТРИКУТНИК</b>",
        "CASINO": "🚨 <b>РИЗИК: КАЗИНО / БЕТ</b>",
        "CHAT_FIRST": "🚨 <b>РИЗИК: СПОЧАТКУ В ЧАТ</b>",
        "EXTERNAL_LINK": "🚨 <b>РИЗИК: ЗОВНІШНІЙ КОНТАКТ</b>",
        "NO_COMMENTS": "🚨 <b>РИЗИК: ЗАБОРОНА КОМЕНТАРІВ</b>",
        "SUSPICIOUS": "🚨 <b>ПІДОЗРІЛІ УМОВИ</b>",
        "LOW_STATS": "⚠️ <b>МАЛО СТАТИСТИКИ</b>",
        "SUSPICIOUS_LIMITS": "⚠️ <b>ПІДОЗРІЛІ ЛІМІТИ</b>",
        "PERFECT_RATING": "⚠️ <b>ПІДОЗРІЛИЙ ІДЕАЛЬНИЙ РЕЙТИНГ</b>",
        "HIGH_RISK_SCORE": "🚨 <b>ВИСОКИЙ НАКОПИЧЕНИЙ РИЗИК</b>",
        "LLM_BLOCK": "🤖 <b>AI: ЗАБЛОКОВАНО</b>",
        "LLM_SUSPICIOUS": "🤖 <b>AI: ПІДОЗРІЛО</b>",
        "LLM_PENDING": "⏳ <i>AI перевіряє…</i>",
        "LLM_UNKNOWN": "🤖 <i>AI timeout / unknown</i>",
        "EMPTY_TERMS": "ℹ️ <i>Немає умов угоди</i>",
        "BADREVIEWS": "⚠️ <b>ПОГАНІ ВІДГУКИ</b>",
        "BLACKLIST": "⛔ <b>BLACKLIST</b>",
        "BLOCK": "⛔ <b>ЗАБЛОКОВАНО</b>",
    }

    flags = [f.strip() for f in flag.split(",") if f.strip()]
    lines: list[str] = []
    has_text_risk = False
    review_reason = ""

    for f in flags:
        if f.startswith("BLOCK:BLACKLIST:"):
            reason = f[len("BLOCK:BLACKLIST:"):].strip()
            lines.append("⛔ <b>BLACKLIST</b>")
            if reason:
                review_reason = reason

        elif f.startswith("BLOCK:BADREVIEWS:"):
            reason = f[len("BLOCK:BADREVIEWS:"):].strip()
            lines.append("⛔ <b>БЛОК: ПОГАНІ ВІДГУКИ</b>")
            if reason:
                review_reason = reason

        elif f.startswith("BLOCK:"):
            parts = f.split(":", 2)
            risk = parts[1] if len(parts) > 1 else "BLOCK"
            reason = parts[2] if len(parts) > 2 else ""
            lines.append(badges.get(risk, badges["BLOCK"]))
            if reason:
                review_reason = reason
            if risk in ("TRIANGLE", "CASINO", "CHAT_FIRST", "EXTERNAL_LINK", "NO_COMMENTS", "SUSPICIOUS"):
                has_text_risk = True

        elif f.startswith("BADREVIEWS:"):
            reason = f[len("BADREVIEWS:"):].strip()
            lines.append(badges["BADREVIEWS"])
            if reason:
                review_reason = reason

        elif f.startswith("LLM_PENDING:"):
            parts = f.split(":", 2)
            risk = parts[1] if len(parts) > 1 else "UNKNOWN_RISK"
            score_raw = parts[2] if len(parts) > 2 else ""
            score_num = score_raw.lstrip("S") if score_raw.startswith("S") else score_raw
            risk_ua = _risk_name_ua(risk)
            msg = f"⏳ <i>AI перевіряє…</i>\n📊 Regex: {escape(risk_ua)}"
            if score_num:
                msg += f", score {escape(score_num)}"
            lines.append(msg)

        elif f.startswith("REGEX_WEAK:"):
            parts = f.split(":", 2)
            risk = parts[1] if len(parts) > 1 else "UNKNOWN_RISK"
            score_raw = parts[2] if len(parts) > 2 else ""
            score_num = score_raw.lstrip("S") if score_raw.startswith("S") else score_raw
            risk_ua = _risk_name_ua(risk)
            msg = f"📊 <i>Regex: {escape(risk_ua)}"
            if score_num:
                msg += f", score {escape(score_num)}"
            msg += "</i>"
            lines.append(msg)
            has_text_risk = True

        elif f.startswith("LLM_SUSPICIOUS:"):
            parts = f.split(":", 2)
            risk = parts[1] if len(parts) > 1 else "SUSPICIOUS"
            reason = parts[2] if len(parts) > 2 else ""
            lines.append(f"🤖 <b>AI: ПІДОЗРІЛО</b> — {escape(_risk_name_ua(risk))}")
            if reason:
                review_reason = reason

        elif f.startswith("LLM_UNKNOWN"):
            lines.append("🤖 <i>AI timeout / unknown</i>")

        elif f in badges:
            lines.append(badges[f])
            if f in ("TRIANGLE", "CASINO", "CHAT_FIRST", "EXTERNAL_LINK", "NO_COMMENTS", "SUSPICIOUS"):
                has_text_risk = True

        else:
            lines.append(f"ℹ️ <i>{escape(f)}</i>")

    if not lines:
        return ""

    if short:
        compact = []
        for line in lines[:2]:
            plain = (
                line.replace("<b>", "").replace("</b>", "")
                .replace("<i>", "").replace("</i>", "")
                .replace("<code>", "").replace("</code>", "")
            )
            compact.append(plain)
        return " | ".join(compact)

    result = "\n".join(lines)

    if review_reason:
        result += f"\nℹ️ Причина:\n<code>{escape(review_reason[:100])}</code>\n"

    trade_terms = getattr(order, "trade_terms", "")
    if has_text_risk and trade_terms:
        safe = trade_terms.replace("\n", " ").strip()[:80]
        if len(trade_terms) > 80:
            safe += "…"
        result += f"\n📝 Умови:\n<code>{escape(safe)}</code>\n"

    return result


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

    async def _send_single(self, alert: SpreadAlert) -> None:
        emoji = "🔥" if alert.spread_pct >= 1.0 else "💡"
        b_icon = EXCHANGE_ICONS.get(alert.buy_order.exchange, "◽️")
        s_icon = EXCHANGE_ICONS.get(alert.sell_order.exchange, "◽️")
        b_bank = BANKS_MAP.get(alert.buy_bank, alert.buy_bank)
        s_bank = BANKS_MAP.get(alert.sell_bank, alert.sell_bank)

        buy_name = _profile_link(
            alert.buy_order.exchange,
            alert.buy_order.merchant_id,
            alert.buy_order.merchant_name,
        )
        sell_name = _profile_link(
            alert.sell_order.exchange,
            alert.sell_order.merchant_id,
            alert.sell_order.merchant_name,
        )

        buy_risk = _risk_badge(alert.buy_order)
        sell_risk = _risk_badge(alert.sell_order)

        text = (
            f"{emoji} <b>Арбітражний сигнал</b>\n"
            f"Спред: <b>{alert.spread_pct:.2f}%</b>\n"
            f"Профіт: <b>{alert.profit_uah:.2f} ₴</b>\n"
            f"Сума: <b>{alert.deal_amount_uah:.0f} ₴</b>\n"
            f"Маршрут: {b_icon} {escape(alert.buy_order.exchange)} ({escape(str(b_bank))}) "
            f"→ {s_icon} {escape(alert.sell_order.exchange)} ({escape(str(s_bank))})\n"
            f"Час: <code>{alert.timestamp.strftime('%H:%M:%S')}</code>\n\n"

            f"🛒 <b>Купівля</b>\n"
            f"Ціна: <code>{escape(str(alert.buy_order.price))}</code>\n"
            f"Мерчант: {buy_name}{_verified_badge(alert.buy_order)} "
            f"({alert.buy_order.finish_rate_pct:.1f}% | {alert.buy_order.month_order_count} угод)\n"
            f"Ліміти: <code>{escape(str(alert.buy_order.min_limit))}–{escape(str(alert.buy_order.max_limit))} ₴</code>\n"
            f"{buy_risk if buy_risk else ''}"

            f"\n💸 <b>Продаж</b>\n"
            f"Ціна: <code>{escape(str(alert.sell_order.price))}</code>\n"
            f"Мерчант: {sell_name}{_verified_badge(alert.sell_order)} "
            f"({alert.sell_order.finish_rate_pct:.1f}% | {alert.sell_order.month_order_count} угод)\n"
            f"Ліміти: <code>{escape(str(alert.sell_order.min_limit))}–{escape(str(alert.sell_order.max_limit))} ₴</code>\n"
            f"{sell_risk if sell_risk else ''}"
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
                b_icon = EXCHANGE_ICONS.get(a.buy_order.exchange, "◽️")
                s_icon = EXCHANGE_ICONS.get(a.sell_order.exchange, "◽️")
                b_short = BANKS_SHORT.get(a.buy_bank, a.buy_bank)
                s_short = BANKS_SHORT.get(a.sell_bank, a.sell_bank)
                risks = _risk_badge(a.buy_order, short=True) + _risk_badge(a.sell_order, short=True)
                b_nick = _profile_link(a.buy_order.exchange, a.buy_order.merchant_id, a.buy_order.merchant_name)
                s_nick = _profile_link(a.sell_order.exchange, a.sell_order.merchant_id, a.sell_order.merchant_name)

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