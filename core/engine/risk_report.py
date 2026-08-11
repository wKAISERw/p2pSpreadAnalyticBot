# core/engine/risk_report.py
"""
Прапори ордера як структура — друга половина етапу 2.

`order.risk_flag` — рядок, склеєний комами:

    "LOW_STATS,BLOCK:TRIANGLE:треті особи,STALE_REVIEWS:NO_SESSION:30h"

`risk_flags.py` навчив читати його як список замість підрядка. Але список
рядків усе одно доводиться розбирати на місці, і кожен читач робить це
по-своєму: двадцять один `startswith` по шести файлах. Найдорожче тут не
дублювання, а те, що новий вид прапора мовчки провалюється повз усі гілки —
рівно так `UNKNOWN:REVIEWS:*` півроку не показувався людині взагалі.

Тут прапор стає `Finding`: вид, категорія, деталь. Один розбір, і новий вид
має куди подітись — `kind` лишається сирим префіксом, а не зникає.

**Сховище лишається рядком свідомо.** `risk_flag` серіалізується в базу
разом з алертами, віддається дашборду й читається зі старих записів.
Замінити його на структуру означало б міграцію плюс переписування всіх
читачів одним заходом. Тому `RiskReport` — це **похідний вигляд**, який
нічого не зберігає: єдине джерело правди лишається одне.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from core.engine import risk_flags

# ── Види прапорів ────────────────────────────────────────────────────────────
#
# Не «severity»: те, наскільки це страшно, вирішує персональна політика, а не
# движок. Тут лише про ПРИРОДУ факту.
BLOCK = "block"          # рішення движка: не показувати нікому
RISK = "risk"            # знайдений ризик у тексті або поведінці
REVIEWS = "reviews"      # щось про відгуки
BLIND = "blind"          # ми чогось НЕ бачили — не факт про мерчанта
PENDING = "pending"      # перевірка ще не завершилась
META = "meta"            # метадані для персональних фільтрів (ФОП, банка)
INFO = "info"            # решта


# Префікс → вид. Порядок важливий: перший збіг перемагає, тож довші
# префікси мусять стояти вище.
_KINDS: tuple[tuple[str, str], ...] = (
    ("BLOCK", BLOCK),
    ("UNKNOWN:", BLIND),
    ("STALE_REVIEWS", BLIND),
    ("NEEDS_LLM:BADREVIEWS", REVIEWS),
    ("BADREVIEWS", REVIEWS),
    ("REVIEW_SOFT", REVIEWS),
    ("REVIEW_UNFLAGGED", REVIEWS),
    ("LLM_PENDING", PENDING),
    ("LLM_UNKNOWN", PENDING),
    ("LLM_SUSPICIOUS", RISK),
    ("LLM_BLOCK", RISK),
    ("REGEX_WEAK", RISK),
    ("BEHAVIOR", RISK),
    ("HIGH_RISK_SCORE", RISK),
    ("FOP_TOV_", META),
    ("BANKA_JAR_", META),
)


@dataclass(frozen=True, slots=True)
class Finding:
    """Один прапор, розібраний на частини."""

    raw: str
    kind: str = INFO
    # Категорія ризику (TRIANGLE, CASINO…) або причина сліпоти
    # (NO_SESSION, API_ERROR) — залежно від виду.
    subject: str = ""
    # Хвіст: цитата з умов, пояснення моделі, вік даних. Може містити «:»,
    # тому ріжемо рівно двічі.
    detail: str = ""

    @property
    def is_blind(self) -> bool:
        return self.kind == BLIND

    @property
    def is_block(self) -> bool:
        return self.kind == BLOCK

    @property
    def what(self) -> str:
        """
        Чого саме не бачили — для прапорів сліпоти.

        Два прапори про одне й те саме написані по-різному:
        `UNKNOWN:REVIEWS:NO_SESSION` кладе предмет у другу позицію, а
        `STALE_REVIEWS:NO_SESSION:30h` — у саму назву. Читач не повинен
        знати про цю різницю; вона тут і закінчується.
        """
        if not self.is_blind:
            return ""
        if self.raw.startswith("STALE_REVIEWS"):
            return "REVIEWS"
        return self.subject

    @property
    def why(self) -> str:
        """Причина сліпоти: NO_SESSION, API_ERROR і подібне."""
        if not self.is_blind:
            return ""
        if self.raw.startswith("STALE_REVIEWS"):
            return self.subject
        return self.detail.split(":")[0] if self.detail else ""


def _classify(part: str) -> str:
    for prefix, kind in _KINDS:
        if part == prefix or part.startswith(prefix):
            return kind
    return INFO


def parse_finding(part: str) -> Finding:
    """
    Розбирає один прапор.

    Формат `ВИД:ПРЕДМЕТ:деталь`, де хвіст може містити двокрапки — тому
    `split(":", 2)`, а не по всіх. Прапор без частин теж законний
    («LOW_STATS»), і вигадувати йому предмет не треба.
    """
    part = (part or "").strip()
    head, _, rest = part.partition(":")
    subject, _, detail = rest.partition(":")
    return Finding(raw=part, kind=_classify(part), subject=subject, detail=detail)


@dataclass(frozen=True, slots=True)
class RiskReport:
    """Усі прапори ордера, розібрані один раз."""

    findings: tuple[Finding, ...] = field(default_factory=tuple)

    @classmethod
    def from_flag(cls, risk_flag: str | None) -> RiskReport:
        return cls(tuple(parse_finding(p) for p in risk_flags.parse(risk_flag)))

    @classmethod
    def from_order(cls, order) -> RiskReport:
        return cls.from_flag(getattr(order, "risk_flag", ""))

    def of_kind(self, kind: str) -> list[Finding]:
        return [f for f in self.findings if f.kind == kind]

    @property
    def blind(self) -> list[Finding]:
        """
        Чого ми не бачили. Найважливіший зріз і найлегший для втрати.

        «Не перевірили» — не пом'якшений ризик і не його відсутність. Саме
        через те, що ці прапори не мали власної гілки в рендері, движок
        чесно писав «відгуків не бачили», а людина цього не бачила ніде.
        """
        return self.of_kind(BLIND)

    @property
    def risks(self) -> list[Finding]:
        return self.of_kind(RISK) + self.of_kind(BLOCK)

    @property
    def categories(self) -> list[str]:
        """Категорії ризику без повторів, у порядку появи."""
        return list(dict.fromkeys(
            f.subject for f in self.risks if f.subject
        ))

    @property
    def has_block(self) -> bool:
        return any(f.is_block for f in self.findings)

    @property
    def is_quiet(self) -> bool:
        """
        Жодного прапора взагалі.

        Не «чисто»: тиша означає лише, що нічого не спрацювало. Що саме
        встигли перевірити, каже `order.risk_coverage`, і плутати ці дві
        відповіді — головна помилка, від якої чиститься весь реворк.
        """
        return not self.findings
