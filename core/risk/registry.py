# core/risk/registry.py
"""
Вбудований реєстр сигналів.

Збирається з двох джерел, і поділ між ними принциповий:

* **`core/analysis/rules.py`** дає ПАТЕРНИ. Вони перевірені живим потоком,
  і переписувати їх заразом зі зміною архітектури означало б змішати два
  ризики в одній зміні. Беремо як є.
* **`tools/antifrod_*.json`** дає ЗНАННЯ про ці патерни: чому це ризик
  (`why_risky`), коли він не ризик (`when_not_risky`), як звучить
  заперечення (`negative_safe_forms`), приклади в обидва боки,
  впевненість. Усе це існувало з самого початку і втрачалось при
  генерації — `build_rules.py` брав лише регекс і вагу.

Тобто JSON-и нарешті стають тим, чим виглядали: джерелом правди про
предметну область, а не мертвим входом зламаного генератора.

Плюс те, чого не було в жодному з двох джерел: `PAYMENT_TARGET` —
накопичувальні рахунки й бізнес-реквізити (див. `vocabulary.py`).
"""
from __future__ import annotations

import json
import logging
from functools import lru_cache
from pathlib import Path

from core.analysis.rules import HARD_RULES, SAFE_RULES, SOFT_RULES, WARN_RULES
from core.risk import vocabulary as vocab
from core.risk.signals import (
    LAYER_HARD, LAYER_SAFE, LAYER_SOFT, LAYER_WARN,
    SCOPE_BOTH, SCOPE_REVIEWS, SCOPE_TERMS,
    Signal, SignalRegistry, compile_phrases,
)

logger = logging.getLogger("RiskRegistry")

_JSON_DIR = Path(__file__).resolve().parents[2] / "tools"
_JSON_FILES = (
    "antifrod_concepts.json",
    "antifrod_trade_terms.json",
    "antifrod_reviews.json",
)

# Людські назви категорій. Без них у налаштуваннях (етап 4) стояли б
# EXTERNAL_LINK і THIRD_PARTY_HINT, а це мова коду, не мова людини.
CATEGORY_TITLES: dict[str, str] = {
    "EXTERNAL_LINK": "Виведення в зовнішній месенджер",
    "TRIANGLE": "Треті особи та дропи",
    "THIRD_PARTY_HINT": "Двозначна згадка третіх осіб",
    "NO_COMMENTS": "Заборона коментаря до переказу",
    "CASINO": "Казино, ставки, процесинг",
    "FINCRIME": "Обнал, фінмон, сірі схеми",
    "CHARGEBACK": "Погроза рефандом або чарджбеком",
    "CHAT_FIRST": "Просить написати до оплати",
    "APPEAL_PRESSURE": "Тиск апеляцією",
    "ANONYMOUS": "Анонімність, без перевірок",
    "MIDDLEMAN": "Посередник або номінал",
    "SUSPICIOUS_BIZ": "Підозрілий бізнес-контекст",
    "RECEIPT_REQUIRED": "Вимога квитанції",
    "PAYMENT_TARGET": "Куди саме йде платіж",
    "SCAM_REPORT": "Пряме звинувачення у відгуку",
    "SAFE": "Захисні формулювання мерчанта",
}

# Категорії, які мають сенс ЛИШЕ в текстах відгуків. У полі умов те саме
# слово пише сам мерчант — найчастіше щоб від нього відхреститись.
_REVIEW_LAYER_NOTE = "review_only"


@lru_cache(maxsize=1)
def _knowledge() -> dict[str, dict]:
    """
    Метадані по категоріях із JSON-ів.

    Читається один раз. Якщо файлів немає (наприклад, урізаний деплой),
    реєстр збирається без метаданих — патерни важливіші за пояснення.
    """
    out: dict[str, dict] = {}
    for name in _JSON_FILES:
        path = _JSON_DIR / name
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except Exception as e:
            logger.debug("Метадані %s недоступні: %s", name, e)
            continue

        items = data.get("concepts") or data.get("candidates") or []
        for item in items:
            cat = item.get("category")
            if not cat:
                continue
            slot = out.setdefault(cat, {
                "why": "", "negations": [], "risky": "", "safe": "", "confidence": 0.0,
            })
            if not slot["why"] and item.get("why_risky"):
                slot["why"] = item["why_risky"].strip()
            # `negative_safe_forms` (концепти) і `suppress_hard` (кандидати) —
            # два імені для одного: формулювань, за яких збіг не є ризиком.
            for key in ("negative_safe_forms", "suppress_hard"):
                for phrase in item.get(key) or []:
                    if isinstance(phrase, str) and phrase.strip():
                        slot["negations"].append(phrase.strip().lower())
            if not slot["risky"] and item.get("example_negative"):
                slot["risky"] = item["example_negative"].strip()
            if not slot["safe"]:
                slot["safe"] = (item.get("example_positive") or item.get("when_not_risky") or "").strip()
            slot["confidence"] = max(slot["confidence"], float(item.get("confidence") or 0.0))

    for slot in out.values():
        slot["negations"] = tuple(dict.fromkeys(slot["negations"]))
    return out


