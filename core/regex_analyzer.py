# core/regex_analyzer.py
"""
Миттєвий regex-аналізатор trade_terms.
Якщо знаходить критичний маркер — одразу BLOCK, LLM не викликається.
"""
import re
import logging

logger = logging.getLogger("RegexAnalyzer")

_TRIANGLE = re.compile(
    r"чужі\s*(карт|рахун)|перерахун[ок]{0,2}\s*від\s*інш"
    r"|дроп[иів]?|без\s*(коментарів?|комент|призначен)"
    r"|переказ\s*від\s*(знайом|друг)",
    re.IGNORECASE,
)

_CASINO = re.compile(
    r"казино|casino|1xbet|1x\s*bet|melbet|mostbet|betway|parimatch"
    r"|покер|poker|ставк[иа]|букмекер|bookie"
    r"|процесинг|processing|агрегатор|обмінник",
    re.IGNORECASE,
)

_CHAT_FIRST = re.compile(
    r"пишіть\s+(у|в)\s+чат|пишіть\s+мені|напишіть\s+(спочатку|перед)"
    r"|contact\s*(me\s*)?before|write\s*(me\s*)?first"
    r"|telegram\s*перед|tg\s*перед|написати\s+до\s+оплат",
    re.IGNORECASE,
)

_SUSPICIOUS = re.compile(
    r"анонімн|anonymous|без\s*перевірк|не\s*питаю|no\s*questions"
    r"|фоп\s*оплат|фізична\s*особа\s*підприємець"
    r"|апеляція|апеляцію\s*відкрию",
    re.IGNORECASE,
)

# А слова про третіх осіб переносимо сюди, щоб їх контекст читала нейронка!
_SOFT_FLAGS = re.compile(
    r"тільки\s*для\s*нових|new\s*users\s*only"
    r"|перший\s*раз|вперше"
    r"|тре(тя|тіх)|третіх\s+осіб|3\s*особ|third\s*party|third\s*person"
    r"|від\s*третіх|від\s*інших|не\s*від\s*свого",
    re.IGNORECASE,
)


# ── Результат ─────────────────────────────────────────────────────────────────

class RegexResult:
    __slots__ = ("verdict", "risk_type", "reason", "needs_llm")

    def __init__(self, verdict: str, risk_type: str = "",
                 reason: str = "", needs_llm: bool = False):
        self.verdict    = verdict    # OK / BLOCK / NEEDS_LLM
        self.risk_type  = risk_type  # TRIANGLE / CASINO / CHAT_FIRST / ...
        self.reason     = reason
        self.needs_llm  = needs_llm


def analyze(trade_terms: str, finish_rate: float = 100.0,
            order_count: int = 0, is_verified: bool = False) -> RegexResult:
    """
    Повертає RegexResult.
    """
    text = (trade_terms or "").strip()

    # ✅ ВИПРАВЛЕНО: Якщо умов немає — це нормально для P2P.
    # Пропускаємо одразу як OK, щоб не забивати чергу до LLM.
    if not text:
        return RegexResult("OK")

    # Критичні — одразу BLOCK без LLM
    if _TRIANGLE.search(text):
        return RegexResult("BLOCK", "TRIANGLE",
                           _excerpt(text, _TRIANGLE))

    if _CASINO.search(text):
        return RegexResult("BLOCK", "CASINO",
                           _excerpt(text, _CASINO))

    if _CHAT_FIRST.search(text):
        return RegexResult("BLOCK", "CHAT_FIRST",
                           _excerpt(text, _CHAT_FIRST))

    if _SUSPICIOUS.search(text):
        return RegexResult("BLOCK", "SUSPICIOUS",
                           _excerpt(text, _SUSPICIOUS))

    # М'які — відправляємо в LLM
    if _SOFT_FLAGS.search(text):
        return RegexResult("NEEDS_LLM", "SOFT_FLAG",
                           "Нестандартні умови", needs_llm=True)

    # Підозрілий ідеальний рейтинг при великій кількості угод
    if order_count >= 50 and finish_rate >= 99.9 and not is_verified:
        return RegexResult("NEEDS_LLM", "PERFECT_RATING",
                           f"100% при {order_count} угодах", needs_llm=True)

    return RegexResult("OK")


def _excerpt(text: str, pattern: re.Pattern) -> str:
    """Витягує фрагмент навколо знайденого маркера."""
    m = pattern.search(text)
    if not m:
        return text[:80]
    start = max(0, m.start() - 15)
    end   = min(len(text), m.end() + 30)
    snippet = text[start:end].strip()
    return f'"{snippet}"'