# core/engine/risk_coverage.py
"""
Що саме ми встигли перевірити про мерчанта — одним об'єктом.

Досі покриття було розсіяне по чотирьох місцях і в трьох різних формах:

* умови — поле `order.terms_status`;
* відгуки — статус усередині зведення плюс прапори `UNKNOWN:REVIEWS:*`
  і `STALE_REVIEWS:*`;
* поведінка — **ніде**. `analyze_history` мовчки повертає порожній
  результат, коли снапшотів менше трьох, і алерт про це не каже нічого:
  «поведінка нормальна» і «історії ще немає» виглядають однаково;
* двійники — **теж ніде**, з тієї самої причини.

Через це правило «не знаю ≠ безпечно» доводилось повторювати в кожному
місці окремо, і два з чотирьох джерел про нього просто не знали.

Тут воно одне. `RiskCoverage` не судить і не рахує балів — він лише
відповідає на питання «на що ми дивились, а на що ні», і саме цю відповідь
показують людині та передають моделі.
"""
from __future__ import annotations

from dataclasses import dataclass

from core.engine import reviews_status, terms_status

# Скільки снапшотів потрібно поведінковому шару, щоб взагалі щось сказати.
# Значення дублює `behavioral_analyzer.MIN_SNAPSHOTS` навмисно: імпорт звідси
# туди створив би цикл, а розходження зловить тест.
BEHAVIOR_MIN_SNAPSHOTS = 3


def _with_subject(label: str, subject: str, subject_gen: str) -> str:
    """
    Дописує предмет до мітки, лише якщо вона його ще не назвала.

    `terms_status` і `reviews_status` формулюють мітки по-різному: одні
    самодостатні («немає сесії біржі — умов не видно»), інші ні («біржа не
    відповідає»). Другі однаково пасують і до умов, і до відгуків, тож без
    предмета читач не зрозуміє, чого саме не бачили.
    """
    if subject in label or subject_gen in label:
        return label
    return f"{subject} — {label}"


@dataclass(frozen=True, slots=True)
class RiskCoverage:
    """Межа нашої видимості на момент вердикту."""

    terms: str = terms_status.UNKNOWN
    reviews: str = reviews_status.UNKNOWN
    # Скільки снапшотів стакану мали під рукою: менше трьох — поведінкові
    # детектори мовчать не тому, що чисто, а тому, що нема з чим порівняти.
    snapshots: int = 0
    # Чи взагалі шукали клонів на інших біржах. Без імені мерчанта пошук
    # не запускається, і це не «клонів немає».
    identity_checked: bool = False
    # Чи бачили самі тексти відгуків, а не лише лічильники.
    review_texts: bool = False

    # ── Що видно ────────────────────────────────────────────────────────

    @property
    def terms_seen(self) -> bool:
        return not terms_status.is_blind(self.terms)

    @property
    def reviews_seen(self) -> bool:
        return not reviews_status.is_blind(self.reviews)

    @property
    def behavior_seen(self) -> bool:
        return self.snapshots >= BEHAVIOR_MIN_SNAPSHOTS

    @property
    def is_full(self) -> bool:
        return (
            self.terms_seen and self.reviews_seen
            and self.behavior_seen and self.identity_checked
        )

    @property
    def is_blind(self) -> bool:
        """Не бачили ні умов, ні відгуків — сказати про мерчанта нічого."""
        return not self.terms_seen and not self.reviews_seen

    # ── Як це сказати людині ────────────────────────────────────────────

    def gaps(self) -> list[str]:
        """
        Прогалини людською мовою, від важливішої до дрібнішої.

        Порядок не випадковий: умови й відгуки — це те, на чому тримається
        вердикт, а поведінка й клони лише уточнюють. Показувати їх усі
        однаково означало б зрівняти «не бачили відгуків» із «мерчант новий,
        історії ще немає».
        """
        out: list[str] = []
        # Мітки вже називають предмет («немає сесії біржі — умов не видно»),
        # тож префікс «умови — » задвоював би формулювання. Додаємо його
        # лише там, де мітка сама по собі не каже, про що йдеться:
        # «біржа не відповідає» однаково пасує і до умов, і до відгуків.
        if not self.terms_seen:
            out.append(_with_subject(terms_status.label(self.terms), "умови", "умов"))
        if not self.reviews_seen:
            out.append(_with_subject(reviews_status.label(self.reviews), "відгуки", "відгуків"))
        elif not self.review_texts:
            out.append("тексти відгуків недоступні, лише лічильники")
        if not self.behavior_seen:
            out.append(
                f"поведінка — замало історії ({self.snapshots} із "
                f"{BEHAVIOR_MIN_SNAPSHOTS} снапшотів)"
            )
        if not self.identity_checked:
            out.append("клонів на інших біржах не шукали")
        return out

    def summary(self) -> str:
        """Один рядок для вердикту. Порожній, коли перевірили все."""
        gaps = self.gaps()
        return "; ".join(gaps) if gaps else ""


def from_analysis(
    order,
    review_summary: dict | None,
    snapshots: list | None,
    identity_checked: bool,
) -> RiskCoverage:
    """
    Збирає покриття з того, що вже є під рукою в `_async_analyze_inner`.

    Нічого не запитує додатково: усі чотири джерела до цього моменту вже
    прочитані, просто досі ніхто не зводив їх разом.
    """
    summary = review_summary or {}
    return RiskCoverage(
        terms=getattr(order, "terms_status", "") or terms_status.UNKNOWN,
        reviews=summary.get("status") or reviews_status.UNKNOWN,
        snapshots=len(snapshots or []),
        identity_checked=identity_checked,
        review_texts=bool(summary.get("bad_texts")),
    )
