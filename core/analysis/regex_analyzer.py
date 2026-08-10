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

from core.analysis.rules import (
    HARD_RULES,
    SOFT_RULES,
    SAFE_RULES,
    WARN_RULES,
    RegexRule,
)

logger = logging.getLogger("RegexAnalyzer")

LLM_SCORE_THRESHOLD    = 30   # мінімум для ескалації в LLM
MIN_MULTI_SIGNAL_SCORE = 20   # поріг при >= 2 різних категоріях


# ─────────────────────────────────────────────────────────────────────────────
# Правила, які взагалі мають право дивитись на УМОВИ УГОДИ
# ─────────────────────────────────────────────────────────────────────────────
#
# `review_only` — прапорець на правилі, який каже: це написано під мову
# ВІДГУКІВ, не під мову оголошення. «Кинув», «шахрай», «дроп» у відгуку пише
# потерпілий, і вага 100 там доречна. Те саме слово в умовах пише сам
# мерчант — найчастіше щоб від цього відхреститись.
#
# `review_fetcher` цей прапорець поважав завжди. Цей модуль — ні: цикли нижче
# ходили по ВСІХ SOFT_RULES і SAFE_RULES. А з 25 SOFT-правил 18 мають
# review_only=True, серед них сім із вагою 100 і сім із вагою 50 — при порозі
# ескалації 30. Тобто одне випадкове спрацювання review-правила на тексті
# оголошення давало score 100, verdict=NEEDS_LLM і risk_type=TRIANGLE, і LLM
# отримувала промпт, у якому вже написано «Regex main risk: TRIANGLE».
#
# У базі це виглядало як 892 BLOCK із 1290 вердиктів (69%), з них TRIANGLE
# 457 і FINCRIME 247.
#
# Фільтруємо один раз на імпорті, а не в кожному виклику: analyze() працює
# на кожен ордер кожного циклу.
TERMS_HARD_RULES = [r for r in HARD_RULES if not r.review_only]
TERMS_SOFT_RULES = [r for r in SOFT_RULES if not r.review_only]
TERMS_SAFE_RULES = [r for r in SAFE_RULES if not r.review_only]
TERMS_WARN_RULES = [r for r in WARN_RULES if not r.review_only]


# Custom patterns for user-configurable blocks
FOP_TOV_PATTERN = re.compile(
    r"\b(?:фоп[ауие]?|тов[ау]?|ооо|іп[ау]?|флп[ау]?|юр\.?\s*особ[аиуї]?|підприєм[еацїік]{2,6})\b|"
    r"(?:бізнес[\s-]рахун)|(?:юридичн[аої]\s*особ)",
    re.IGNORECASE
)

BANKA_JAR_PATTERN = re.compile(
    r"(?:send\.monobank\.ua)|"
    r"\bбань?к[ауи]\s+(?:моно|mono)\b|"
    r"\b(?:моно|mono|monobank|монобанк)\s+бань?к[ауи]\b|"
    r"\b(?:на|через|в|into|to|through|via)\s+['\"«`]?бань?к[ауи]\b|"
    r"\b(?:посилання|посиланням|лінк|link|ссылк[аиоу]|ссылкой)\s+(?:на\s+)?['\"«`]?бань?к[ауи]\b|"
    r"\bбань?к[ауи]\s+(?:по|за|в)\s+(?:ссылк[еиау]|посиланн\w*|чат|лс)\b|"
    r"\b(?:створ|созда|откри|відкри)\w{0,8}\s+бань?к[ауи]\b|"
    r"\b(?:на|через|в|посилання|посиланням|лінк|link|ссылк[аиоу]|ссылкой)\s+(?:на\s+)?(?:копилк[ау]|сейф[ау]?|конверт[ау]?)\b|"
    r"\bконверт[ауи]?\s+(?:приват|privat)\b|"
    r"\b(?:моно|mono|monobank|монобанк)\s+(?:\w+\s+){0,3}(?:посиланн\w*|ссылк\w*)\b|"
    r"\b(?:посиланн\w*|ссылк\w*)\s+(?:\w+\s+){0,3}(?:моно|mono|monobank|монобанк)\b",
    re.IGNORECASE
)

