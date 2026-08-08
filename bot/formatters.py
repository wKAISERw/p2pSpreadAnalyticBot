"""
bot/formatters.py — Форматування алертів і бейджів для Telegram.
Виділено з notifications/telegram_notifier.py.
"""
from __future__ import annotations
from html import escape
import time
from exchanges.base import Order
from core.analytics.merchant_profile import build_profile_url, build_app_profile_url

# ── Бейджі ризику ──────────────────────────────────────────────────────
RISK_BADGES = {
    # «Не змогли перевірити» — окремий стан, не «чисто».
    #
    # Порядок у словнику має значення: risk_badge() бере ПЕРШИЙ ключ, який
    # трапився в рядку. UNKNOWN стоїть першим свідомо — якщо відгуків немає,
    # це головне, що треба сказати про мерчанта.
    "UNKNOWN":               "❔",

    # Поведінкові (Deep Research)
    "BEHAVIOR_BOTLIKE":      "🤖",
    "EXACT_LIMITS":          "🎯",
    "API_REPLENISH":         "🤖",   # бот-авто-поповнення
    "STATIC_DROP":           "📏",   # статичний дроп min=max
    "VELOCITY_SPIKE":        "⚡",
    "CROSS_EXCHANGE_BOT":    "👥",
    "FLICKER_RELIST":        "🔄",
    "NARROW_SPREAD":         "📏",
    "SYNERGY":               "🔗",   # комбо-підозра → LLM
 
    # Класичні (Regex)
    "BLOCK":                 "🚫",
    "NEEDS_LLM":             "🔍",
    "EXTERNAL_LINK":         "🔗",
    "TRIANGLE":              "🔺",
    "CASINO":                "🎰",
    "FINCRIME":              "💸",
    "CHARGEBACK":            "↩️",
    "SUSPICIOUS_BIZ":        "⚠️",
    "CHAT_FIRST":            "💬",
    "APPEAL_PRESSURE":       "📢",
    "BADREVIEWS":            "👎",
    "BADREVIEWS_TEXTS":      "👎",
    "HIGH_RISK_SCORE":       "📊",
 
    # Статуси
    "OK":                    "✅",
    "PENDING":               "⏳",
    "SAFE":                  "🛡️",
    "BLACKLIST":             "⛔",
    "WHITELIST":             "💚",
}

RISK_COLORS = {
    "BLOCK":     "🔴",
    "NEEDS_LLM": "🟡",
    "WARN":      "🟠",
    "OK":        "🟢",
}

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
    "BingX": "❇️",
}

BANKS_MAP = {
    "43": "Monobank",
    "14": "PrivatBank",
    "64": "ПУМБ",
    "48": "А-Банк",
}

# BANK_CODE_TO_DB_NAME переїхала в config/banks.py: це була четверта копія
# мапи «код → назва банку», і саме тут жили альтернативні коди 61/80/1,
# яких решта системи не знала.

BANKS_SHORT = {
    "43": "Mono",
    "14": "Privat",
    "64": "ПУМБ",
    "48": "А-Банк",
}

REC_LABELS = {
    "APPROVE":     "✅ БЕЗПЕЧНО",
    "CONDITIONAL": "⚡ З ОБЕРЕЖНІСТЮ",
    "REJECT":      "🚫 НЕ ТОРГУВАТИ",
    "PENDING":     "🔍 AI АНАЛІЗУЄ…",
    "RECHECKING":  "🔄 AI ПЕРЕПРОВІРЯЄ…",
}


def risk_badge(risk_flag: str) -> str:
    """Повертає емодзі-бейдж для risk_flag."""
    if not risk_flag:
        return "✅"
    flag_upper = risk_flag.upper()
    for key, badge in RISK_BADGES.items():
        if key in flag_upper:
            return badge
    return "⚠️"


# Чому саме не вдалось перевірити — людською мовою.
UNKNOWN_REASONS = {
    "NO_SESSION": "немає сесії біржі",
    "NO_AUTH": "біржа не авторизує запит",
    "UNAVAILABLE": "біржа не відповідає",
    "NOT_SUPPORTED": "біржа не віддає відгуки",
    "EMPTY": "мерчант не вказав умов",
}


def format_risk_line(risk_flag: str) -> str:
    """Форматує рядок ризику для Telegram-повідомлення."""
    if not risk_flag or risk_flag == "OK":
        return ""

    # «Невідомо» читається інакше за ризик: це не звинувачення мерчанта, а
    # чесне «ми не змогли подивитись». Мовчати про це не можна — саме так
    # непройдена перевірка й видавалась за успішну.
    if "UNKNOWN:" in risk_flag.upper():
        gaps = []
        for chunk in risk_flag.split(","):
            chunk = chunk.strip().upper()
            if not chunk.startswith("UNKNOWN:"):
                continue
            parts = chunk.split(":")
            what = "відгуки" if "REVIEWS" in parts else "умови угоди"
            why = UNKNOWN_REASONS.get(parts[-1], "технічна причина")
            gaps.append(f"{what} — {why}")
        if gaps:
            return "❔ <b>НЕ ПЕРЕВІРЕНО</b>: <i>" + "; ".join(gaps) + "</i>"

    badge = risk_badge(risk_flag)
    # Вирізаємо технічні деталі для читабельності
    display = risk_flag.replace("BLOCK:", "").replace("NEEDS_LLM:", "")
    parts = display.split(":", 1)
    risk_type = parts[0].strip()
    detail = parts[1].strip() if len(parts) > 1 else ""
    if detail:
        return f"{badge} <b>{risk_type}</b>: <i>{detail[:80]}</i>"
    return f"{badge} <b>{risk_type}</b>"