def _scope_for(rule) -> str:
    """
    Де правилу дозволено дивитись.

    `review_only=True` у згенерованому `rules.py` означало «написано під
    мову відгуків». `review_fetcher` це поважав, `regex_analyzer` — ні, і
    18 із 25 SOFT-правил судили умови угоди чужою міркою (баг 2.2).
    Тепер область — властивість сигналу, а не домовленість між модулями.
    """
    return SCOPE_REVIEWS if getattr(rule, _REVIEW_LAYER_NOTE, False) else SCOPE_TERMS


def _from_rule(rule, layer: str) -> Signal:
    meta = _knowledge().get(rule.category, {})
    negations = list(meta.get("negations", ()))

    # `suppress_hard` у SAFE-правилах містить НАЗВИ КАТЕГОРІЙ, які треба
    # глушити, а не фрази. У HARD/SOFT — навпаки, фрази. Одне поле, два
    # різні змісти; тут вони нарешті роз'їжджаються.
    raw_suppress = getattr(rule, "suppress_hard", None) or frozenset()
    if layer == LAYER_SAFE:
        suppresses = frozenset(raw_suppress)
    else:
        suppresses = frozenset()
        negations.extend(p.lower() for p in raw_suppress if isinstance(p, str))

    return Signal(
        key=rule.id,
        category=rule.category,
        title=CATEGORY_TITLES.get(rule.category, rule.category),
        pattern=rule.pattern,
        layer=layer,
        weight=rule.weight,
        scope=_scope_for(rule),
        why=meta.get("why", "") or (rule.description or "").strip(),
        negations=tuple(dict.fromkeys(negations)),
        suppresses=suppresses,
        example_risky=meta.get("risky", ""),
        example_safe=meta.get("safe", ""),
        confidence=meta.get("confidence", 0.0),
    )


def _payment_target_signals() -> list[Signal]:
    """
    Накопичувальні рахунки й бізнес-реквізити — категорія, якої не було.

    Досі це жило двома захардкодженими патернами в `regex_analyzer`
    (`BANKA_JAR_PATTERN`, `FOP_TOV_PATTERN`) поза будь-яким реєстром, зі
    своїм окремим шляхом перевірки заперечень. Патерни були дірявими саме
    там, де люди пишуть найчастіше: «приймаю на монобанку» і «а-банк збір»
    не ловились.
    """
    out = []
    jar = compile_phrases(vocab.JAR_TERMS)
    if jar:
        out.append(Signal(
            key="PAY_JAR",
            category="PAYMENT_TARGET",
            title="Банка / накопичувальний рахунок",
            pattern=jar,
            layer=LAYER_WARN,
            weight=0,
            scope=SCOPE_BOTH,
            why=vocab.JAR_WHY,
            negations=vocab.JAR_NEGATIONS,
            example_risky="кидаю на монобанку і конверт приват",
            example_safe="тільки на картку, без банок",
            confidence=0.9,
        ))
    scam = compile_phrases(vocab.SCAM_ACCUSATION_TERMS)
    if scam:
        out.append(Signal(
            key="REVIEW_SCAM_CLAIM",
            category="SCAM_REPORT",
            title="Пряме звинувачення в шахрайстві",
            pattern=scam,
            layer=LAYER_SOFT,
            # Вага як у критичних review-правил: свідчення людини, яка вже
            # торгувала з цим мерчантом, важить не менше за збіг патерна.
            weight=100,
            scope=SCOPE_REVIEWS,
            why=vocab.SCAM_WHY,
            negations=vocab.SCAM_ACCUSATION_NEGATIONS,
            example_risky="кинув на 5000, скам",
            example_safe="мерчант не кидає, все чесно",
            confidence=0.95,
        ))

    biz = compile_phrases(vocab.BUSINESS_TERMS)
    if biz:
        out.append(Signal(
            key="PAY_BUSINESS",
            category="PAYMENT_TARGET",
            title="ФОП / ТОВ замість картки",
            pattern=biz,
            layer=LAYER_WARN,
            weight=0,
            scope=SCOPE_BOTH,
            why=vocab.BUSINESS_WHY,
            negations=vocab.BUSINESS_NEGATIONS,
            example_risky="оплата на рахунок ФОП",
            example_safe="тільки фізособа, не фоп",
            confidence=0.85,
        ))
    return out


@lru_cache(maxsize=1)
def builtin_registry() -> SignalRegistry:
    """Вбудовані сигнали. Незмінні; персональні надбудовуються поверх."""
    signals: list[Signal] = []
    for rules, layer in (
        (HARD_RULES, LAYER_HARD),
        (SOFT_RULES, LAYER_SOFT),
        (WARN_RULES, LAYER_WARN),
        (SAFE_RULES, LAYER_SAFE),
    ):
        signals.extend(_from_rule(r, layer) for r in rules)

    signals.extend(_payment_target_signals())
    registry = SignalRegistry(signals)
    logger.info(
        "Реєстр сигналів: %d усього, %d для умов, %d для відгуків, категорій %d",
        len(signals),
        sum(1 for s in signals if s.applies_to(SCOPE_TERMS)),
        sum(1 for s in signals if s.applies_to(SCOPE_REVIEWS)),
        len(registry.categories()),
    )
    return registry
