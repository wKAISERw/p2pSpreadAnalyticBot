# notifications/telegram_notifier.py
import asyncio
import logging
from dataclasses import dataclass
from datetime import datetime
from html import escape
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton, CallbackQuery, BotCommand
from aiogram import Bot, Dispatcher, Router, F
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.exceptions import TelegramRetryAfter
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton, CallbackQuery

from config import settings
from bot import commands as bot_commands
from exchanges.base import Order
from core.storage.merchant_db import MerchantDB

logger = logging.getLogger(__name__)

WARN_BADGES = {
    "RECEIPT_REQUIRED": "🧾 ПРОСИТЬ КВИТАНЦІЮ",
}
EXCHANGE_ICONS = {
    "Binance": "🟡",
    "Bybit": "🟣",
    "OKX": "🟢",
    "MEXC": "🔵",
    "Wallet": "👛",
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
    buy_bank: str = ""
    sell_bank: str = ""
    buy_banks_all: list[str] | None = None
    sell_banks_all: list[str] | None = None
    buy_banks_fit: list[str] | None = None
    sell_banks_fit: list[str] | None = None
    route_variants: list[str] | None = None
    route_type: str = ""
    timestamp: datetime | None = None

    def __post_init__(self):
        if self.timestamp is None:
            self.timestamp = datetime.now()


def _format_bank_list(codes: list[str] | None) -> str:
    if not codes:
        return "—"
    names = [BANKS_MAP.get(code, code) for code in codes]
    return ", ".join(escape(str(x)) for x in names)


def _format_route_variants(routes: list[str] | None, limit: int = 6) -> str:
    if not routes:
        return "—"
    safe = [escape(str(x)) for x in routes[:limit]]
    suffix = " …" if len(routes) > limit else ""
    return ", ".join(safe) + suffix


def _profile_link(exchange: str, merchant_id: str, merchant_name: str) -> str:
    safe_name = escape(str(merchant_name or "Unknown"))
    if not merchant_id:
        return safe_name

    links = {
        "Binance": f"https://p2p.binance.com/en/advertiserDetail?advertiserNo={merchant_id}",
        "Bybit": f"https://www.bybit.com/fiat/trade/otc/profile/{merchant_id}",
        "OKX": f"https://www.okx.com/p2p/profile/{merchant_id}",
        "MEXC": f"https://www.mexc.com/uk-UA/p2p/merchant/{merchant_id}",
    }
    url = links.get(exchange)
    if url:
        return f'<a href="{escape(url, quote=True)}">{safe_name}</a>'
    return f"<b>{safe_name}</b>"


def _verified_badge(order: Order) -> str:
    return " ✅" if getattr(order, "is_verified", False) else ""


def _alert_grade(spread_pct: float) -> tuple[str, bool]:
    if spread_pct >= 2.0:
        return "🦄 <b>СУПЕР ПРОФІТ</b> 🦄", False
    if spread_pct >= 1.0:
        return "🔥 <b>ГАРНИЙ СПРЕД</b> 🔥", False
    if spread_pct >= 0.5:
        return "💡 <b>БАЗОВИЙ СПРЕД</b>", True
    return "🤏 <b>МІКРО-СПРЕД</b>", True


def _regex_warn_block(order: Order, short: bool = False) -> str:
    warn_flags = getattr(order, "regex_warn_flags", None) or []
    if not warn_flags:
        return ""

    if short:
        return "🧾"

    lines: list[str] = ["⚠️ <b>WARN-СИГНАЛИ</b>\n"]
    seen = set()

    for category, excerpt in warn_flags[:2]:
        badge = WARN_BADGES.get(category, f"⚠️ {category}")
        key = (category, excerpt)
        if key in seen:
            continue
        seen.add(key)

        lines.append(f"{badge}\n")
        if excerpt:
            lines.append(f"<code>{escape(str(excerpt)[:140])}</code>\n")

    score = int(getattr(order, "regex_score", 0) or 0)
    if score > 0:
        lines.append(f"Regex score: <code>{score}</code>\n")

    return "".join(lines)


def _risk_badge(order: Order, short: bool = False) -> str:
    flag = getattr(order, "risk_flag", "")

    if flag in ("", "OK", "PENDING"):
        return ""

    # 🚀 ОНОВЛЕНІ КАТЕГОРІЇ ЗГІДНО НОВИХ JSON-ПРАВИЛ
    badges = {
        "TRIANGLE": "🚫 ТРИКУТНИК / ДРОП\n" if not short else "🚫",
        "CASINO": "🎰 КАЗИНО / GAMBLING\n" if not short else "🎰",
        "CHAT_FIRST": "💬 СПОЧАТКУ В ЧАТ\n" if not short else "💬",
        "SUSPICIOUS_BIZ": "🏢 ПІДОЗРІЛИЙ BIZ-КОНТЕКСТ\n" if not short else "🏢",
        "APPEAL_PRESSURE": "⚠️ ТИСК АПЕЛЯЦІЄЮ\n" if not short else "⚠️",
        "ANONYMOUS": "🕶 АНОНІМНИЙ КОНТЕКСТ\n" if not short else "🕶",
        "EXTERNAL_LINK": "📲 ЗОВНІШНІЙ КОНТАКТ\n" if not short else "📲",
        "FINCRIME": "🏴‍☠️ ФІНМОН / СІРА СХЕМА\n" if not short else "🏴‍☠️",
        "CHARGEBACK": "🔙 РЕФАНД / ЧАРДЖБЕК\n" if not short else "🔙",
        "NO_COMMENTS": "🤫 БЕЗ КОМЕНТАРІВ\n" if not short else "🤫",
        "MIDDLEMAN": "👥 ПОСЕРЕДНИК / ПРОКЛАДКА\n" if not short else "👥",
        "THIRD_PARTY_HINT": "👤 ЗГАДКА 3-Х ОСІБ\n" if not short else "👤",

        "LOW_STATS": "⚠️ МАЛО УГОД / НИЗЬКИЙ %\n" if not short else "📉",
        "SUSPICIOUS_LIMITS": "⚠️ АНОМАЛЬНІ ЛІМІТИ\n" if not short else "📏",
        "PERFECT_RATING": "🤖 ПІДОЗРІЛИЙ РЕЙТИНГ\n" if not short else "🤖",
        "LLM_BLOCK": "🧠 AI BLOCK\n" if not short else "🧠",
        "LLM_SUSPICIOUS": "🧠 AI ПІДОЗРА\n" if not short else "🧠",
        "LLM_PENDING": "⏳ AI ANALYZE\n" if not short else "⏳",
        "LLM_UNKNOWN": "⌛ AI TIMEOUT\n" if not short else "⌛",
        "REGEX_WEAK": "🧩 WEAK REGEX\n" if not short else "🧩",
        "BADREVIEWS": "🗣 ПОГАНІ ВІДГУКИ\n" if not short else "🗣",
        "BLACKLIST": "⛔ BLACKLIST\n" if not short else "⛔",
        "HIGH_RISK_SCORE": "📛 HIGH RISK SCORE\n" if not short else "📛",
        "BLOCK": "⛔ BLOCK\n" if not short else "⛔",
    }

    # Категорії, для яких обов'язково треба показати уривок тексту в телеграмі
    show_text_cats = {"TRIANGLE", "CASINO", "CHAT_FIRST", "EXTERNAL_LINK", "FINCRIME", "CHARGEBACK", "NO_COMMENTS",
                      "MIDDLEMAN"}

    flags = [f.strip() for f in flag.split(",") if f.strip()]
    lines = []
    reasons = []
    has_text_risk = False



    for f in flags:
        if f.startswith("BADREVIEWS:"):
            lines.append(badges["BADREVIEWS"])
            reasons.append(f[len("BADREVIEWS:"):])
            continue
        # 🚀 НОВІ ПОВЕДІНКОВІ МАРКЕРИ (Deep Research)
        if f.startswith("API_REPLENISH:"):
            parts = f.split(":", 1)
            count = parts[1] if len(parts) > 1 else ""
            lines.append(f"🤖 АВТОПОПОВНЕННЯ БОТОМ ({count} цикл.)\n" if not short else "🤖")
            continue

        if f.startswith("STATIC_DROP:"):
            parts = f.split(":", 1)
            count = parts[1] if len(parts) > 1 else ""
            lines.append(f"📏 СТАТИЧНИЙ ДРОП ({count} цикл.)\n" if not short else "📏")
            continue

        if f.startswith("VELOCITY_SPIKE:"):
            parts = f.split(":", 1)
            vel = parts[1] if len(parts) > 1 else ""
            lines.append(f"⚡ АНОМАЛЬНА АКТИВНІСТЬ ({vel})\n" if not short else "⚡")
            continue

        if f == "EXACT_LIMITS":
            lines.append("🎯 ФІКСОВАНА СУМА (min=max)\n" if not short else "🎯")
            continue

        if f.startswith("BEHAVIOR_BOTLIKE:"):
            lines.append("🤖 ПІДОЗРІЛА ПОВЕДІНКА (БОТ)\n" if not short else "🤖")
            continue

        if f.startswith("CROSS_EXCHANGE_BOT:"):
            parts = f.split(":", 2)
            ex_names = parts[2].replace("CLONES:", "") if len(parts) > 2 else ""
            lines.append(f"👯 КЛОН НА БІРЖАХ: {ex_names}\n" if not short else "👯")
            continue

        if f.startswith("BLOCK:"):
            parts = f.split(":", 2)
            risk = parts[1] if len(parts) > 1 else "BLOCK"
            reason = parts[2] if len(parts) > 2 else ""
            if risk == "BLACKLIST":
                lines.append(badges["BLACKLIST"])
            else:
                lines.append(badges.get(risk, badges["BLOCK"]))
            if reason:
                reasons.append(reason)
            if risk in show_text_cats:
                has_text_risk = True
            continue

        if f.startswith("LLM_SUSPICIOUS:"):
            parts = f.split(":", 2)
            lines.append(badges["LLM_SUSPICIOUS"])
            if len(parts) > 2 and parts[2]:
                reasons.append(parts[2])
            continue

        if f.startswith("LLM_UNKNOWN:"):
            parts = f.split(":", 2)
            lines.append(badges["LLM_UNKNOWN"])
            if len(parts) > 2 and parts[2]:
                reasons.append(parts[2])
            continue

        if f.startswith("REGEX_WEAK:"):
            parts = f.split(":", 2)
            lines.append(badges["REGEX_WEAK"])
            if len(parts) > 2 and parts[2]:
                reasons.append(parts[2])
            continue

        if f.startswith("LLM_PENDING:"):
            lines.append(badges["LLM_PENDING"])
            continue

        if f in badges:
            lines.append(badges[f])

    if not lines:
        return ""

    result = "".join(lines)

    if reasons and not short:
        uniq = []
        seen = set()
        for r in reasons:
            r = (r or "").strip()
            if r and r not in seen:
                seen.add(r)
                uniq.append(r[:140])

        if uniq:
            result += "\n"
            for r in uniq[:3]:
                result += f"<code>{escape(r)}</code>\n"

    trade_terms = getattr(order, "trade_terms", "")
    if has_text_risk and trade_terms and not short:
        safe = trade_terms.replace("\n", " ")[:120]
        if len(trade_terms) > 120:
            safe += "…"
        result += f"Умови:\n<code>{escape(safe)}</code>\n"

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

        # 🚀 ДОДАЄМО РОУТЕР І ДИСПЕТЧЕР ДЛЯ КНОПОК
        self._dp = Dispatcher()
        self._router = Router()
        self._dp.include_router(self._router)
        self._dp.include_router(bot_commands.router)
        self._db: MerchantDB | None = None
        self._setup_handlers()

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

    def bind_commands(self, db, account_clients: dict) -> None:
        """Ініціалізує command center з посиланнями на компоненти."""
        bot_commands.setup(db, account_clients)

    async def send_to_user(self, chat_id: int, alert: "SpreadAlert") -> None:
        """
        Multi-user: відправляє алерт в конкретний chat_id.
        chat_id передається явним параметром — без мутації self._chat_id.
        Повністю concurrency-safe: кожен виклик незалежний.
        """
        try:
            await self._send_single(alert, chat_id=chat_id)
        except Exception as e:
            logger.error("send_to_user [%d]: %s", chat_id, e)

    def _setup_handlers(self):
        """Обробник натискань на callback-кнопки."""

        @self._router.callback_query(F.data.startswith("fb:"))
        async def on_feedback(call: CallbackQuery):
            if not self._db:
                return await call.answer("База даних не підключена", show_alert=True)

            try:
                parts = call.data.split(":")
                if len(parts) != 4:
                    return await call.answer("Помилка формату кнопок")

                _, exchange, mid, action = parts

                reason_map = {
                    "triangle": "🚫 ТРЕТІ ОСОБИ (Ручний Blacklist)",
                    "receipt": "🧾 СКАМ З ЧЕКОМ (Ручний Blacklist)",
                    "chat": "📲 ТЯГНЕ В ТГ (Ручний Blacklist)",
                    "fincrime": "🏴‍☠️ ФІНМОН/СХЕМА (Ручний Blacklist)"
                }

                if action not in reason_map:
                    return await call.answer("Невідома дія")

                reason = reason_map[action]

                # Записуємо в глобальний Blacklist
                await self._db.add_to_blacklist(exchange, mid, "Unknown", reason, "manual_tg")
                await call.answer(f"✅ Успіх! Заблоковано: {reason}", show_alert=True)

                # Перекреслюємо повідомлення, щоб візуально закрити тікет
                old_text = call.message.html_text or "Ордер"
                new_text = f"🚨 <b>МЕРЧАНТ ЗАБЛОКОВАНИЙ (Blacklist)!</b>\nПричина: {reason}\nБіржа: {exchange}\n\n<del>{old_text[:3000]}</del>"

                # Прибираємо кнопки
                await call.message.edit_text(new_text, reply_markup=None)

            except Exception as e:
                logger.error("Помилка обробки кнопки: %s", e)
                await call.answer("Помилка БД при блокуванні", show_alert=True)

    async def start(self) -> None:
        # 🚀 СТВОРЮЄМО СИСТЕМНЕ МЕНЮ КНОПКОЮ (Повний список)
        commands =[
            BotCommand(command="start", description="▶️ Запустити мій сканер (Дашборд)"),
            BotCommand(command="stop", description="🛑 Зупинити мій сканер"),
            BotCommand(command="balance", description="💰 Перевірити баланси"),
            BotCommand(command="keys", description="🔑 Підключені API Ключі"),
            BotCommand(command="connect", description="🔌 Підключити біржу"),
            BotCommand(command="disconnect", description="❌ Відключити біржу"),
            BotCommand(command="status", description="📊 Системний статус сканера"),
            BotCommand(command="settings", description="⚙️ Глобальні налаштування"),
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

    async def _send_single(self, alert: SpreadAlert, chat_id: int | None = None) -> None:
        title, silent = _alert_grade(alert.spread_pct)

        b_icon = EXCHANGE_ICONS.get(alert.buy_order.exchange, "◽️")
        s_icon = EXCHANGE_ICONS.get(alert.sell_order.exchange, "◽️")

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

        route_type = getattr(alert, "route_type", "")
        if route_type == "CROSS":
            route_marker = "🔀 CROSS"
        elif route_type == "INTRA":
            route_marker = "🔁 INTRA"
        else:
            route_marker = "📍 ROUTE"

        route_variants = _format_route_variants(getattr(alert, "route_variants", None))
        buy_all = _format_bank_list(getattr(alert, "buy_banks_all", None))
        sell_all = _format_bank_list(getattr(alert, "sell_banks_all", None))
        buy_fit = _format_bank_list(getattr(alert, "buy_banks_fit", None))
        sell_fit = _format_bank_list(getattr(alert, "sell_banks_fit", None))
        buy_warn = _regex_warn_block(alert.buy_order)
        sell_warn = _regex_warn_block(alert.sell_order)

        text = (
            f"{title}\n\n"
            f"💰 Профіт: <b>+{alert.profit_uah:.2f} ₴</b>   "
            f"💼 Угода: <b>{alert.deal_amount_uah:.0f} ₴</b>\n"
            f"🔄 Маршрут: {route_marker} | "
            f"{b_icon}{escape(alert.buy_order.exchange)} → "
            f"{s_icon}{escape(alert.sell_order.exchange)}\n"
            f"⏱ {alert.timestamp.strftime('%H:%M:%S')}\n"
            f"📈 Спред: <b>{alert.spread_pct:.2f}%</b>\n\n"

            f"🏦 Варіанти зв'язки: {route_variants}\n"
            f"🛒 Buy-мерчант банки: {buy_all}\n"
            f"✅ Buy під твій фільтр: {buy_fit}\n"
            f"💸 Sell-мерчант банки: {sell_all}\n"
            f"✅ Sell під твій фільтр: {sell_fit}\n\n"

            f"🛒 <b>КУПУЄМО</b>\n"
            f"Курс: <code>{escape(str(alert.buy_order.price))}</code>\n"
            f"Мерчант: {buy_name}{_verified_badge(alert.buy_order)} "
            f"({alert.buy_order.finish_rate_pct:.1f}% | {alert.buy_order.month_order_count} угод)\n"
            f"Ліміти: <code>{escape(str(alert.buy_order.min_limit))}–{escape(str(alert.buy_order.max_limit))} ₴</code>\n"
            f"{buy_risk if buy_risk else ''}"
            f"{buy_warn if buy_warn else ''}\n"

            f"💸 <b>ПРОДАЄМО</b>\n"
            f"Курс: <code>{escape(str(alert.sell_order.price))}</code>\n"
            f"Мерчант: {sell_name}{_verified_badge(alert.sell_order)} "
            f"({alert.sell_order.finish_rate_pct:.1f}% | {alert.sell_order.month_order_count} угод)\n"
            f"Ліміти: <code>{escape(str(alert.sell_order.min_limit))}–{escape(str(alert.sell_order.max_limit))} ₴</code>\n"
            f"{sell_risk if sell_risk else ''}"
            f"{sell_warn if sell_warn else ''}"
        )

        # 🚀 НОВІ ІНТЕРАКТИВНІ КНОПКИ
        kb = [
            [
                InlineKeyboardButton(text="🛒 Купити", url=alert.buy_order.link or "https://google.com"),
                InlineKeyboardButton(text="💸 Продати", url=alert.sell_order.link or "https://google.com"),
            ]
        ]

        b_mid = alert.buy_order.merchant_id
        if b_mid:
            kb.append([
                InlineKeyboardButton(text="🔴 Buy: 3-ті",
                                     callback_data=f"fb:{alert.buy_order.exchange}:{b_mid}:triangle"),
                InlineKeyboardButton(text="🔴 Чек", callback_data=f"fb:{alert.buy_order.exchange}:{b_mid}:receipt"),
                InlineKeyboardButton(text="🔴 ТГ", callback_data=f"fb:{alert.buy_order.exchange}:{b_mid}:chat"),
            ])

        s_mid = alert.sell_order.merchant_id
        if s_mid:
            kb.append([
                InlineKeyboardButton(text="🔵 Sell: 3-ті",
                                     callback_data=f"fb:{alert.sell_order.exchange}:{s_mid}:triangle"),
                InlineKeyboardButton(text="🔵 Чек", callback_data=f"fb:{alert.sell_order.exchange}:{s_mid}:receipt"),
                InlineKeyboardButton(text="🔵 ТГ", callback_data=f"fb:{alert.sell_order.exchange}:{s_mid}:chat"),
            ])

        keyboard = InlineKeyboardMarkup(inline_keyboard=kb)

        await self._send_with_retry(
            text,
            keyboard=keyboard,
            disable_notification=silent,
            chat_id=chat_id,  # явно передаємо — без підміни self._chat_id
        )

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
            warns = _regex_warn_block(a.buy_order, short=True) + _regex_warn_block(a.sell_order, short=True)
            chips = (risks + warns).strip()

            b_nick = _profile_link(a.buy_order.exchange, a.buy_order.merchant_id, a.buy_order.merchant_name)
            s_nick = _profile_link(a.sell_order.exchange, a.sell_order.merchant_id, a.sell_order.merchant_name)

            lines.append(
                f"{medals[i]} <b>{a.spread_pct:.2f}%</b>  "
                f"<code>+{a.profit_uah:.0f} ₴</code>"
                f"{'  ' + chips if chips else ''}\n"
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
            disable_notification: bool = False,
            chat_id: int | None = None,
    ) -> None:
        target_chat = chat_id or self._chat_id
        for attempt in range(max_attempts):
            try:
                await self._bot.send_message(
                    chat_id=target_chat,
                    text=text,
                    reply_markup=keyboard,
                    disable_web_page_preview=True,
                    disable_notification=disable_notification,
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
        chunks, current = ""
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