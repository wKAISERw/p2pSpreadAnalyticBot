"""
bot/formatters.py — Форматування алертів і бейджів для Telegram.
Виділено з notifications/telegram_notifier.py.
"""
from __future__ import annotations

# ── Бейджі ризику ──────────────────────────────────────────────────────
RISK_BADGES = {
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


def risk_badge(risk_flag: str) -> str:
    """Повертає емодзі-бейдж для risk_flag."""
    if not risk_flag:
        return "✅"
    flag_upper = risk_flag.upper()
    for key, badge in RISK_BADGES.items():
        if key in flag_upper:
            return badge
    return "⚠️"


def format_risk_line(risk_flag: str) -> str:
    """Форматує рядок ризику для Telegram-повідомлення."""
    if not risk_flag or risk_flag == "OK":
        return ""
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