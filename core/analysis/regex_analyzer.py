# core/regex_analyzer.py
# =============================================================================
# БОЙОВА ВЕРСІЯ v1.0  (Крок 2: правила винесено в rules.py)
# =============================================================================
"""
Миттєвий regex-аналізатор trade_terms.

Задача цього модуля — ТІЛЬКИ логіка аналізу.
Всі словники та патерни — в core/rules.py.

Pipeline:
  1. HARD_RULES  → якщо є збіг → BLOCK (повертаємо одразу)
  2. SOFT_RULES  → накопичуємо score + excerpts
  3. SAFE_RULES  → від'ємні бали (suppressors)
  4. WARN_RULES  → окремі warning-флаги (не блокують)
  5. Ескалація:  score >= LLM_SCORE_THRESHOLD → NEEDS_LLM
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass

from core.risk.matcher import match_text
from core.risk.signals import LAYER_SAFE, LAYER_SOFT, LAYER_WARN, SCOPE_TERMS

# Персональні фільтри питають лише про одну категорію — решту сигналів
# ганяти заради цього нема сенсу.
PAYMENT_TARGET_ONLY = frozenset({"PAYMENT_TARGET"})

logger = logging.getLogger("RegexAnalyzer")

LLM_SCORE_THRESHOLD    = 30   # мінімум для ескалації в LLM
MIN_MULTI_SIGNAL_SCORE = 20   # поріг при >= 2 різних категоріях


def check_custom_blocks_metadata(terms: str) -> list[str]:
    """
    Метадані для персональних фільтрів: ФОП/ТОВ і накопичувальні рахунки.

    Раніше тут жили два власні патерни (`FOP_TOV_PATTERN`, `BANKA_JAR_PATTERN`)
    зі своїм окремим шляхом перевірки заперечень — поза реєстром і поза
    матчером. Патерни були дірявими саме там, де люди пишуть найчастіше:
    «приймаю на монобанку», «а-банк збір», «накопичувальний збір»,
    «скарбничка» не ловились жодним.

    Тепер це категорія PAYMENT_TARGET у реєстрі, а словник лежить словами
    в `core/risk/vocabulary.py` — його видно й можна доповнити.

    Назви прапорів лишаються старі: їх читають alert_builder,
    alert_dispatcher, monitoring і formatters.
    """
    found = match_text(terms, scope=SCOPE_TERMS, categories=PAYMENT_TARGET_ONLY)
    keys = {m.signal.key for m in found.matches}

    flags = []
    if "PAY_BUSINESS" in keys:
        flags.append("FOP_TOV_BLOCKED")
    if "PAY_JAR" in keys:
        flags.append("BANKA_JAR_BLOCKED")
    return flags


@dataclass(slots=True)
class RegexMatch:
    rule_id:  str
    category: str
    excerpt:  str
    weight:   int


class RegexResult:
    __slots__ = (
        "verdict",
        "risk_type",
        "reason",
        "needs_llm",
        "score",
        "matches",
        "warn_flags",
        "normalized_text",
        "signal_keys",
    )

    def __init__(
        self,
        verdict:   str,
        risk_type: str = "",
        reason:    str = "",
        needs_llm: bool = False,
    ):
        self.verdict         = verdict      # OK / BLOCK / NEEDS_LLM
        self.risk_type       = risk_type
        self.reason          = reason
        self.needs_llm       = needs_llm
        self.score           = 0
        self.matches:    list[RegexMatch] = []
        self.warn_flags: list[tuple[str, str]] = []
        # warn_flags: [(category, excerpt), ...]  — RECEIPT_REQUIRED та ін.
        # excerpt дозволяє показати в алерті конкретний фрагмент умов.
        self.normalized_text = ""
        # Ключі ВСЬОГО, що спрацювало, — окремо від `matches`.
        #
        # `matches` несе лише те, що має вагу, бо на ньому тримається
        # score. Персональній політиці потрібне інше: факт «платіж іде на
        # банку» ваги не має взагалі, але саме його людина хоче ховати.
        self.signal_keys: list[str] = []


# ─────────────────────────────────────────────────────────────────────────────
# Internal helpers
# ─────────────────────────────────────────────────────────────────────────────

def _clean_text(text: str) -> str:
    text = (text or "").lower()
    text = re.sub(r"[\u200b-\u200f\u2060\ufeff]", "", text)
    text = text.replace("ё", "е")
    text = re.sub(r"\s+", " ", text)
    return text.strip()


def _fuzzy_text(text: str) -> str:
    """Замінює латинські омографи на кирилицю для обходу маскування."""
    replacements = str.maketrans({
        "o": "о", "a": "а", "e": "е", "i": "і",
        "c": "с", "p": "р", "x": "х", "y": "у",
        "k": "к", "m": "м", "t": "т", "b": "в", "h": "н",
    })
    return text.translate(replacements)


def _excerpt(text: str, match_obj: re.Match) -> str:
    start = max(0, match_obj.start() - 20)
    end   = min(len(text), match_obj.end() + 20)
    return f'"{text[start:end].strip()}"'


# ─────────────────────────────────────────────────────────────────────────────
# Public API
# ─────────────────────────────────────────────────────────────────────────────

def analyze(
    trade_terms: str,
    finish_rate: float = 100.0,
    order_count: int   = 0,
    is_verified: bool  = False,
) -> RegexResult:
    """
    Аналізує умови мерчанта і повертає RegexResult.

    Аргументи finish_rate / order_count / is_verified наразі приймаються
    для сумісності з RiskEngine (майбутня можливість скоригувати пороги
    залежно від статистики).
    """
    if not trade_terms or not str(trade_terms).strip():
        return RegexResult("OK")

    found = match_text(trade_terms, scope=SCOPE_TERMS)

    result = RegexResult("OK")
    result.normalized_text = found.normalized

    # ── 1. HARD ──────────────────────────────────────────────────────────
    hard = found.hard
    if hard:
        result.matches = [
            RegexMatch(hard.signal.key, hard.category, f'"{hard.excerpt}"', hard.weight)
        ]
        result.signal_keys = found.actionable_keys
        result.score     = hard.weight
        result.verdict   = "BLOCK"
        result.risk_type = hard.category
        result.reason    = hard.signal.why or hard.signal.title
        return result

    # ── 2. SOFT + SAFE ───────────────────────────────────────────────────
    scored = [m for m in found.matches if m.signal.layer in (LAYER_SOFT, LAYER_SAFE)]
    result.matches = [
        RegexMatch(m.signal.key, m.category, f'"{m.excerpt}"', m.weight) for m in scored
    ]
    result.score = max(0, sum(m.weight for m in scored))
    result.signal_keys = found.actionable_keys

    # ── 3. WARN ──────────────────────────────────────────────────────────
    for m in found.by_layer(LAYER_WARN):
        result.warn_flags.append((m.category, f'"{m.excerpt}"'))

    # ── 4. Ескалація в LLM ───────────────────────────────────────────────
    positive = [m for m in scored if m.weight > 0]
    categories = list(dict.fromkeys(m.category for m in positive))

    should_escalate = (
        result.score >= LLM_SCORE_THRESHOLD
        or (len(categories) >= 2 and result.score >= MIN_MULTI_SIGNAL_SCORE)
    )

    if should_escalate:
        result.verdict   = "NEEDS_LLM"
        result.needs_llm = True
        if positive:
            top = max(positive, key=lambda x: x.weight)
            result.risk_type = top.category
            result.reason = (
                f"Сигналів: {len(positive)} "
                f"({', '.join(categories)}) Score: {result.score}"
            )
        else:
            result.risk_type = "UNKNOWN_RISK"
            result.reason    = f"Підозрілий score: {result.score}"
        return result

    # ── 5. Слабкі сигнали — не ескалюємо, але лишаємо слід ───────────────
    if positive:
        top = max(positive, key=lambda x: x.weight)
        result.risk_type = top.category
        result.reason = (
            f"Слабкі сигнали: {len(positive)} "
            f"({', '.join(categories)}) Score: {result.score}"
        )

    return result