NEGATION_PREFIX_PATTERN = re.compile(
    r"\b(?:не|ні|нет|без|not|no|немає?|запрещен[ыоа]?|заборонен[оиаї]?|виключен[оиаї]?)\b\s*\w*\s*$",
    re.IGNORECASE
)
NEGATION_SUFFIX_PATTERN = re.compile(
    r"^\s*\w*\s*\b(?:не|ні|нет|без|not|no|немає?|запрещен[ыоа]?|заборонен[оиаї]?|виключен[оиаї]?)\b",
    re.IGNORECASE
)


def _is_negated(text: str, start: int, end: int) -> bool:
    prefix_start = max(0, start - 35)
    prefix = text[prefix_start:start]
    suffix_end = min(len(text), end + 25)
    suffix = text[end:suffix_end]

    last_clause_boundary = max(
        prefix.rfind('.'), prefix.rfind('!'), prefix.rfind('?'),
        prefix.rfind(';'), prefix.rfind('\n')
    )
    if last_clause_boundary != -1:
        prefix = prefix[last_clause_boundary + 1:]

    first_clause_boundary = min(
        [suffix.find(c) for c in ('.', '!', '?', ';', '\n') if suffix.find(c) != -1] or [len(suffix)]
    )
    suffix = suffix[:first_clause_boundary]

    if NEGATION_PREFIX_PATTERN.search(prefix):
        return True
    if NEGATION_SUFFIX_PATTERN.search(suffix):
        return True

    return False


def check_custom_blocks_metadata(terms: str) -> list[str]:
    if not terms or not str(terms).strip():
        return []
    raw_text = _clean_text(terms)
    fuzzy = _fuzzy_text(raw_text)

    flags = []
    m_fop = FOP_TOV_PATTERN.search(raw_text) or FOP_TOV_PATTERN.search(fuzzy)
    if m_fop:
        start, end = m_fop.span()
        if not _is_negated(raw_text, start, end):
            flags.append("FOP_TOV_BLOCKED")

    m_jar = BANKA_JAR_PATTERN.search(raw_text) or BANKA_JAR_PATTERN.search(fuzzy)
    if m_jar:
        start, end = m_jar.span()
        if not _is_negated(raw_text, start, end):
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
    # Читаємо rule.suppress_hard напряму (frozenset або list — обидва варіанти).
    # suppress_hard містить імена HARD категорій що треба скасувати при збігу.
    # Стара _SUPPRESSOR_MAP по назві категорії suppressor-правила — видалена.
    suppressed_hard: set[str] = set()
    for rule in TERMS_SAFE_RULES:
        sh = getattr(rule, "suppress_hard", None)
        if not sh:
            continue
        m = _search_rule(rule, raw_text, fuzzy)
        if m:
            # Захист від type mismatch: frozenset|=list → TypeError у CPython < 3.9
            suppressed_hard.update(sh)
            logger.debug(
                "Suppressor '%s' активний → скасовує %s",
                rule.id, list(sh),
            )

    # ── 1. HARD BLOCKS ────────────────────────────────────────────────────────
    for rule in TERMS_HARD_RULES:
        if rule.category in suppressed_hard:
            logger.debug(
                "Suppressed HARD %s (rule %s) через suppressed_hard=%s",
                rule.category, rule.id, suppressed_hard,
            )
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

    for rule in TERMS_SOFT_RULES:
        m = _search_rule(rule, raw_text, fuzzy)
        if not m:
            continue
        collected.append(RegexMatch(
            rule_id=rule.id, category=rule.category,
            excerpt=_excerpt(raw_text, m), weight=rule.weight,
        ))
        raw_score += rule.weight

    for rule in TERMS_SAFE_RULES:
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
    for rule in TERMS_WARN_RULES:
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