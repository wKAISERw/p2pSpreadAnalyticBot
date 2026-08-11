# core/risk/matcher.py
"""
Один матчер на всі тексти.

Досі їх було три, і кожен знав про правила щось своє:

* `regex_analyzer.analyze` — умови угоди; ходив по ВСІХ правилах, зокрема
  написаних під мову відгуків;
* `review_fetcher._analyze_review_text` — тексти відгуків; поважав
  `review_only`, але не знав ні про заперечення, ні про SAFE-шар;
* `review_analyzer.analyze_review_text` — дослівна копія другого, яку не
  імпортував ніхто.

Тут один прохід із параметром `scope`, і правило про заперечення діє
однаково скрізь.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

from core.risk.registry import builtin_registry
from core.risk.signals import (
    LAYER_HARD, LAYER_SAFE, LAYER_SOFT, LAYER_WARN,
    SCOPE_TERMS, Match, Signal, SignalRegistry,
    excerpt_around, is_negated,
)

_ZERO_WIDTH = re.compile(r"[​-‏⁠﻿]")
# Горизонтальні пробіли схлопуємо, переноси рядків — НІ.
#
# Раніше тут стояло `\s+` → " ", і це було нешкідливо, поки не з'явилась
# перевірка заперечень. Мерчанти й автори відгуків пишуть списками, і після
# схлопування заперечення з наступного рядка починало стосуватись
# попереднього:
#
#     «оплатив гроші до того як скинув\nНе цінує свій та чужий час»
#
# ставало одним твердженням, і «Не» з другого рядка гасило сигнал із
# першого. Перенос рядка — така сама межа твердження, як крапка й кома.
_H_SPACES = re.compile(r"[^\S\n]+")
_MULTI_NEWLINE = re.compile(r"\n{2,}")

# Латиниця, якою маскують кирилицю: «оbнал», «kазино».
_HOMOGLYPHS = str.maketrans({
    "o": "о", "a": "а", "e": "е", "i": "і", "c": "с", "p": "р",
    "x": "х", "y": "у", "k": "к", "m": "м", "t": "т", "b": "в", "h": "н",
})


def normalize(text: str) -> str:
    text = (text or "").lower()
    text = _ZERO_WIDTH.sub("", text)
    text = text.replace("ё", "е").replace("\r", "\n")
    text = _H_SPACES.sub(" ", text)
    return _MULTI_NEWLINE.sub("\n", text).strip()


def deobfuscate(text: str) -> str:
    return text.translate(_HOMOGLYPHS)


@dataclass
class MatchResult:
    """Що знайшли в тексті — фактами, без вироку."""
    matches: list[Match] = field(default_factory=list)
    suppressed: set[str] = field(default_factory=set)   # категорії, знеструмлені SAFE
    negated: list[str] = field(default_factory=list)    # ключі сигналів під запереченням
    normalized: str = ""

    @property
    def score(self) -> int:
        return max(0, sum(m.weight for m in self.matches))

    @property
    def hard(self) -> Match | None:
        return next((m for m in self.matches if m.signal.layer == LAYER_HARD), None)

    @property
    def categories(self) -> list[str]:
        seen = {}
        for m in self.matches:
            if m.weight > 0:
                seen.setdefault(m.category, None)
        return list(seen)

    def by_layer(self, layer: str) -> list[Match]:
        return [m for m in self.matches if m.signal.layer == layer]


def _search(signal: Signal, raw: str, fuzzy: str) -> tuple[re.Match | None, str]:
    """Шукаємо в оригіналі, потім у деобфускованому варіанті."""
    m = signal.pattern.search(raw)
    if m:
        return m, raw
    m = signal.pattern.search(fuzzy)
    return (m, fuzzy) if m else (None, raw)


def match_text(
    text: str,
    scope: str = SCOPE_TERMS,
    registry: SignalRegistry | None = None,
) -> MatchResult:
    """
    Проганяє текст через сигнали, дозволені для цієї області.

    Порядок шарів має значення:
      1. SAFE — спершу, бо він вирішує, які категорії взагалі слухати;
      2. HARD — перший збіг завершує розбір;
      3. SOFT і WARN — накопичуються.
    """
    reg = registry or builtin_registry()
    result = MatchResult()
    if not text or not str(text).strip():
        return result

    raw = normalize(text)
    fuzzy = deobfuscate(raw)
    result.normalized = raw

    def hit(signal: Signal) -> Match | None:
        m, source = _search(signal, raw, fuzzy)
        if not m:
            return None
        # SAFE-сигнал сам є запереченням: «без дропів», «тільки своя картка».
        # Проганяти його через перевірку заперечень означало б заперечити
        # заперечення — і мерчант, який пише «не приймаю від третіх осіб»,
        # втрачав би саме той захист, який щойно проголосив.
        if signal.layer != LAYER_SAFE:
            # Перевіряємо на ТОМУ ТЕКСТІ, де знайшли збіг: у деобфускованому
            # варіанті зсуви інші.
            if is_negated(
                source, m.start(), m.end(), signal.negations,
                explicit_only=signal.only_explicit_negation,
            ):
                result.negated.append(signal.key)
                return None
        return Match(signal, excerpt_around(source, m.start(), m.end()), m.span())

    # ── 1. SAFE ──────────────────────────────────────────────────────────
    for signal in reg.for_scope(scope, LAYER_SAFE):
        found = hit(signal)
        if found:
            result.matches.append(found)
            result.suppressed.update(signal.suppresses)

    # ── 2. HARD ──────────────────────────────────────────────────────────
    for signal in reg.for_scope(scope, LAYER_HARD):
        if signal.category in result.suppressed:
            continue
        found = hit(signal)
        if found:
            result.matches.append(found)
            return result

    # ── 3. SOFT + WARN ───────────────────────────────────────────────────
    for layer in (LAYER_SOFT, LAYER_WARN):
        for signal in reg.for_scope(scope, layer):
            if signal.category in result.suppressed:
                continue
            found = hit(signal)
            if found:
                result.matches.append(found)

    return result
