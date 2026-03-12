"""
bot/formatters.py — Форматування алертів і бейджів для Telegram.
Виділено з notifications/telegram_notifier.py.
"""
from __future__ import annotations

# ── Бейджі ризику ──────────────────────────────────────────────────────
RISK_BADGES = {
    # Поведінкові (Deep Research)
    "BEHAVIOR_BOTLIKE":      "🤖",
    "EXACT_LIMITS":          "📏",
    "STICKY_LIMITS":         "📏",
    "VELOCITY_SPIKE":        "⚡",
    "CROSS_EXCHANGE_BOT":    "👥",
    "FLICKER_RELIST":        "🔄",

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
    behavioral_keys = {
        "BEHAVIOR_BOTLIKE", "EXACT_LIMITS", "STICKY_LIMITS",
        "VELOCITY_SPIKE", "CROSS_EXCHANGE_BOT", "FLICKER_RELIST",
    }
    found = []
    for key in behavioral_keys:
        if key in risk_flag.upper():
            badge = RISK_BADGES.get(key, "⚠️")
            # Витягуємо значення після ключа якщо є (напр. STICKY_LIMITS:42)
            pattern = key + "[:\\w./]*"
            import re
            m = re.search(pattern, risk_flag, re.IGNORECASE)
            found.append(f"{badge} {m.group(0) if m else key}")
    return " | ".join(found)
