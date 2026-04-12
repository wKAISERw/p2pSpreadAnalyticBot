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
    buy_terms_summary: str = ""
    sell_terms_summary: str = ""
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
        "RECHECKING":  "🔄",
    }.get((rec or "PENDING").upper(), "🔍")


REC_LABELS = {
    "APPROVE":     "✅ БЕЗПЕЧНО",
    "CONDITIONAL": "⚡ З ОБЕРЕЖНІСТЮ",
    "REJECT":      "🚫 НЕ ТОРГУВАТИ",
    "PENDING":     "🔍 AI АНАЛІЗУЄ…",
    "RECHECKING":  "🔄 AI ПЕРЕПРОВІРЯЄ…",
}


def _llm_verdict_block(
    label: str, rec: str, reason: str,
    terms_summary: str = "",        # залишаємо для сумісності — тепер у _terms_block
    show_ai_logic: bool = True,
    show_ai_terms_summary: bool = True,  # залишаємо для сумісності
) -> str:
    """Форматує вердикт AI для buy/sell мерчанта (лише вердикт + логіка)."""
    rec_upper = (rec or "PENDING").upper()
    rec_text = REC_LABELS.get(rec_upper, f"🔍 {rec_upper}")
    line = f"🧠 <b>{label}:</b> {rec_text}\n"

    # 🔘 Логіка AI (reason під спойлером)
    if show_ai_logic and reason and rec_upper not in ("PENDING",):
        safe_reason = escape(str(reason).strip()[:300])
        prefix = "💬 " if rec_upper != "RECHECKING" else "💬 (попередній аналіз) "
        line += f"<blockquote expandable>{prefix}{safe_reason}</blockquote>\n"
    return line


