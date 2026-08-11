# core/risk/signals.py
"""
Модель сигналу ризику — одна на всі шари.

До цього правило описувалось `RegexRule` у згенерованому `rules.py`: id,
категорія, скомпільований патерн, вага, дія. Усе, що робить правило
зрозумілим людині — чому це ризик, як воно звучить у житті, коли воно
НЕ ризик — лишалось у `tools/antifrod_*.json` і втрачалось при генерації.

`Signal` збирає це докупи й додає те, чого не було зовсім:

* **`negations`** — формулювання, за яких збіг НЕ означає ризику. Досі
  заперечення жило в `suppress_hard` як набір фраз, вгаданих наперед, і
  працювало лише на них. Через це «не працюю з дропами, не приймаю обнал»
  давало BLOCK FINCRIME: у списку були «без обналу» і «не обнал», а
  «не приймаю обнал» ніхто не передбачив.
* **`scope`** — де сигналу дозволено спрацьовувати. «Кинув», «шахрай»,
  «дроп» у відгуку пише потерпілий; у полі умов те саме слово пише сам
  мерчант, найчастіше щоб від цього відхреститись.
* **`owner`** — `builtin` або `user:<id>`. Вбудовані сигнали не мутуються
  ніколи; персональні надбудовуються над ними (етап 3 плану).

Патерни навмисно лишились ті самі, що були в `rules.py`. Вони перевірені
живим потоком, і переписувати їх «красивіше» разом зі зміною архітектури
означало б змішати два ризики в одній зміні.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

# ── Де сигнал має право спрацьовувати ────────────────────────────────────────
SCOPE_TERMS = "terms"
SCOPE_REVIEWS = "reviews"
SCOPE_BOTH = "both"

# ── Що з ним робити ──────────────────────────────────────────────────────────
LAYER_HARD = "hard"    # збіг = блок без обговорення
LAYER_SOFT = "soft"    # бали, ескалація в модель
LAYER_WARN = "warn"    # окрема позначка, не впливає на score
LAYER_SAFE = "safe"    # від'ємні бали, глушить категорії зі `suppresses`


@dataclass(frozen=True, slots=True)
class Signal:
    key: str
    category: str
    title: str
    pattern: re.Pattern
    layer: str = LAYER_SOFT
    weight: int = 0
    scope: str = SCOPE_TERMS

    # Чому це ризик — людською мовою. Іде в алерт і в промпт моделі.
    why: str = ""
    # Коли збіг НЕ означає ризику: словами, а не регексом.
    negations: tuple[str, ...] = ()
    # Категорії, які цей сигнал глушить (для SAFE-шару).
    suppresses: frozenset = frozenset()
    # Приклади в обидва боки — для тестів і для пояснення людині.
    example_risky: str = ""
    example_safe: str = ""
    confidence: float = 0.0
    owner: str = "builtin"
    enabled: bool = True

    def applies_to(self, scope: str) -> bool:
        return self.scope in (scope, SCOPE_BOTH)


# ─────────────────────────────────────────────────────────────────────────────
# Компіляція людських фраз у патерн
# ─────────────────────────────────────────────────────────────────────────────

def compile_phrases(phrases: list[str] | tuple[str, ...]) -> re.Pattern | None:
    """
    Фрази → один патерн.

    Користувач у боті (етап 4) пише «кидаю на банку», а не
    `\\bбань?к[ауи]\\b`. Робота движка — перетворити перше на друге:
    пробіли стають гнучкими роздільниками, решта екранується.

    Порожній список дає None, а не патерн, що збігається з усім, — інакше
    сигнал без фраз мовчки блокував би геть усе.
    """
    parts = []
    for phrase in phrases:
        text = (phrase or "").strip().lower()
        if not text:
            continue
        # Пробіл у фразі → «пробіл або будь-який роздільник»: мерчанти
        # пишуть і «на банку», і «на  банку», і «на-банку».
        chunks = [re.escape(w) for w in text.split()]
        body = r"\s*[\s\-_.,]?\s*".join(chunks)

        # Межі слова обов'язкові. Без них «банка» ловилось усередині
        # «банками», а «збір» — усередині «збірка документів», і категорія
        # PAYMENT_TARGET спрацьовувала на звичайних реченнях про банки.
        #
        # Ціна відома: злите написання треба перелічувати явно. Саме тому в
        # словнику поруч стоять і «банку», і «монобанку» — це не дублікат,
        # а два різні слова. Пропустити збіг дешевше, ніж вигадати його:
        # словник — дані, і його доповнюють із практики.
        if body[:1].isalnum() or text[:1].isalnum():
            body = r"\b" + body
        if text[-1:].isalnum():
            body = body + r"\b"
        parts.append(body)
    if not parts:
        return None
    return re.compile("|".join(f"(?:{p})" for p in parts), re.IGNORECASE)


# ─────────────────────────────────────────────────────────────────────────────
# Заперечення
# ─────────────────────────────────────────────────────────────────────────────

# Загальні заперечні конструкції. Раніше кожне правило носило власний
# список вгаданих фраз, і «не приймаю обнал» повз нього проходило.
_NEGATION_HEAD = re.compile(
    r"\b(?:не|ні|нє|без|нема[єю]?|не\s*прийма\w*|не\s*працю\w*|не\s*бер\w*|"
    r"заборонен\w*|запрещен\w*|виключен\w*|відмовл\w*|no|not|without)\b",
    re.IGNORECASE,
)

# Скільки символів навколо збігу ще вважається тим самим твердженням.
# «не приймаю обнал» — 10 символів перед; «обнал не беру» — 9 після.
_NEGATION_WINDOW = 40
_NEGATION_TAIL_WINDOW = 25

# Кома тут теж межа, і це не дрібниця. «не приймаю дропів, обнал ок» —
# заперечення стосується дропів, а не обналу, і без коми в списку меж ми б
# пропустили справжній ризик, прийнявши його за застереження.
_CLAUSE_BOUNDARIES = ".!?;,\n"


def is_negated(text: str, start: int, end: int, extra: tuple[str, ...] = ()) -> bool:
    """
    Чи стоїть збіг під запереченням.

    Три перевірки, саме в такому порядку:

    1. Точні формулювання сигналу (`negations` / `negative_safe_forms` з
       JSON) — вони найнадійніші, бо їх писала людина під конкретний випадок.
    2. Заперечна конструкція перед збігом: «не приймаю обнал».
    3. Заперечна конструкція після збігу: «обнал не беру». Українська
       дозволяє винести додаток наперед, і без цієї перевірки половина
       застережень читалась би як зізнання.

    Межа речення (і коми) відсікає чуже заперечення: «дропи заборонені.
    обнал приймаю» — блок, а не застереження.
    """
    lowered = text.lower()

    for phrase in extra:
        if phrase and phrase.lower() in lowered:
            return True

    head = lowered[max(0, start - _NEGATION_WINDOW):start]
    cut = max(head.rfind(c) for c in _CLAUSE_BOUNDARIES)
    if cut != -1:
        head = head[cut + 1:]
    if _NEGATION_HEAD.search(head):
        return True

    tail = lowered[end:end + _NEGATION_TAIL_WINDOW]
    stops = [tail.find(c) for c in _CLAUSE_BOUNDARIES if tail.find(c) != -1]
    if stops:
        tail = tail[:min(stops)]
    return bool(_NEGATION_HEAD.search(tail))


@dataclass(slots=True)
class Match:
    """Один спрацьований сигнал із доказом."""
    signal: Signal
    excerpt: str
    span: tuple[int, int]

    @property
    def weight(self) -> int:
        return self.signal.weight

    @property
    def category(self) -> str:
        return self.signal.category


def excerpt_around(text: str, start: int, end: int, pad: int = 20) -> str:
    lo = max(0, start - pad)
    hi = min(len(text), end + pad)
    return text[lo:hi].strip()


@dataclass
class SignalRegistry:
    """
    Набір сигналів із дешевою вибіркою за шаром і областю.

    Фільтри рахуються один раз при створенні: `match` викликається на кожен
    ордер кожного циклу, і перебирати там усі сигнали заново — марна робота.
    """
    signals: list[Signal] = field(default_factory=list)

    def __post_init__(self):
        self._by_scope: dict[tuple[str, str], list[Signal]] = {}
        for scope in (SCOPE_TERMS, SCOPE_REVIEWS):
            for layer in (LAYER_HARD, LAYER_SOFT, LAYER_WARN, LAYER_SAFE):
                self._by_scope[(scope, layer)] = [
                    s for s in self.signals
                    if s.enabled and s.layer == layer and s.applies_to(scope)
                ]

    def for_scope(self, scope: str, layer: str) -> list[Signal]:
        return self._by_scope.get((scope, layer), [])

    def by_key(self, key: str) -> Signal | None:
        return next((s for s in self.signals if s.key == key), None)

    def categories(self) -> set[str]:
        return {s.category for s in self.signals}
