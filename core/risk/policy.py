# core/risk/policy.py
"""
Що робити зі знайденим сигналом — рішення, а не факт.

Межа, на якій тримається весь реворк: движок каже, ЩО побачив, а політика
каже, ЩО З ЦИМ РОБИТИ. Перше однакове для всіх і рахується один раз;
друге в кожного своє й не потребує жодного виклику моделі.

Саме тому повна кастомізація ріск-енджину не впирається ні в ключі до
моделей, ні в спільний кеш вердиктів: політика працює над уже знайденими
фактами.

**Асиметрія купівлі й продажу — не деталь, а причина існування цього
модуля.** Коли я купую USDT, фіат відправляю я, і згадка третіх осіб в
умовах мерчанта — привід придивитись. Коли я продаю, фіат відправляють
МЕНІ, і «приймаю від третіх осіб» означає, що на мою картку прилетить
переказ невідомо від кого. Один і той самий сигнал, дві різні ціни помилки.
Тому дія зберігається окремо для кожного напрямку.
"""
from __future__ import annotations

from dataclasses import dataclass, replace

from core.risk.signals import (
    LAYER_HARD, LAYER_SAFE, LAYER_SOFT, LAYER_WARN, Signal,
)

# ── Що можна зробити зі спрацьованим сигналом ────────────────────────────────
BLOCK = "block"     # не показувати ордер узагалі
WARN = "warn"       # показати з попередженням
NOTE = "note"       # згадати дрібним, без попередження
IGNORE = "ignore"   # не згадувати

ACTIONS = (BLOCK, WARN, NOTE, IGNORE)

# Наскільки дія сувора. Потрібно, щоб зводити кілька спрацювань в одне
# рішення: якщо хоч один сигнал каже «блокувати», решта вже не пом'якшить.
_SEVERITY = {IGNORE: 0, NOTE: 1, WARN: 2, BLOCK: 3}

SIDE_BUY = "buy"
SIDE_SELL = "sell"


def stricter(a: str, b: str) -> str:
    return a if _SEVERITY.get(a, 0) >= _SEVERITY.get(b, 0) else b


# ── Дефолти ──────────────────────────────────────────────────────────────────
#
# Від шару: базова суворість сигналу.
_BY_LAYER: dict[str, tuple[str, str]] = {
    LAYER_HARD: (BLOCK, BLOCK),
    LAYER_SOFT: (WARN, WARN),
    LAYER_WARN: (NOTE, NOTE),
    LAYER_SAFE: (IGNORE, IGNORE),
}

# Від категорії: там, де напрямок угоди міняє ціну помилки.
#
# Це і є та сама постановка, з якої почався реворк: «треугол на купівлю не
# так страшно, а на продаж — інше». Значення тут — дефолти; кожен користувач
# перекриває їх під себе.
_BY_CATEGORY: dict[str, tuple[str, str]] = {
    # Треті особи: купуючи, я лише обираю, кому платити; продаючи — приймаю
    # гроші невідомо від кого, і це вже мій фінмон.
    "TRIANGLE": (WARN, BLOCK),
    "THIRD_PARTY_HINT": (NOTE, WARN),
    "MIDDLEMAN": (WARN, BLOCK),
    # Куди йде платіж — стосується саме моєї купівлі: це я переказую на
    # банку й пояснюю потім банку, звідки рух. Продаючи, я нічого туди не
    # шлю.
    "PAYMENT_TARGET": (WARN, NOTE),
    # Решта не залежить від напрямку: скам є скам.
    "SCAM_REPORT": (BLOCK, BLOCK),
    "CASINO": (BLOCK, BLOCK),
    "FINCRIME": (BLOCK, BLOCK),
    "EXTERNAL_LINK": (BLOCK, BLOCK),
    "NO_COMMENTS": (BLOCK, BLOCK),
    "RECEIPT_REQUIRED": (NOTE, NOTE),
}


@dataclass(frozen=True, slots=True)
class SignalPolicy:
    """Рішення користувача про один сигнал."""

    signal_key: str
    enabled: bool = True
    on_buy: str = WARN
    on_sell: str = WARN
    weight_override: int | None = None

    def action(self, side: str) -> str:
        if not self.enabled:
            return IGNORE
        return self.on_sell if side == SIDE_SELL else self.on_buy

    def with_action(self, side: str, action: str) -> "SignalPolicy":
        if action not in ACTIONS:
            raise ValueError(f"невідома дія: {action!r}")
        field = "on_sell" if side == SIDE_SELL else "on_buy"
        return replace(self, **{field: action})


