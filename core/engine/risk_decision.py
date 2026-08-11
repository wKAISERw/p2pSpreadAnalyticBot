# core/engine/risk_decision.py
"""
Персональне рішення про ордер: показати, попередити чи сховати.

Місце, де сходяться дві половини реворку. Движок уже сказав, ЩО побачив
(`order.risk_signals`, `order.risk_coverage`); користувач уже сказав, ЩО З
ЦИМ РОБИТИ (`RiskRepo.resolver_for`). Тут це зводиться в одне слово.

Дотепер таких рішень було два з половиною, і всі захардкожені: два
персональні фільтри (ФОП/ТОВ і банка) в `alert_builder` і `_user_wants`,
плюс «будь-який BLOCK ховаємо». Категорій у реєстрі шістнадцять, тож
чотирнадцять із них налаштувати було неможливо — вони або блокували всіх,
або нікого.

Напрямок угоди тут не параметр «про всяк випадок», а суть: `order.side`
каже, купує людина чи продає, і та сама згадка третіх осіб коштує різного.
"""
from __future__ import annotations

from dataclasses import dataclass

from core.risk.policy import (
    BLOCK, IGNORE, NOTE, SIDE_BUY, SIDE_SELL, WARN, PolicyResolver,
)
from core.risk.registry import CATEGORY_TITLES, builtin_registry


@dataclass(slots=True)
class Decision:
    """Що робити з ордером і чому саме."""
    action: str = IGNORE
    reasons: list[str] = None          # людською мовою, для алерта
    signals: list[str] = None          # ключі — для логів і діагностики

    def __post_init__(self):
        self.reasons = self.reasons or []
        self.signals = self.signals or []

    @property
    def hide(self) -> bool:
        return self.action == BLOCK

    @property
    def warn(self) -> bool:
        return self.action == WARN


def side_of(order) -> str:
    """
    Купує людина чи продає — з точки зору КОРИСТУВАЧА.

    `Order.side` проставляє сканер: "buy" для ордерів, де мерчант продає
    USDT (тобто людина купує й відправляє фіат), "sell" — навпаки. Плутати
    ці дві системи координат тут найлегше й найдорожче: помилка перевертає
    всю асиметрію догори дриґом.
    """
    return SIDE_SELL if getattr(order, "side", "") == "sell" else SIDE_BUY


def _as_list(value) -> list[str]:
    """Список рядків із того, що прийшло: рядок — це один елемент, не набір літер."""
    if not value:
        return []
    if isinstance(value, str):
        return [value]
    return [str(v) for v in value]


def _custom_matches(order, resolver: PolicyResolver) -> list:
    """
    Власні сигнали користувача — прикладені до тексту саме тут.

    Спільний прохід движка їх не бачить і бачити не може: він рахує факти
    один раз на всіх, а це правило написала одна людина. Тому текст
    проганяється вдруге — але лише по її власних сигналах, яких одиниці, і
    лише тоді, коли вони взагалі є.

    Умови й відгуки розводяться за `scope` не для акуратності: правило
    «якщо в тексті є слово „скам“» на умовах мерчанта означає «він пише, що
    скаму не буде», а на відгуках — «його називають скамером». Одне слово,
    протилежний зміст.
    """
    if not resolver.has_custom:
        return []

    from core.risk.matcher import match_text
    from core.risk.signals import SCOPE_REVIEWS, SCOPE_TERMS

    registry = resolver.custom_registry()
    out: list = []
    seen: set[str] = set()
    sources = (
        (SCOPE_TERMS, getattr(order, "trade_terms", "") or ""),
        # `or []` мало б вистачати, але ордер їздить через серіалізацію в
        # базу й назад, а звідти поле може повернутись рядком. `"\n".join`
        # на рядку не падає — він мовчки склеює його ПОСИМВОЛЬНО, і сигнал
        # почав би спрацьовувати на випадкових збігах.
        (SCOPE_REVIEWS, "\n".join(_as_list(getattr(order, "review_texts", None)))),
    )
    for scope, text in sources:
        if not text.strip():
            continue
        for m in match_text(text, scope, registry=registry).matches:
            if m.signal.key not in seen:
                seen.add(m.signal.key)
                out.append(m.signal)
    return out


def decide(order, resolver: PolicyResolver, side: str | None = None) -> Decision:
    """
    Рішення по одному ордеру під конкретного користувача.

    Порожній список сигналів — не «безпечно», а «нічого не спрацювало».
    Про те, чого ми не бачили, каже `order.risk_coverage`, і це окрема
    розмова: рішення про показ і чесність про покриття — різні речі.
    """
    keys = list(getattr(order, "risk_signals", None) or [])
    registry = builtin_registry()
    signals = [s for s in (registry.by_key(k) for k in keys) if s is not None]
    signals += _custom_matches(order, resolver)
    if not signals:
        return Decision()

    action, causes = resolver.decide(signals, side or side_of(order))
    return Decision(
        action=action,
        # Назва сигналу конкретніша за назву категорії: «Банка /
        # накопичувальний рахунок» каже людині, ЩО саме знайшли, а «Куди
        # саме йде платіж» — лише в якому розділі це шукати.
        reasons=[s.title or CATEGORY_TITLES.get(s.category, s.category) for s in causes],
        signals=[s.key for s in causes],
    )


def decide_pair(buy_order, sell_order, resolver: PolicyResolver) -> Decision:
    """
    Рішення по зв'язці: найсуворіше з двох ніг.

    Ноги оцінюються В РІЗНИХ напрямках — на buy_order людина купує, на
    sell_order продає. Один resolver, дві різні відповіді, і саме тому
    зв'язку не можна звести до одного виклику.
    """
    buy = decide(buy_order, resolver, SIDE_BUY)
    sell = decide(sell_order, resolver, SIDE_SELL)

    from core.risk.policy import stricter

    action = stricter(buy.action, sell.action)
    reasons: list[str] = []
    signals: list[str] = []
    for leg, label in ((buy, "купівля"), (sell, "продаж")):
        if leg.action == action and leg.reasons:
            reasons += [f"{r} ({label})" for r in leg.reasons]
            signals += leg.signals
    return Decision(action=action, reasons=reasons, signals=signals)
