# core/regex_analyzer.py
"""
Миттєвий regex-аналізатор trade_terms (Rule Engine).

Цілі:
- одразу блокувати критичний скам;
- накопичувати soft-сигнали й ескалювати тільки достатньо підозрілі кейси;
- зберігати backward compatibility з поточним RiskEngine / LLMWorker;
- давати структурований результат для майбутнього prompt compression.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from typing import Iterable

logger = logging.getLogger("RegexAnalyzer")

LLM_SCORE_THRESHOLD = 30
MIN_MULTI_SIGNAL_SCORE = 20


@dataclass(slots=True)
class RegexRule:
    id: str
    category: str
    pattern: re.Pattern
    weight: int
    action: str  # BLOCK / NEEDS_LLM / SAFE
    description: str


@dataclass(slots=True)
class RegexMatch:
    rule_id: str
    category: str
    excerpt: str
    weight: int


class RegexResult:
    __slots__ = (
        "verdict",
        "risk_type",
        "reason",
        "needs_llm",
        "score",
        "matches",
        "normalized_text",
    )

    def __init__(
        self,
        verdict: str,
        risk_type: str = "",
        reason: str = "",
        needs_llm: bool = False,
    ):
        self.verdict = verdict          # OK / BLOCK / NEEDS_LLM
        self.risk_type = risk_type
        self.reason = reason
        self.needs_llm = needs_llm
        self.score = 0
        self.matches: list[RegexMatch] = []
        self.normalized_text = ""


# ── HARD BLOCK ────────────────────────────────────────────────────────────────

HARD_RULES: list[RegexRule] = [
    RegexRule(
        "H1",
        "EXTERNAL_LINK",
        re.compile(
            r"t\.me/|telegram\.me/|viber://|"
            r"@\w{4,}|"
            r"(пишіть|напишіть|contact|write).{0,20}(telegram|tg|viber|whatsapp|signal)|"
            r"(telegram|tg|viber|whatsapp|signal).{0,20}(перед оплатою|before payment|before deal)",
            re.IGNORECASE,
        ),
        100,
        "BLOCK",
        "Вимагає винести спілкування в зовнішній месенджер",
    ),
    RegexRule(
        "H2",
        "TRIANGLE",
        re.compile(
            r"чуж[іаю]\s*(карт|рахун)|"
            r"карта\s*(знайом|друг|дружин|брат)|"
            r"дроп[иів]?|"
            r"переказ\s*від\s*(знайом|друг)|"
            r"оплата\s*від\s*інш(ої|ого)\s*особ",
            re.IGNORECASE,
        ),
        100,
        "BLOCK",
        "Явна вимога оплати від третьої особи",
    ),
    RegexRule(
        "H3",
        "NO_COMMENTS",
        re.compile(
            r"без\s*(коментарів?|комент|призначен)|"
            r"пусте\s*поле|"
            r"нічого\s*не\s*пиш|"
            r"не\s*вказуйте\s*призначення",
            re.IGNORECASE,
        ),
        100,
        "BLOCK",
        "Заборона коментарів до платежу",
    ),
    RegexRule(
        "H4",
        "CASINO",
        re.compile(
            r"казино|casino|1xbet|1x\s*bet|melbet|mostbet|betway|parimatch|"
            r"покер|poker|букмекер|bookie|"
            r"процесинг|processing|агрегатор",
            re.IGNORECASE,
        ),
        100,
        "BLOCK",
        "Казино, ставки, процесинг",
    ),
]

# ── SOFT SIGNALS ─────────────────────────────────────────────────────────────

SOFT_RULES: list[RegexRule] = [
    RegexRule(
        "S1",
        "SUSPICIOUS_BIZ",
        re.compile(
            r"фоп\s*оплат|"
            r"фізична\s*особа\s*підприємець|"
            r"рахунок\s*фоп|"
            r"iban\s*фоп",
            re.IGNORECASE,
        ),
        40,
        "NEEDS_LLM",
        "Оплата на рахунок ФОП",
    ),
    RegexRule(
        "S2",
        "CHAT_FIRST",
        re.compile(
            r"пишіть\s+мені|"
            r"contact\s*(me\s*)?before|"
            r"write\s*(me\s*)?first|"
            r"написати\s+до\s+оплат|"
            r"напишіть\s+(спочатку|перед)",
            re.IGNORECASE,
        ),
        30,
        "NEEDS_LLM",
        "Просить писати в чат до оплати",
    ),
    RegexRule(
        "S3",
        "APPEAL_PRESSURE",
        re.compile(
            r"апеляція|скарга|апеляцію\s*відкрию|"
            r"appeal|report\s+you",
            re.IGNORECASE,
        ),
        30,
        "NEEDS_LLM",
        "Тиск апеляцією або скаргою",
    ),
    RegexRule(
        "S4",
        "NEW_USERS",
        re.compile(
            r"тільки\s*для\s*нових|"
            r"new\s*users\s*only|"
            r"перший\s*раз|вперше",
            re.IGNORECASE,
        ),
        20,
        "NEEDS_LLM",
        "Таргет на новачків",
    ),
    RegexRule(
        "S5",
        "ANONYMOUS",
        re.compile(
            r"анонімн|anonymous|"
            r"без\s*перевірк|"
            r"no\s*questions|"
            r"термінал|cash-in",
            re.IGNORECASE,
        ),
        30,
        "NEEDS_LLM",
        "Анонімність або поповнення готівкою",
    ),
    RegexRule(
        "S6",
        "THIRD_PARTY_HINT",
        re.compile(
            r"третіх\s+осіб|3\s*особ|third\s*party|third\s*person|"
            r"від\s*третіх|від\s*інших|не\s*від\s*свого|"
            r"telegram|tg|viber|whatsapp|signal",
            re.IGNORECASE,
        ),
        20,
        "NEEDS_LLM",
        "Є згадка третіх осіб або зовнішнього контакту, потрібен контекст",
    ),
]


# ── SAFE / SUPPRESSORS ───────────────────────────────────────────────────────

SAFE_RULES: list[RegexRule] = [
    RegexRule(
        "W1",
        "STANDARD_BANKS",
        re.compile(
            r"тільки\s*(mono|monobank|privat|privatbank|пумб|а-банк|abank|моно|приват)",
            re.IGNORECASE,
        ),
        -20,
        "SAFE",
        "Стандартні вимоги по банках",
    ),
    RegexRule(
        "W2",
        "ANTI_THIRD_PARTY",
        re.compile(
            r"без\s+третіх\s+осіб|"
            r"не\s+від\s+третіх\s+осіб|"
            r"тільки\s+зі\s+своєї\s+картки|"
            r"лише\s+зі\s+своєї\s+картки|"
            r"оплата\s+лише\s+з\s+картки\s+власника|"
            r"переказ\s+тільки\s+від\s+власника",
            re.IGNORECASE,
        ),
        -40,
        "SAFE",
        "Мерчант забороняє третіх осіб",
    ),
    RegexRule(
        "W3",
        "ANTI_EXTERNAL_LINK",
        re.compile(
            r"не\s+пишіть\s+(у|в)\s+(telegram|tg|viber|whatsapp|signal)|"
            r"в\s+месенджери\s+не\s+переходжу|"
            r"спілкування\s+тільки\s+в\s+чаті\s+біржі|"
            r"тільки\s+чат\s+біржі",
            re.IGNORECASE,
        ),
        -40,
        "SAFE",
        "Мерчант забороняє зовнішні месенджери",
    ),
]


ALL_RULES: list[RegexRule] = HARD_RULES + SOFT_RULES + SAFE_RULES


def _clean_text(text: str) -> str:
    text = (text or "").lower()
    text = re.sub(r"[\u200b-\u200f\u2060\ufeff]", "", text)
    text = text.replace("ё", "е")
    text = re.sub(r"\s+", " ", text)
    return text.strip()


def _fuzzy_text(text: str) -> str:
    replacements = str.maketrans({
        "o": "о",
        "a": "а",
        "e": "е",
        "i": "і",
        "c": "с",
        "p": "р",
        "x": "х",
        "y": "у",
        "k": "к",
        "m": "м",
        "t": "т",
        "b": "в",
        "h": "н",
    })
    return text.translate(replacements)


def _excerpt(text: str, match_obj: re.Match) -> str:
    start = max(0, match_obj.start() - 20)
    end = min(len(text), match_obj.end() + 20)
    return f'"{text[start:end].strip()}"'


def _search_rule(rule: RegexRule, raw_text: str, fuzzy_text: str) -> re.Match | None:
    m = rule.pattern.search(raw_text)
    if m:
        return m
    return rule.pattern.search(fuzzy_text)


##def _dedupe_matches(matches: Iterable[RegexMatch]) -> list[RegexMatch]:
    seen: set[tuple[str, str]] = set()
    result: list[RegexMatch] = []
    for m in matches:
        key = (m.rule_id, m.excerpt)
        if key not in seen:
            seen.add(key)
            result.append(m)
    return result##


def analyze(
    trade_terms: str,
    finish_rate: float = 100.0,
    order_count: int = 0,
    is_verified: bool = False,
) -> RegexResult:
    if not trade_terms or not str(trade_terms).strip():
        return RegexResult("OK")

    raw_text = _clean_text(trade_terms)
    fuzzy = _fuzzy_text(raw_text)

    result = RegexResult("OK")
    result.normalized_text = raw_text

    # ── 1. HARD BLOCKS ────────────────────────────────────────────────────────
    for rule in HARD_RULES:
        match_obj = _search_rule(rule, raw_text, fuzzy)
        if not match_obj:
            continue

        match_data = RegexMatch(
            rule_id=rule.id,
            category=rule.category,
            excerpt=_excerpt(raw_text, match_obj),
            weight=rule.weight,
        )
        result.matches.append(match_data)
        result.score += rule.weight
        result.verdict = "BLOCK"
        result.risk_type = rule.category
        result.reason = rule.description
        return result

    # ── 2. SOFT / SAFE SIGNALS ───────────────────────────────────────────────
    score = 0
    collected: list[RegexMatch] = []

    for rule in SOFT_RULES:
        match_obj = _search_rule(rule, raw_text, fuzzy)
        if not match_obj:
            continue
        collected.append(
            RegexMatch(
                rule_id=rule.id,
                category=rule.category,
                excerpt=_excerpt(raw_text, match_obj),
                weight=rule.weight,
            )
        )
        score += rule.weight

    for rule in SAFE_RULES:
        match_obj = _search_rule(rule, raw_text, fuzzy)
        if not match_obj:
            continue
        collected.append(
            RegexMatch(
                rule_id=rule.id,
                category=rule.category,
                excerpt=_excerpt(raw_text, match_obj),
                weight=rule.weight,
            )
        )
        score += rule.weight

    result.matches = collected
    result.score = max(0, score)

    positive_matches = [m for m in collected if m.weight > 0]
    categories = list(dict.fromkeys(m.category for m in positive_matches))

    # ── 3. ESCALATION ─────────────────────────────────────────────────────────
    should_escalate = (
        result.score >= LLM_SCORE_THRESHOLD
        or (len(categories) >= 2 and result.score >= MIN_MULTI_SIGNAL_SCORE)
    )

    if should_escalate:
        result.verdict = "NEEDS_LLM"
        result.needs_llm = True
        if positive_matches:
            top_match = max(positive_matches, key=lambda x: x.weight)
            result.risk_type = top_match.category
            result.reason = (
                f"Сигналів: {len(positive_matches)} "
                f"({', '.join(categories)}) Score: {result.score}"
            )
        else:
            result.risk_type = "UNKNOWN_RISK"
            result.reason = f"Підозрілий score: {result.score}"
        return result

    # ── 4. WEAK SIGNALS WITHOUT LLM ───────────────────────────────────────────
    if positive_matches:
        top_match = max(positive_matches, key=lambda x: x.weight)
        result.risk_type = top_match.category
        result.reason = (
            f"Слабкі сигнали: {len(positive_matches)} "
            f"({', '.join(categories)}) Score: {result.score}"
        )

    return result
