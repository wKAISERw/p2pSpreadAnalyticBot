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
from core.engine.network_fee_engine import NetworkFeeEngine
from core.analytics.merchant_profile import build_profile_url

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
    buy_bank: str
    sell_bank: str
    buy_banks_all: list[str] | None = None
    sell_banks_all: list[str] | None = None
    buy_banks_fit: list[str] | None = None
    sell_banks_fit: list[str] | None = None
    route_variants: list[str] | None = None
    route_type: str = "UNKNOWN"
    # 🚀 ДОДАНО ПОЛЯ ДЛЯ LLM
    buy_rec: str = "PENDING"
    sell_rec: str = "PENDING"
    buy_reason: str = ""
    sell_reason: str = ""
    timestamp: datetime = None

    def __post_init__(self):
        if self.timestamp is None:
            self.timestamp = datetime.now()

# 🚀 ДОДАНО ФУНКЦІЮ БЕЙДЖІВ
def rec_badge(rec: str) -> str:
    return {
        "APPROVE":     "✅",
        "CONDITIONAL": "⚡",
        "REJECT":      "🚫",
        "PENDING":     "🔍",
    }.get((rec or "PENDING").upper(), "🔍")


REC_LABELS = {
    "APPROVE":     "✅ БЕЗПЕЧНО",
    "CONDITIONAL": "⚡ З ОБЕРЕЖНІСТЮ",
    "REJECT":      "🚫 НЕ ТОРГУВАТИ",
    "PENDING":     "🔍 AI АНАЛІЗУЄ…",
}