def default_policy(signal: Signal) -> SignalPolicy:
    """
    Дефолт для сигналу: спершу за категорією, інакше за шаром.

    Категорія важливіша, бо саме вона знає про напрямок угоди. Шар — лише
    запасний варіант для категорій, яких у таблиці ще немає.
    """
    on_buy, on_sell = _BY_CATEGORY.get(
        signal.category, _BY_LAYER.get(signal.layer, (WARN, WARN))
    )
    return SignalPolicy(signal.key, on_buy=on_buy, on_sell=on_sell)


# ── Профілі ──────────────────────────────────────────────────────────────────
#
# Пресет не перелічує сигнали — він зсуває суворість. Інакше кожен новий
# сигнал доводилось би дописувати в три місця, і профілі мовчки застарівали б.

PROFILE_BALANCED = "balanced"
PROFILE_CAREFUL = "careful"
PROFILE_RELAXED = "relaxed"

PROFILES: dict[str, dict] = {
    PROFILE_CAREFUL: {
        "title": "🛡 Обережний",
        "hint": "Для перших угод: усе сумнівне ховається, а не попереджає.",
        "shift": +1,
    },
    PROFILE_BALANCED: {
        "title": "⚖️ Збалансований",
        "hint": "Дефолт: блокуються тільки явні речі, решта з попередженням.",
        "shift": 0,
    },
    PROFILE_RELAXED: {
        "title": "⚡ Агресивний",
        "hint": "Показує майже все; рішення за вами. Менше пропущених угод, більше ризику.",
        "shift": -1,
    },
}

_LADDER = (IGNORE, NOTE, WARN, BLOCK)


def _shift(action: str, steps: int) -> str:
    if steps == 0 or action not in _LADDER:
        return action
    i = max(0, min(len(_LADDER) - 1, _LADDER.index(action) + steps))
    return _LADDER[i]


def apply_profile(policy: SignalPolicy, profile: str) -> SignalPolicy:
    """
    Зсуває суворість політики на крок вгору або вниз.

    Профіль НЕ чіпає вимкнені сигнали: якщо людина вимкнула сигнал руками,
    зміна профілю не має його воскресити.
    """
    shift = PROFILES.get(profile, {}).get("shift", 0)
    if not shift or not policy.enabled:
        return policy
    return replace(
        policy,
        on_buy=_shift(policy.on_buy, shift),
        on_sell=_shift(policy.on_sell, shift),
    )


class PolicyResolver:
    """
    Дефолт сигналу → профіль користувача → його точкове налаштування.

    Порожні налаштування дають рівно теперішню поведінку — саме тому
    міграція нікому нічого не ламає.
    """

    def __init__(self, profile: str = PROFILE_BALANCED,
                 overrides: dict[str, SignalPolicy] | None = None):
        self.profile = profile if profile in PROFILES else PROFILE_BALANCED
        self.overrides = overrides or {}

    def for_signal(self, signal: Signal) -> SignalPolicy:
        override = self.overrides.get(signal.key)
        if override is not None:
            # Точкове налаштування — остаточне. Профіль його не зсуває:
            # людина вже сказала, чого хоче саме тут.
            return override
        return apply_profile(default_policy(signal), self.profile)

    def action_for(self, signal: Signal, side: str) -> str:
        return self.for_signal(signal).action(side)

    def decide(self, signals: list[Signal], side: str) -> tuple[str, list[Signal]]:
        """
        Одне рішення на весь набір знайдених сигналів.

        Повертає (дія, сигнали_що_її_спричинили). Найсуворіша дія перемагає:
        один «блокувати» не пом'якшується десятьма «згадати».
        """
        verdict = IGNORE
        causes: list[Signal] = []
        for signal in signals:
            action = self.action_for(signal, side)
            if action == IGNORE:
                continue
            if _SEVERITY[action] > _SEVERITY[verdict]:
                verdict, causes = action, [signal]
            elif action == verdict:
                causes.append(signal)
        return verdict, causes