def format_behavioral_summary(risk_flag: str) -> str:
    """
    Виділяє поведінкові флаги для окремого блоку в алерті.
    Повертає порожній рядок якщо поведінкових флагів немає.
    """
    if not risk_flag:
        return ""
 
    import re
 
    # Флаги що мають значення після двокрапки (напр. API_REPLENISH:42)
    VALUE_FLAGS = {
        "API_REPLENISH":    ("🤖", "АВТО-БОТ"),
        "STATIC_DROP":      ("📏", "СТАТИК-ДРОП"),
        "VELOCITY_SPIKE":   ("⚡", "VELOCITY"),
        "FLICKER_RELIST":   ("🔄", "РІЛІСТИНГ"),
        "CROSS_EXCHANGE_BOT": ("👥", "КЛОН"),
        "BEHAVIOR_BOTLIKE": ("🤖", "BOTLIKE"),
    }
    # Флаги без значення
    SIMPLE_FLAGS = {
        "EXACT_LIMITS": ("🎯", "ФІКС.ЛІМІТ"),
    }
 
    found = []
 
    for key, (badge, label) in VALUE_FLAGS.items():
        m = re.search(rf"{key}:([\w./]+)", risk_flag, re.IGNORECASE)
        if m:
            found.append(f"{badge} {label}:{m.group(1)}")
        elif key in risk_flag.upper():
            found.append(f"{badge} {label}")
 
    for key, (badge, label) in SIMPLE_FLAGS.items():
        if key in risk_flag.upper():
            found.append(f"{badge} {label}")
 
    return " | ".join(found)


def _bank_code_to_db(code: str) -> str:
    """Конвертує числовий код банку біржі в назву в БД."""
    from config.banks import normalize_bank

    return normalize_bank(code)


def rec_badge(rec: str) -> str:
    return {
        "APPROVE":     "✅",
        "CONDITIONAL": "⚡",
        "REJECT":      "🚫",
        "PENDING":     "🔍",
        "RECHECKING":  "🔄",
    }.get((rec or "PENDING").upper(), "🔍")


def _llm_verdict_block(
    label: str, rec: str, reason: str,
    terms_summary: str = "",        # залишаємо для сумісності — тепер у _terms_block
    show_ai_logic: bool = True,
    show_ai_terms_summary: bool = True,  # залишаємо для сумісності
    reviews_analysis: str = "",
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
 
    # 📝 Аналіз відгуків (якщо є)
    if reviews_analysis and str(reviews_analysis).strip():
        safe_rev = escape(str(reviews_analysis).strip()[:300])
        line += f"<blockquote expandable>📝 Відгуки: {safe_rev}</blockquote>\n"
 
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


def _profile_link(
    exchange: str,
    merchant_id: str,
    merchant_name: str,
    side: str = "",
    profile_mode: str = "chat",
    offer_id: str = "",
) -> str:
    """Генерує клікабельне ім'я мерчанта (синій лінк у Telegram → профіль/ордер на біржі)."""
    safe_name = escape(str(merchant_name or "Unknown"))
    if not merchant_id:
        return safe_name

    web_url = build_profile_url(
        exchange, merchant_id, merchant_name,
        side=side, profile_mode=profile_mode, offer_id=offer_id,
    )
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
        "FOP_TOV_WARN": "⚠️ УВАГА: Оплата на ФОП/ТОВ\n" if not short else "⚠️🏢",
        "BANKA_JAR_WARN": "⚠️ УВАГА: Оплата на банку/сейф\n" if not short else "⚠️🍯",
        "FOP_TOV_BLOCKED": "🏢 ФОП / ТОВ (приховано)\n" if not short else "🏢",
        "BANKA_JAR_BLOCKED": "🍯 БАНКА / СЕЙФ (приховано)\n" if not short else "🍯",
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
    detected_risk_types: set[str] = set()   # для показу trade_terms
 
    for f in flags:
        # ── Відгуки (NEEDS_LLM:BADREVIEWS: ПЕРЕД BADREVIEWS: !) ────────
        if f.startswith("NEEDS_LLM:BADREVIEWS:"):
            lines.append("🗣👎 ПОГАНІ ВІДГУКИ → AI ПЕРЕВІРКА\n" if not short else "🗣")
            detected_risk_types.add("BADREVIEWS")
            continue
 
        if f.startswith("BADREVIEWS_TEXTS:"):
            lines.append("🗣 ПІДОЗРІЛІ ВІДГУКИ (тексти)\n" if not short else "🗣")
            continue
 
        if f.startswith("BADREVIEWS:"):
            lines.append(badges["BADREVIEWS"])
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
            if risk == "BLACKLIST":
                lines.append(badges["BLACKLIST"])
            else:
                lines.append(badges.get(risk, badges["BLOCK"]))
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
            detected_risk_types.add(risk_type)
            continue
 
        if f.startswith("LLM_UNKNOWN:"):
            lines.append(badges["LLM_UNKNOWN"])
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