def _llm_verdict_block(label: str, rec: str, reason: str) -> str:
    """Форматує вердикт AI для buy/sell мерчанта з короткою вижимкою."""
    rec_upper = (rec or "PENDING").upper()
    rec_text = REC_LABELS.get(rec_upper, f"🔍 {rec_upper}")
    line = f"🧠 <b>{label}:</b> {rec_text}\n"
    # Показуємо стислу вижимку від ЛЛМ (reason) — умови + відгуки
    if reason and rec_upper != "PENDING":
        safe_reason = escape(str(reason).strip()[:200])
        line += f"<blockquote expandable>💬 {safe_reason}</blockquote>\n"
    return line

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

    url = build_profile_url(exchange, merchant_id, merchant_name)
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

    # ── Бейджі по типу ризику ──────────────────────────────────────────
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

    # Людські назви типів ризику (для LLM_PENDING / REGEX_WEAK)
    RISK_TYPE_LABELS = {
        "TRIANGLE": "ТРИКУТНИК", "CASINO": "КАЗИНО", "CHAT_FIRST": "ЧАТ",
        "EXTERNAL_LINK": "ЗОВН.КОНТАКТ", "FINCRIME": "ФІНМОН",
        "CHARGEBACK": "РЕФАНД", "ANONYMOUS": "АНОНІМ", "MIDDLEMAN": "ПОСЕРЕДНИК",
        "SUSPICIOUS_BIZ": "ПІДОЗР.БІЗ", "APPEAL_PRESSURE": "ТИСК",
        "NO_COMMENTS": "БЕЗ КОМЕНТІВ", "THIRD_PARTY_HINT": "3-ТІ ОСОБИ",
        "BEHAVIOR": "ПОВЕДІНКА", "SUSPICIOUS": "ПІДОЗРА", "BOT_API": "БОТ",
        "EXACT_LIMITS": "ФІКС.ЛІМІТИ", "NARROW_SPREAD": "ВУЗЬКИЙ ДІАПАЗОН",
        "PROACTIVE": "СКРИНІНГ",
    }

    # Категорії, для яких обов'язково показувати уривок умов
    show_text_cats = {"TRIANGLE", "CASINO", "CHAT_FIRST", "EXTERNAL_LINK", "FINCRIME",
                      "CHARGEBACK", "NO_COMMENTS", "MIDDLEMAN"}

    flags = [f.strip() for f in flag.split(",") if f.strip()]
    lines = []
    reasons = []
    detected_risk_types: set[str] = set()   # для показу trade_terms

    for f in flags:
        # ── Відгуки (NEEDS_LLM:BADREVIEWS: ПЕРЕД BADREVIEWS: !) ────────
        if f.startswith("NEEDS_LLM:BADREVIEWS:"):
            lines.append("🗣👎 ПОГАНІ ВІДГУКИ → AI ПЕРЕВІРКА\n" if not short else "🗣")
            reason_text = f[len("NEEDS_LLM:BADREVIEWS:"):]
            if reason_text:
                reasons.append(reason_text)
            detected_risk_types.add("BADREVIEWS")
            continue

        if f.startswith("BADREVIEWS_TEXTS:"):
            lines.append("🗣 ПІДОЗРІЛІ ВІДГУКИ (тексти)\n" if not short else "🗣")
            reason_text = f[len("BADREVIEWS_TEXTS:"):]
            if reason_text:
                reasons.append(reason_text)
            continue

        if f.startswith("BADREVIEWS:"):
            lines.append(badges["BADREVIEWS"])
            reasons.append(f[len("BADREVIEWS:"):])
            continue

        # ── Поведінкові маркери (Deep Research) ────────────────────────
        if f.startswith("API_REPLENISH:"):
            count = f.split(":", 1)[1] if ":" in f else ""
            lines.append(f"🤖 АВТОПОПОВНЕННЯ БОТОМ ({count} цикл.)\n" if not short else "🤖")
            continue

        if f.startswith("STATIC_DROP:"):
            count = f.split(":", 1)[1] if ":" in f else ""
            lines.append(f"📏 СТАТИЧНИЙ ДРОП ({count} цикл.)\n" if not short else "📏")
            continue

        if f.startswith("VELOCITY_SPIKE:"):
            vel = f.split(":", 1)[1] if ":" in f else ""
            lines.append(f"⚡ АНОМАЛЬНА АКТИВНІСТЬ ({vel})\n" if not short else "⚡")
            continue

        if f.startswith("FLICKER_RELIST:"):
            count = f.split(":", 1)[1] if ":" in f else ""
            lines.append(f"🔄 РІЛІСТИНГ ({count} раз)\n" if not short else "🔄")
            continue

        if f == "EXACT_LIMITS":
            lines.append("🎯 ФІКСОВАНА СУМА (min=max)\n" if not short else "🎯")
            continue

        if f == "NARROW_SPREAD":
            lines.append("📏 ВУЗЬКИЙ ДІАПАЗОН\n" if not short else "📏")
            continue

        if f.startswith("BEHAVIOR_BOTLIKE:"):
            lines.append("🤖 ПІДОЗРІЛА ПОВЕДІНКА (БОТ)\n" if not short else "🤖")
            continue

        if f.startswith("CROSS_EXCHANGE_BOT:"):
            parts = f.split(":", 2)
            ex_names = parts[2].replace("CLONES:", "") if len(parts) > 2 else ""
            lines.append(f"👯 КЛОН НА БІРЖАХ: {ex_names}\n" if not short else "👯")
            continue

        # ── Синергії (раніше BLOCK:SYNERGY → тепер SYNERGY:) ──────────
        if f.startswith("SYNERGY:"):
            detail = f[len("SYNERGY:"):]
            lines.append(f"🔗 КОМБО: {escape(detail[:60])}\n" if not short else "🔗")
            continue

        # ── Жорсткий блок (blacklist, cached BLOCK verdict) ───────────
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
            detected_risk_types.add(risk)
            continue

        # ── LLM результати ────────────────────────────────────────────
        if f.startswith("LLM_SUSPICIOUS:"):
            parts = f.split(":", 2)
            risk_type = parts[1] if len(parts) > 1 else ""
            label = RISK_TYPE_LABELS.get(risk_type, "")
            if label and not short:
                lines.append(f"🧠 AI ПІДОЗРА: {label}\n")
            else:
                lines.append(badges["LLM_SUSPICIOUS"])
            if len(parts) > 2 and parts[2]:
                reasons.append(parts[2])
            detected_risk_types.add(risk_type)
            continue

        if f.startswith("LLM_UNKNOWN:"):
            parts = f.split(":", 2)
            lines.append(badges["LLM_UNKNOWN"])
            if len(parts) > 2 and parts[2]:
                reasons.append(parts[2])
            continue

        # ── Regex (м'який сигнал — тепер з типом ризику) ──────────────
        if f.startswith("REGEX_WEAK:"):
            parts = f.split(":", 2)
            risk_type = parts[1] if len(parts) > 1 else ""
            label = RISK_TYPE_LABELS.get(risk_type, risk_type)
            if label and label != risk_type and not short:
                lines.append(f"🧩 REGEX: {label}\n")
            else:
                lines.append(badges["REGEX_WEAK"])
            if len(parts) > 2 and parts[2]:
                reasons.append(parts[2])
            detected_risk_types.add(risk_type)
            continue

        # ── LLM Pending (тепер з типом підозри) ──────────────────────
        if f.startswith("LLM_PENDING:"):
            parts = f.split(":", 2)
            risk_type = parts[1] if len(parts) > 1 else ""
            label = RISK_TYPE_LABELS.get(risk_type, "")
            if label and not short:
                lines.append(f"⏳ AI ПЕРЕВІРЯЄ: {label}\n")
            else:
                lines.append(badges["LLM_PENDING"])
            detected_risk_types.add(risk_type)
            continue

        # ── Fallback: bare flag in badges dict ────────────────────────
        if f in badges:
            lines.append(badges[f])

    if not lines:
        return ""

    result = "".join(lines)

    # ── Спойлер-блок: деталі під тапом (reasons + стата + відгуки + умови) ──
    if not short:
        spoiler_parts: list[str] = []

        # 1. LLM reason (причина вердикту) — повна, без обрізання (в expandable blockquote)
        if reasons:
            uniq = []
            seen = set()
            for r in reasons:
                r = (r or "").strip()
                if r and r not in seen:
                    seen.add(r)
                    uniq.append(r)  # без обрізання — знаходиться в expandable blockquote
            for r in uniq[:2]:
                spoiler_parts.append(f"💬 {escape(r)}")

        # 2. Умови мерчанта (якщо ризиковий тип)
        trade_terms = getattr(order, "trade_terms", "")
        if detected_risk_types & show_text_cats and trade_terms:
            safe = trade_terms.replace("\n", " ")[:100]
            if len(trade_terms) > 100:
                safe += "…"
            spoiler_parts.append(f"📝 {escape(safe)}")

        if spoiler_parts:
            spoiler_text = "\n".join(spoiler_parts)
            result += f"<blockquote expandable>{spoiler_text}</blockquote>\n"

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

    def bind_commands(self, db: MerchantDB, account_clients: dict, trade_worker=None, single_leg_executor=None) -> None:
        """Оновлює змінні модуля. Router вже підключений в __init__."""
        bot_commands.setup(db, account_clients, trade_worker, notifier=self, single_leg_executor=single_leg_executor)
        # ← більше нічого не треба

    async def _get_user_show_llm_summary(self, chat_id: int) -> bool:
        """Перевіряє чи юзер хоче бачити вижимку AI в алерті."""
        if not self._db:
            return True  # default: показувати
        try:
            conn = getattr(self._db, "db", None) or getattr(self._db, "_db", self._db)
            async with conn.execute(
                "SELECT COALESCE(show_llm_summary, 1) FROM scanner_users WHERE telegram_chat_id=?",
                (chat_id,)
            ) as cur:
                row = await cur.fetchone()
            return bool(row[0]) if row else True
        except Exception:
            return True

    async def send_to_user(self, chat_id: int, alert: "SpreadAlert") -> None:
        """
        Multi-user: відправляє алерт в конкретний chat_id.
        chat_id передається явним параметром — без мутації self._chat_id.
        Повністю concurrency-safe: кожен виклик незалежний.
        """
        try:
            show_llm = await self._get_user_show_llm_summary(chat_id)
            await self._send_single(alert, chat_id=chat_id, show_llm_summary=show_llm)
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
            BotCommand(command="active", description="📡 Активні спреди зараз"),
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

    async def _send_single(self, alert: SpreadAlert, chat_id: int | None = None, show_llm_summary: bool = True) -> None:
        # 🔄 Refresh LLM verdicts from DB (LLM може завершитись після створення алерту)
        if self._db:
            try:
                b_rec, _, b_reason = await self._db.get_trade_recommendation_full(
                    alert.buy_order.exchange, alert.buy_order.merchant_id
                )
                s_rec, _, s_reason = await self._db.get_trade_recommendation_full(
                    alert.sell_order.exchange, alert.sell_order.merchant_id
                )
                alert.buy_rec = b_rec
                alert.sell_rec = s_rec
                alert.buy_reason = b_reason
                alert.sell_reason = s_reason
            except Exception:
                pass  # fallback: використовуємо значення з алерту

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
        buy_name = escape(alert.buy_order.merchant_name or alert.buy_order.merchant_id or "Unknown")
        buy_name_str = f"{rec_badge(alert.buy_rec)} {buy_name}{_verified_badge(alert.buy_order)}"
        sell_name = escape(alert.sell_order.merchant_name or alert.sell_order.merchant_id or "Unknown")
        sell_name_str = f"{rec_badge(alert.sell_rec)} {sell_name}{_verified_badge(alert.sell_order)}"

        # 🧠 LLM Verdict блоки (повна вижимка або тільки бейдж)
        if show_llm_summary:
            buy_llm = _llm_verdict_block("Buy", alert.buy_rec, alert.buy_reason)
            sell_llm = _llm_verdict_block("Sell", alert.sell_rec, alert.sell_reason)
        else:
            # Тільки бейдж без expandable reason
            buy_llm = _llm_verdict_block("Buy", alert.buy_rec, "")
            sell_llm = _llm_verdict_block("Sell", alert.sell_rec, "")

        # 🚀 Блок D: Мережі переказу
        buy_ex = alert.buy_order.exchange
        sell_ex = alert.sell_order.exchange
        if buy_ex != sell_ex:
            net_block = NetworkFeeEngine.format_for_alert(buy_ex, sell_ex)
            net_block_html = "\n" + escape(net_block) + "\n"
        else:
            net_block_html = ""

        text = (
            f"{title}\n\n"
            f"💰 Профіт: <b>+{alert.profit_uah:.2f} ₴</b>   "
            f"💼 Угода: <b>{alert.deal_amount_uah:.0f} ₴</b>\n"
            f"🔄 Маршрут: {route_marker} | "
            f"{b_icon}{escape(alert.buy_order.exchange)} → "
            f"{s_icon}{escape(alert.sell_order.exchange)}\n"
            f"⏱ {alert.timestamp.strftime('%H:%M:%S')}\n"
            f"📈 Спред: <b>{alert.spread_pct:.2f}%</b>"
            f"{net_block_html}\n"

            f"<blockquote expandable>"
            f"🏦 Варіанти зв'язки: {route_variants}\n"
            f"🛒 Buy банки: {buy_all}\n"
            f"✅ Buy фільтр: {buy_fit}\n"
            f"💸 Sell банки: {sell_all}\n"
            f"✅ Sell фільтр: {sell_fit}"
            f"</blockquote>\n"

            f"🛒 <b>КУПУЄМО</b>\n"
            f"Курс: <code>{escape(str(alert.buy_order.price))}</code>\n"
            f"Мерчант: {buy_name_str} "
            f"({alert.buy_order.finish_rate_pct:.1f}% | {alert.buy_order.month_order_count} угод)\n"
            f"Ліміти: <code>{escape(str(alert.buy_order.min_limit))}–{escape(str(alert.buy_order.max_limit))} ₴</code>\n"
            f"{buy_risk if buy_risk else ''}"
            f"{buy_warn if buy_warn else ''}"
            f"\n{buy_llm}\n"

            f"💸 <b>ПРОДАЄМО</b>\n"
            f"Курс: <code>{escape(str(alert.sell_order.price))}</code>\n"
            f"Мерчант: {sell_name_str} "
            f"({alert.sell_order.finish_rate_pct:.1f}% | {alert.sell_order.month_order_count} угод)\n"
            f"Ліміти: <code>{escape(str(alert.sell_order.min_limit))}–{escape(str(alert.sell_order.max_limit))} ₴</code>\n"
            f"{sell_risk if sell_risk else ''}"
            f"{sell_warn if sell_warn else ''}"
            f"\n{sell_llm}"
        )

        # 🚀 НОВІ ІНТЕРАКТИВНІ КНОПКИ
        kb = []

        # 🚀 Блок A: Single-Leg кнопки "Купити" / "Продати"
        b_ad = getattr(alert.buy_order, "ad_id", getattr(alert.buy_order, "order_id", ""))
        s_ad = getattr(alert.sell_order, "ad_id", getattr(alert.sell_order, "order_id", ""))

        if b_ad and s_ad and alert.buy_rec != "REJECT" and alert.sell_rec != "REJECT":
            cache_key = f"{b_ad[:12]}_{s_ad[:12]}"

            import time
            # 🚀 ФІКС: Зберігаємо алерт разом із міткою часу для TTL
            bot_commands._spread_cache[cache_key] = (alert, time.time())

            kb.append([
                InlineKeyboardButton(text=f"⚡ Авто-Трейд (T→T) {alert.spread_pct:.2f}%",
                                     callback_data=f"trade:tt:{cache_key}")
            ])

        # Кнопки Single-Leg (незалежні від пари)
        single_leg_row = []
        if b_ad and alert.buy_rec != "REJECT":
            buy_bank = alert.buy_bank or ""
            # Telegram callback_data max 64 bytes — скорочуємо
            sl_buy_key = f"{b_ad[:10]}|{alert.buy_order.exchange[:3]}|{buy_bank[:4]}"
            bot_commands._single_leg_cache[f"b:{sl_buy_key}"] = {
                "ad_id": str(b_ad), "exchange": alert.buy_order.exchange,
                "price": float(alert.buy_order.price),
                "merchant_id": alert.buy_order.merchant_id,
                "min_limit": float(alert.buy_order.min_limit),
                "max_limit": float(alert.buy_order.max_limit),
                "bank": buy_bank, "ts": time.time(),
            }
            single_leg_row.append(
                InlineKeyboardButton(
                    text=f"🛒 Купити ({alert.buy_order.exchange})",
                    callback_data=f"sl:b:{sl_buy_key}"
                )
            )
        if s_ad and alert.sell_rec != "REJECT":
            sell_bank = alert.sell_bank or ""
            sl_sell_key = f"{s_ad[:10]}|{alert.sell_order.exchange[:3]}|{sell_bank[:4]}"
            bot_commands._single_leg_cache[f"s:{sl_sell_key}"] = {
                "ad_id": str(s_ad), "exchange": alert.sell_order.exchange,
                "price": float(alert.sell_order.price),
                "merchant_id": alert.sell_order.merchant_id,
                "min_limit": float(alert.sell_order.min_limit),
                "max_limit": float(alert.sell_order.max_limit),
                "bank": sell_bank, "ts": time.time(),
            }
            single_leg_row.append(
                InlineKeyboardButton(
                    text=f"💸 Продати ({alert.sell_order.exchange})",
                    callback_data=f"sl:s:{sl_sell_key}"
                )
            )
        if single_leg_row:
            kb.append(single_leg_row)

        # URL-кнопки (відкрити на біржі)
        url_row = []
        buy_url = getattr(alert.buy_order, "link", "") or build_profile_url(
            alert.buy_order.exchange, alert.buy_order.merchant_id
        )
        sell_url = getattr(alert.sell_order, "link", "") or build_profile_url(
            alert.sell_order.exchange, alert.sell_order.merchant_id
        )
        if buy_url:
            url_row.append(InlineKeyboardButton(text="🔗 Buy на біржі", url=buy_url))
        if sell_url:
            url_row.append(InlineKeyboardButton(text="🔗 Sell на біржі", url=sell_url))
        if url_row:
            kb.append(url_row)

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

        # Розбиваємо якщо повідомлення > 4096 (Telegram ліміт)
        # Keyboard тільки на першому чанку
        chunks = self._split_message(text)
        for i, chunk in enumerate(chunks):
            await self._send_with_retry(
                chunk,
                keyboard=keyboard if i == 0 else None,
                disable_notification=silent,
                chat_id=chat_id,
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
                logger.debug("✅ TG sent → chat_id=%s (len=%d)", target_chat, len(text))
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