def _terms_block(
    terms_raw: str,
    terms_summary: str = "",
    show_ai_terms_summary: bool = True,
    show_full_terms: bool = True,
) -> str:
    """
    Розділ «📋 Умови» між ризиками та вердиктом LLM.
    Весь контент — в одному <blockquote expandable>.

    Конфігурація (що потрапляє всередину blockquote):
      🔘 show_ai_terms_summary: AI вижимка 1-2 речення (коротко, основне)
      🔘 show_full_terms:       повний raw текст умов мерчанта

    Комбінації:
      обидва True  → вижимка + роздільник + повний текст в одному спойлері
      тільки вижимка → тільки AI summary в спойлері
      тільки повні   → тільки raw текст в спойлері
      обидва False   → розділ «Умови» не показується взагалі
    """
    has_summary = show_ai_terms_summary and bool(terms_summary and str(terms_summary).strip())
    has_full    = show_full_terms and bool(terms_raw and str(terms_raw).strip())
    if not has_summary and not has_full:
        return ""

    # Заголовок завжди один, але кожен блок — окремий expandable blockquote
    block = "📋 <b>Умови</b>\n"
    if has_summary:
        safe_s = escape(str(terms_summary).strip()[:250])
        block += f"<blockquote expandable>🤖 {safe_s}</blockquote>\n"
    if has_full:
        safe_t = escape(str(terms_raw).strip()[:500])
        block += f"<blockquote expandable>📝 {safe_t}</blockquote>\n"
    return block

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
    """Генерує клікабельне ім'я мерчанта (синій лінк у Telegram → профіль на біржі)."""
    safe_name = escape(str(merchant_name or "Unknown"))
    if not merchant_id:
        return safe_name

    web_url = build_profile_url(exchange, merchant_id, merchant_name)
    if web_url:
        return f'<a href="{escape(web_url, quote=True)}">{safe_name}</a>'
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
        "PROACTIVE": "СКРИНІНГ", "RECHECK": "ПЕРЕПРОВІРКА",
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

    # ── Спойлер-блок: тільки уривок умов (якщо ризиковий тип) ──
    if not short:
        spoiler_parts: list[str] = []

        # Умови мерчанта (якщо ризиковий тип — короткий уривок прямо під ризиком)
        # Повний текст і AI-вижимка — в окремому розділі _terms_block() нижче в повідомленні
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

    def bind_commands(self, db: MerchantDB, account_clients: dict, trade_worker=None, single_leg_executor=None, maker_monitor=None) -> None:
        """Оновлює змінні модуля. Router вже підключений в __init__."""
        bot_commands.setup(db, account_clients, trade_worker, notifier=self, single_leg_executor=single_leg_executor, maker_monitor=maker_monitor)
        # Зберігаємо ПРЯМІ ПОСИЛАННЯ на кеші з commands — щоб send_taker_to_user
        # писав у той самий dict, з якого on_taker_take() читає.
        self._taker_cache = bot_commands._taker_order_cache

    async def _get_display_settings(self, chat_id: int) -> dict:
        """Повертає per-user налаштування виводу повідомлень."""
        if not self._db:
            return {
                "show_ai_terms_summary": True,
                "show_full_terms": True,
                "show_ai_logic": True,
                "show_bank_details": True,
                "show_llm_summary": True,
            }
        try:
            return await self._db.get_user_display_settings(chat_id)
        except Exception:
            return {
                "show_ai_terms_summary": True,
                "show_full_terms": True,
                "show_ai_logic": True,
                "show_bank_details": True,
                "show_llm_summary": True,
            }

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

    async def send_maker_buy_suggestion(self, chat_id: int, advice: dict) -> None:
        """
        Надсилає рекомендацію оптимальної ціни купівлі (MAKER_BUY).
        advice: dict від PriceAdvisor.suggest_buy_price() + можливий buy_book_top
        """
        from core.engine.price_advisor import PriceAdvisor
        buy_book_top = advice.get("buy_book_top", 0)
        book_block = ""
        if buy_book_top > 0:
            diff = advice["max_buy_price"] - buy_book_top
            if diff > 0:
                book_block = (
                    f"\n📊 <b>Стакан:</b> топ buy = <code>{buy_book_top:.2f}</code> ₴\n"
                    f"✅ Можна поставити вище на <code>+{diff:.4f}</code> ₴ і залишитись в плюсі"
                )
            else:
                book_block = (
                    f"\n📊 <b>Стакан:</b> топ buy = <code>{buy_book_top:.2f}</code> ₴\n"
                    f"⚠️ Конкуренція висока — топ buy вже вище рекомендації на <code>{abs(diff):.4f}</code> ₴"
                )

        text = (
            "📥 <b>MAKER BUY: Аналіз ринку</b>\n\n"
            f"{PriceAdvisor.format_buy_suggestion(advice)}"
            f"{book_block}\n\n"
            f"⏱ {datetime.now().strftime('%H:%M:%S')}\n"
            "<i>💡 Натисни «Створити оголошення» щоб виставити ad з рекомендованою ціною.</i>"
        )
        kb = [[InlineKeyboardButton(
            text="📢 Створити оголошення",
            callback_data="ad:create",
        )]]
        try:
            await self._send_with_retry(
                text,
                keyboard=InlineKeyboardMarkup(inline_keyboard=kb),
                disable_notification=False,
                chat_id=chat_id,
            )
        except Exception as e:
            logger.error("send_maker_buy_suggestion [%d]: %s", chat_id, e)

    async def send_maker_sell_update(self, chat_id: int, advice: dict) -> None:
        """
        Надсилає оновлення рекомендованої ціни продажу (MAKER_SELL).
        advice: dict від PriceAdvisor.suggest_sell_price() + можливі sell_book_top, book_vs_min
        """
        from core.engine.price_advisor import PriceAdvisor
        book_top = advice.get("sell_book_top", 0)
        book_vs_min = advice.get("book_vs_min", 0)

        book_block = ""
        if book_top > 0:
            if book_vs_min > 0:
                book_block = (
                    f"\n📊 <b>Стакан:</b> топ sell = <code>{book_top:.2f}</code> ₴\n"
                    f"✅ Різниця з мін. ціною: <code>+{book_vs_min:.4f}</code> ₴ (вигідно!)"
                )
            else:
                book_block = (
                    f"\n📊 <b>Стакан:</b> топ sell = <code>{book_top:.2f}</code> ₴\n"
                    f"⚠️ Різниця з мін. ціною: <code>{book_vs_min:.4f}</code> ₴ (невигідно)"
                )

        text = (
            "📤 <b>MAKER SELL: Оновлення ціни</b>\n\n"
            f"{PriceAdvisor.format_sell_suggestion(advice)}"
            f"{book_block}\n\n"
            f"⏱ {datetime.now().strftime('%H:%M:%S')}\n"
            "<i>💡 Порада оновлена на основі поточного стакану.</i>"
        )
        kb = [[InlineKeyboardButton(
            text="📢 Створити / оновити оголошення",
            callback_data="ad:create",
        )]]
        try:
            await self._send_with_retry(
                text,
                keyboard=InlineKeyboardMarkup(inline_keyboard=kb),
                disable_notification=True,
                chat_id=chat_id,
            )
        except Exception as e:
            logger.error("send_maker_sell_update [%d]: %s", chat_id, e)

    async def send_taker_to_user(
        self, chat_id: int, orders: list[Order], mode: str,
    ) -> None:
        """
        Відправляє тейкер-алерти юзеру — КОЖЕН ордер як окреме повідомлення
        в тому ж дизайні що й spread-алерт, але з однією стороною.
        mode: TAKER_BUY або TAKER_SELL
        """
        if not orders:
            return
        ds = await self._get_display_settings(chat_id)
        for i, order in enumerate(orders[:10]):
            try:
                await self._send_taker_single(order, mode, chat_id=chat_id, display_settings=ds)
                if i < len(orders) - 1:
                    await asyncio.sleep(0.8)
            except Exception as e:
                logger.error("send_taker_to_user [%d] order #%d: %s", chat_id, i, e)

    async def _send_taker_single(
        self, order: Order, mode: str,
        chat_id: int | None = None,
        display_settings: dict | None = None,
    ) -> None:
        """
        Відправляє ОДИН тейкер-ордер як повноцінне повідомлення
        у стилі spread-алерту (ризики, LLM вердикт, умови, банки, кнопки).
        """
        import time

        ds = display_settings or {
            "show_ai_terms_summary": True, "show_full_terms": True,
            "show_ai_logic": True, "show_bank_details": True, "show_llm_summary": True,
        }

        is_buy = mode == "TAKER_BUY"
        side_title = "🛒 <b>ТЕЙКЕР: КУПІВЛЯ</b>" if is_buy else "💸 <b>ТЕЙКЕР: ПРОДАЖ</b>"
        side_label = "КУПУЄМО" if is_buy else "ПРОДАЄМО"
        side_icon = "🛒" if is_buy else "💸"

        # ── Refresh LLM verdict from DB ──
        llm_rec = "PENDING"
        llm_reason = ""
        terms_summary = ""
        rev_analysis = ""
        if self._db:
            try:
                rec, _, reason, t_sum, rev_analyz = await self._db.get_trade_recommendation_full(
                    order.exchange, order.merchant_id,
                )
                llm_rec = rec
                llm_reason = reason
                terms_summary = t_sum
                rev_analysis = rev_analyz
            except Exception:
                pass

        icon = EXCHANGE_ICONS.get(order.exchange, "◽️")
        merchant_link = _profile_link(order.exchange, order.merchant_id, order.merchant_name)
        name_str = f"{rec_badge(llm_rec)} {merchant_link}{_verified_badge(order)}"

        risk_block = _risk_badge(order)
        warn_block = _regex_warn_block(order)

        banks_all = _format_bank_list(order.bank_codes)

        now = datetime.now()
        silent = False  # тейкер — завжди зі звуком

        text = (
            f"{side_title}\n\n"
            f"⏱ {now.strftime('%H:%M:%S')} | {icon} <b>{escape(order.exchange)}</b>\n\n"
        )

        # ── Деталі банків ──
        if ds.get("show_bank_details", True):
            text += (
                f"<blockquote expandable>"
                f"🏦 Банки: {banks_all}"
                f"</blockquote>\n"
            )

        # ── Основний блок ордера ──
        terms_raw = getattr(order, "trade_terms", "") or ""
        text += (
            f"{side_icon} <b>{side_label}</b>\n"
            f"Курс: <code>{escape(str(order.price))}</code>\n"
            f"Мерчант: {name_str} "
            f"({order.finish_rate_pct:.1f}% | {order.month_order_count} угод)\n"
            f"Ліміти: <code>{escape(str(order.min_limit))}–{escape(str(order.max_limit))} ₴</code>\n"
            f"{risk_block if risk_block else ''}"
            f"{warn_block if warn_block else ''}"
        )

        # ── Блок умов ──
        terms_blk = _terms_block(
            terms_raw,
            terms_summary=terms_summary,
            show_ai_terms_summary=ds.get("show_ai_terms_summary", True),
            show_full_terms=ds.get("show_full_terms", True),
        )
        if terms_blk:
            text += terms_blk

        # ── LLM Verdict ──
        if ds.get("show_llm_summary", True):
            llm_block = _llm_verdict_block(
                "Buy" if is_buy else "Sell", llm_rec, llm_reason,
                terms_summary=terms_summary,
                show_ai_logic=ds.get("show_ai_logic", True),
                show_ai_terms_summary=ds.get("show_ai_terms_summary", True),
                reviews_analysis=rev_analysis
            )
        else:
            llm_block = _llm_verdict_block(
                "Buy" if is_buy else "Sell", llm_rec, "",
                show_ai_logic=False, show_ai_terms_summary=False,
            )
        text += f"\n{llm_block}"

        # ── Кнопки ──
        kb: list[list[InlineKeyboardButton]] = []

        ad_id = getattr(order, "ad_id", getattr(order, "order_id", order.id))
        if ad_id and llm_rec != "REJECT":
            # Кнопка ⚡ Взяти
            if hasattr(self, "_taker_cache"):
                cache_key = f"tk_{ad_id[:12]}_{int(time.time()) % 10000}"
                self._taker_cache.set(cache_key, {
                    "ad_id": str(ad_id),
                    "exchange": order.exchange,
                    "price": float(order.price),
                    "merchant_id": order.merchant_id,
                    "min_limit": float(order.min_limit),
                    "max_limit": float(order.max_limit),
                    "bank": (order.bank_codes[0] if order.bank_codes else ""),
                    "direction": "b" if is_buy else "s",
                    "action": "BUY" if is_buy else "SELL",
                    "ts": time.time(),
                })
                action_label = "Купити" if is_buy else "Продати"
                kb.append([InlineKeyboardButton(
                    text=f"⚡ {action_label} ({order.exchange} {order.price})",
                    callback_data=f"taker:take:{cache_key}",
                )])

            # Single-Leg кнопка
            bank_code = (order.bank_codes[0] if order.bank_codes else "")
            direction = "b" if is_buy else "s"
            sl_key = f"{str(ad_id)[:10]}|{order.exchange[:3]}|{bank_code[:4]}"
            bot_commands._single_leg_cache[f"{direction}:{sl_key}"] = {
                "ad_id": str(ad_id), "exchange": order.exchange,
                "price": float(order.price),
                "merchant_id": order.merchant_id,
                "min_limit": float(order.min_limit),
                "max_limit": float(order.max_limit),
                "bank": bank_code, "ts": time.time(),
            }
            sl_label = "🛒 Купити" if is_buy else "💸 Продати"
            kb.append([InlineKeyboardButton(
                text=f"{sl_label} ({order.exchange})",
                callback_data=f"sl:{direction}:{sl_key}",
            )])

        # URL-кнопка
        url = getattr(order, "link", "") or build_profile_url(
            order.exchange, order.merchant_id
        )
        if url:
            kb.append([InlineKeyboardButton(text="🔗 На біржі", url=url)])

        # Blacklist кнопки
        mid = order.merchant_id
        if mid:
            prefix = "🔴 Buy" if is_buy else "🔵 Sell"
            kb.append([
                InlineKeyboardButton(text=f"{prefix}: 3-ті",
                                     callback_data=f"fb:{order.exchange}:{mid}:triangle"),
                InlineKeyboardButton(text="Чек", callback_data=f"fb:{order.exchange}:{mid}:receipt"),
                InlineKeyboardButton(text="ТГ", callback_data=f"fb:{order.exchange}:{mid}:chat"),
            ])

        keyboard = InlineKeyboardMarkup(inline_keyboard=kb)

        # Розбиваємо якщо > 4096
        chunks = self._split_message(text)
        for i, chunk in enumerate(chunks):
            await self._send_with_retry(
                chunk,
                keyboard=keyboard if i == 0 else None,
                disable_notification=silent,
                chat_id=chat_id,
            )

    async def send_maker_order_alert(
        self,
        chat_id: int,
        order_info: dict,
        counterparty: "Order",
        exchange: str,
    ) -> None:
        """
        Відправляє TG-нотифікацію про вхідний ордер на мейкер-оголошення.

        order_info: enriched dict з MakerAdMonitor
        counterparty: synthetic Order з даними контрагента (+ risk_flag, composite_score)
        """
        try:
            icon = EXCHANGE_ICONS.get(exchange, "◽️")
            name = _profile_link(exchange, counterparty.merchant_id, counterparty.merchant_name)
            verified = _verified_badge(counterparty)
            risk = _risk_badge(counterparty)

            # LLM вердикт
            rec = order_info.get("rec", "PENDING")
            reason = order_info.get("reason", "")
            rec_text = REC_LABELS.get((rec or "PENDING").upper(), f"🔍 {rec}")
            badge = rec_badge(rec)

            order_id = order_info.get("order_id", "")
            price = order_info.get("price", 0)
            amount_usdt = order_info.get("amount_usdt", 0)
            total_fiat = order_info.get("total_fiat", 0)
            item_id = order_info.get("item_id", "")

            text = (
                f"🔔 <b>НОВЕ ЗАМОВЛЕННЯ!</b>\n\n"
                f"{icon} <b>{escape(exchange)}</b> | "
                f"Оголошення: <code>{escape(str(item_id)[:20])}</code>\n\n"
                f"👤 <b>Контрагент:</b> {name}{verified}\n"
                f"📊 {counterparty.finish_rate_pct:.1f}% | "
                f"{counterparty.month_order_count} угод\n"
            )

            if risk:
                text += f"\n{risk}"

            text += (
                f"\n💰 <b>Сума:</b> <code>{total_fiat:.0f} ₴</code>"
                f" ({amount_usdt:.2f} USDT по {price:.2f})\n"
            )

            text += f"\n🧠 <b>AI:</b> {badge} {rec_text}\n"
            if reason and rec.upper() not in ("PENDING", "RECHECKING"):
                safe_reason = escape(str(reason).strip()[:300])
                text += f"<blockquote expandable>💬 {safe_reason}</blockquote>\n"

            # Кнопки
            kb_rows = []

            # Кешуємо order_id для кнопок
            short_oid = order_id[:20] if order_id else "?"
            kb_rows.append([
                InlineKeyboardButton(
                    text="✅ Прийняти",
                    callback_data=f"mkord:accept:{short_oid}",
                ),
                InlineKeyboardButton(
                    text="❌ Відхилити",
                    callback_data=f"mkord:reject:{short_oid}",
                ),
            ])

            # Лінк на біржу
            order_url = ""
            if exchange == "Bybit" and order_id:
                order_url = f"https://www.bybit.com/uk-UA/p2p/order/{order_id}"
            if order_url:
                kb_rows.append([InlineKeyboardButton(text="🔗 Відкрити на біржі", url=order_url)])

            # Бан контрагента
            mid = counterparty.merchant_id
            if mid:
                kb_rows.append([
                    InlineKeyboardButton(
                        text="🚫 Бан контрагента",
                        callback_data=f"fb:{exchange}:{mid}:triangle",
                    ),
                ])

            keyboard = InlineKeyboardMarkup(inline_keyboard=kb_rows)

            for chunk in self._split_message(text):
                await self._send_with_retry(
                    chunk,
                    keyboard=keyboard if chunk == text else None,
                    chat_id=chat_id,
                )

        except Exception as e:
            logger.error("send_maker_order_alert [%d]: %s", chat_id, e, exc_info=True)

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

        @self._router.callback_query(F.data.startswith("mkord:"))
        async def on_maker_order_action(call: CallbackQuery):
            """Обробка кнопок Прийняти/Відхилити для вхідних maker-ордерів."""
            try:
                parts = call.data.split(":")
                if len(parts) < 3:
                    return await call.answer("Помилка формату")

                action = parts[1]   # "accept" або "reject"
                order_id = parts[2]

                if action == "accept":
                    # Просто підтверджуємо — фактичний release робиться на біржі вручну
                    await call.answer("✅ Прийнято! Завершіть угоду на біржі.", show_alert=True)
                    old_text = call.message.html_text or ""
                    new_text = f"✅ <b>ПРИЙНЯТО</b>\n\n{old_text[:3500]}"
                    from contextlib import suppress
                    from aiogram.exceptions import TelegramBadRequest
                    with suppress(TelegramBadRequest):
                        await call.message.edit_text(new_text, reply_markup=None)

                elif action == "reject":
                    await call.answer("❌ Відхилено. Спробуйте скасувати на біржі.", show_alert=True)
                    old_text = call.message.html_text or ""
                    new_text = f"❌ <b>ВІДХИЛЕНО</b>\n\n<del>{old_text[:3500]}</del>"
                    from contextlib import suppress
                    from aiogram.exceptions import TelegramBadRequest
                    with suppress(TelegramBadRequest):
                        await call.message.edit_text(new_text, reply_markup=None)

                else:
                    await call.answer("Невідома дія")

            except Exception as e:
                logger.error("mkord callback error: %s", e)
                await call.answer("Помилка обробки", show_alert=True)


    async def start(self) -> None:
        # 🚀 СТВОРЮЄМО СИСТЕМНЕ МЕНЮ КНОПКОЮ (Повний список)
        commands =[
            BotCommand(command="start", description="▶️ Запустити мій сканер (Дашборд)"),
            BotCommand(command="stop", description="🛑 Зупинити мій сканер"),
            BotCommand(command="active", description="📡 Активні спреди зараз"),
            BotCommand(command="mode", description="🎯 Режим сканування"),
            BotCommand(command="ad", description="📢 Створити P2P оголошення"),
            BotCommand(command="ads", description="📝 Мої активні оголошення"),
            BotCommand(command="orders", description="📋 Мої поточні угоди"),
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

    async def _send_single(self, alert: SpreadAlert, chat_id: int | None = None, display_settings: dict | None = None, is_sniper_match: bool = False) -> None:
        # Display settings (per-user)
        ds = display_settings or {
            "show_ai_terms_summary": True, "show_full_terms": True,
            "show_ai_logic": True, "show_bank_details": True, "show_llm_summary": True,
        }

        # 🔄 Refresh LLM verdicts from DB (LLM може завершитись після створення алерту)
        if self._db:
            try:
                b_rec, _, b_reason, b_terms, b_rev = await self._db.get_trade_recommendation_full(
                    alert.buy_order.exchange, alert.buy_order.merchant_id
                )
                s_rec, _, s_reason, s_terms, s_rev = await self._db.get_trade_recommendation_full(
                    alert.sell_order.exchange, alert.sell_order.merchant_id
                )
                alert.buy_rec = b_rec
                alert.sell_rec = s_rec
                alert.buy_reason = b_reason
                alert.sell_reason = s_reason
                alert.buy_terms_summary = b_terms
                alert.sell_terms_summary = s_terms
                alert.buy_reviews_analysis = b_rev
                alert.sell_reviews_analysis = s_rev
            except Exception:
                pass  # fallback: використовуємо значення з алерту

        title, silent = _alert_grade(alert.spread_pct)
        if is_sniper_match:
            title = f"🎯 <b>СНАЙПЕР ОРДЕР!</b>\n{title}"
            silent = False  # Примусово вмикаємо звук для снайпера

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
        # buy_name / sell_name вже є <a href> лінками від _profile_link() (рядки 675-684)
        buy_name_str = f"{rec_badge(alert.buy_rec)} {buy_name}{_verified_badge(alert.buy_order)}"
        sell_name_str = f"{rec_badge(alert.sell_rec)} {sell_name}{_verified_badge(alert.sell_order)}"

        # 🧠 LLM Verdict блоки (конфігуровані per-user)
        if ds.get("show_llm_summary", True):
            buy_llm = _llm_verdict_block(
                "Buy", alert.buy_rec, alert.buy_reason,
                terms_summary=getattr(alert, "buy_terms_summary", ""),
                show_ai_logic=ds.get("show_ai_logic", True),
                show_ai_terms_summary=ds.get("show_ai_terms_summary", True),
                reviews_analysis=getattr(alert, "buy_reviews_analysis", "")
            )
            sell_llm = _llm_verdict_block(
                "Sell", alert.sell_rec, alert.sell_reason,
                terms_summary=getattr(alert, "sell_terms_summary", ""),
                show_ai_logic=ds.get("show_ai_logic", True),
                show_ai_terms_summary=ds.get("show_ai_terms_summary", True),
                reviews_analysis=getattr(alert, "sell_reviews_analysis", "")
            )
        else:
            # Тільки бейдж без деталей
            buy_llm = _llm_verdict_block("Buy", alert.buy_rec, "", show_ai_logic=False, show_ai_terms_summary=False)
            sell_llm = _llm_verdict_block("Sell", alert.sell_rec, "", show_ai_logic=False, show_ai_terms_summary=False)

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
        )

        # 🔘 Деталі банків (під спойлером, конфігуровано)
        if ds.get("show_bank_details", True):
            text += (
                f"<blockquote expandable>"
                f"🏦 Варіанти зв'язки: {route_variants}\n"
                f"🛒 Buy банки: {buy_all}\n"
                f"✅ Buy фільтр: {buy_fit}\n"
                f"💸 Sell банки: {sell_all}\n"
                f"✅ Sell фільтр: {sell_fit}"
                f"</blockquote>\n"
            )

        # ── КУПУЄМО ──
        buy_terms_raw = getattr(alert.buy_order, "trade_terms", "") or ""
        text += (
            f"🛒 <b>КУПУЄМО</b>\n"
            f"Курс: <code>{escape(str(alert.buy_order.price))}</code>\n"
            f"Мерчант: {buy_name_str} "
            f"({alert.buy_order.finish_rate_pct:.1f}% | {alert.buy_order.month_order_count} угод)\n"
            f"Ліміти: <code>{escape(str(alert.buy_order.min_limit))}–{escape(str(alert.buy_order.max_limit))} ₴</code>\n"
            f"{buy_risk if buy_risk else ''}"
            f"{buy_warn if buy_warn else ''}"
        )
        # 🔘 Окремий розділ умов (AI-вижимка + повний текст під спойлером)
        buy_terms_blk = _terms_block(
            buy_terms_raw,
            terms_summary=alert.buy_terms_summary,
            show_ai_terms_summary=ds.get("show_ai_terms_summary", True),
            show_full_terms=ds.get("show_full_terms", True),
        )
        if buy_terms_blk:
            text += buy_terms_blk
        text += f"\n{buy_llm}\n"

        # ── ПРОДАЄМО ──
        sell_terms_raw = getattr(alert.sell_order, "trade_terms", "") or ""
        text += (
            f"💸 <b>ПРОДАЄМО</b>\n"
            f"Курс: <code>{escape(str(alert.sell_order.price))}</code>\n"
            f"Мерчант: {sell_name_str} "
            f"({alert.sell_order.finish_rate_pct:.1f}% | {alert.sell_order.month_order_count} угод)\n"
            f"Ліміти: <code>{escape(str(alert.sell_order.min_limit))}–{escape(str(alert.sell_order.max_limit))} ₴</code>\n"
            f"{sell_risk if sell_risk else ''}"
            f"{sell_warn if sell_warn else ''}"
        )
        # 🔘 Окремий розділ умов (AI-вижимка + повний текст під спойлером)
        sell_terms_blk = _terms_block(
            sell_terms_raw,
            terms_summary=alert.sell_terms_summary,
            show_ai_terms_summary=ds.get("show_ai_terms_summary", True),
            show_full_terms=ds.get("show_full_terms", True),
        )
        if sell_terms_blk:
            text += sell_terms_blk
        text += f"\n{sell_llm}"

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

        # URL-кнопки (відкрити оголошення на біржі)
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
