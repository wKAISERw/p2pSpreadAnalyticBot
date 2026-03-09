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
from typing import Optional

from core.rules import (
    HARD_RULES,
    SOFT_RULES,
    SAFE_RULES,
    WARN_RULES,
    RegexRule,
)

logger = logging.getLogger("RegexAnalyzer")

LLM_SCORE_THRESHOLD    = 30   # мінімум для ескалації в LLM
MIN_MULTI_SIGNAL_SCORE = 20   # поріг при >= 2 різних категоріях


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


def _search_rule(rule: RegexRule, raw_text: str, fuzzy: str) -> Optional[re.Match]:
    m = rule.pattern.search(raw_text)
    return m if m else rule.pattern.search(fuzzy)


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

    raw_text = _clean_text(trade_terms)
    fuzzy    = _fuzzy_text(raw_text)

    result = RegexResult("OK")
    result.normalized_text = raw_text

    # ── 0. PRE-SCAN SAFE_RULES — suppressors перед HARD ─────────────────────
    # Які HARD категорії скасовуються — визначається з SUPPRESSOR_MAP (rules.py).
    # Логіка suppression не залежить від magic чисел — тільки від suppress_hard поля.
    suppressed_hard: set[str] = set()
    for rule in SAFE_RULES:
        if not rule.suppress_hard:
            continue
        m = _search_rule(rule, raw_text, fuzzy)
        if m:
            suppressed_hard |= rule.suppress_hard
            logger.debug("Suppressor %s активний → скасовує %s", rule.category, rule.suppress_hard)

    # ── 1. HARD BLOCKS ────────────────────────────────────────────────────────
    for rule in HARD_RULES:
        if rule.category in suppressed_hard:
            logger.debug("Suppressed HARD %s через suppressed_hard=%s", rule.category, suppressed_hard)
            continue
        m = _search_rule(rule, raw_text, fuzzy)
        if not m:
            continue
        result.matches.append(RegexMatch(
            rule_id=rule.id, category=rule.category,
            excerpt=_excerpt(raw_text, m), weight=rule.weight,
        ))
        result.score     = rule.weight
        result.verdict   = "BLOCK"
        result.risk_type = rule.category
        result.reason    = rule.description
        return result   # перший HARD = одразу BLOCK

    # ── 2. SOFT + SAFE сигнали ────────────────────────────────────────────────
    raw_score  = 0
    collected: list[RegexMatch] = []

    for rule in SOFT_RULES:
        m = _search_rule(rule, raw_text, fuzzy)
        if not m:
            continue
        collected.append(RegexMatch(
            rule_id=rule.id, category=rule.category,
            excerpt=_excerpt(raw_text, m), weight=rule.weight,
        ))
        raw_score += rule.weight

    for rule in SAFE_RULES:
        m = _search_rule(rule, raw_text, fuzzy)
        if not m:
            continue
        collected.append(RegexMatch(
            rule_id=rule.id, category=rule.category,
            excerpt=_excerpt(raw_text, m), weight=rule.weight,
        ))
        raw_score += rule.weight   # вже від'ємне — знижує score

    result.matches = collected
    result.score   = max(0, raw_score)   # score не може бути від'ємним

    # ── 3. WARN (незалежно від score) ─────────────────────────────────────────
    for rule in WARN_RULES:
        m = _search_rule(rule, raw_text, fuzzy)
        if m:
            result.warn_flags.append((rule.category, _excerpt(raw_text, m)))

    # ── 4. ЕСКАЛАЦІЯ в LLM ────────────────────────────────────────────────────
    positive_matches = [m for m in collected if m.weight > 0]
    categories = list(dict.fromkeys(m.category for m in positive_matches))

    should_escalate = (
        result.score >= LLM_SCORE_THRESHOLD
        or (len(categories) >= 2 and result.score >= MIN_MULTI_SIGNAL_SCORE)
    )

    if should_escalate:
        result.verdict   = "NEEDS_LLM"
        result.needs_llm = True
        if positive_matches:
            top = max(positive_matches, key=lambda x: x.weight)
            result.risk_type = top.category
            result.reason = (
                f"Сигналів: {len(positive_matches)} "
                f"({', '.join(categories)}) Score: {result.score}"
            )
        else:
            result.risk_type = "UNKNOWN_RISK"
            result.reason    = f"Підозрілий score: {result.score}"
        return result

    # ── 5. Слабкі сигнали — не ескалюємо, але логуємо ────────────────────────
    if positive_matches:
        top = max(positive_matches, key=lambda x: x.weight)
        result.risk_type = top.category
        result.reason = (
            f"Слабкі сигнали: {len(positive_matches)} "
            f"({', '.join(categories)}) Score: {result.score}"
        )

    return result