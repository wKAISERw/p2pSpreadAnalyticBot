# core/engine/bank_discovery.py
"""
Банки, які біржі віддають, а реєстр не знає.

Питання «а що таке код 545?» не потребує здогадок: біржа присилає назву
платіжного методу в тому самому payload, з якого ми беремо код. Просто
парсери назву викидають.

Наслідки різні залежно від біржі, і обидва погані по-своєму:

* **Bybit** кладе сирий `paymentType` без мапінгу. Коди, що збігаються з
  внутрішніми (43, 14, 64…), працюють випадково; решта стає
  псевдобанком — під код 545 картка не знайдеться ніколи, а в статистиці
  це виглядає як «немає активних карток»;
* **Binance і OKX** мапять через `BankRegistry` і невідоме мовчки
  **викидають**. Ордер лишається, але з коротшим списком банків — тобто
  ми можемо відкинути ордер, який насправді приймає наш банк.

Тут ці випадки не лікуються (це вимагало б доповнити реєстр), а
**фіксуються**: що саме прийшло, з якої біржі та як воно називається.
Далі людина бачить це в /checkup і каже, який це банк, — після чого
рядок у `config/banks.py` пишеться з реальними даними, а не з гадання.

Стан живе в пам'яті: сканер обходить стакан щохвилини, тож після
рестарту список наповнюється заново за один цикл. Таблиця заради цього
не потрібна.
"""
from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field

logger = logging.getLogger(__name__)

# Ключі, під якими біржі кладуть людську назву методу оплати. Порядок —
# від найточнішого до найзагальнішого.
_NAME_KEYS = (
    "paymentName", "bankName", "tradeMethodName", "payTypeName",
    "name", "identifier", "payMethodName",
)

# Скільки різних невідомих кодів тримаємо. Захист від сміттєвого payload:
# без межі один зламаний парсер роздув би словник на весь стакан.
_MAX_TRACKED = 200


@dataclass
class UnknownBank:
    exchange: str
    code: str
    names: set[str] = field(default_factory=set)
    hits: int = 0
    first_seen: float = 0.0
    last_seen: float = 0.0

    @property
    def title(self) -> str:
        return " / ".join(sorted(self.names)) if self.names else "назва невідома"


_seen: dict[tuple[str, str], UnknownBank] = {}


def extract_name(payload) -> str:
    """Людська назва методу оплати з сирого об'єкта біржі, або ''."""
    if isinstance(payload, str):
        return payload.strip()
    if not isinstance(payload, dict):
        return ""

    for key in _NAME_KEYS:
        value = payload.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()

    # Bybit ховає назву на рівень глибше, у paymentConfigVo.
    nested = payload.get("paymentConfigVo")
    if isinstance(nested, dict):
        return extract_name(nested)
    return ""


def note(exchange: str, code: str, payload=None) -> None:
    """Запам'ятати код, якого немає в реєстрі, разом із назвою від біржі."""
    code = str(code or "").strip()
    if not code:
        return

    key = (exchange, code)
    entry = _seen.get(key)
    if entry is None:
        if len(_seen) >= _MAX_TRACKED:
            return
        entry = UnknownBank(exchange=exchange, code=code, first_seen=time.time())
        _seen[key] = entry

    name = extract_name(payload)
    if name and name.lower() != code.lower():
        entry.names.add(name)
    entry.hits += 1
    entry.last_seen = time.time()


def discovered() -> list[UnknownBank]:
    """Знайдені невідомі банки, найчастіші спершу."""
    return sorted(_seen.values(), key=lambda u: -u.hits)


def render() -> str:
    """Блок для /checkup. Порожньо — коли реєстр покриває все, що бачив сканер."""
    items = discovered()
    if not items:
        return ""

    lines = ["🏦 <b>Банки, яких немає в реєстрі бота</b>"]
    for u in items[:8]:
        lines.append(
            f"• <code>{u.code}</code> ({u.exchange}) — {u.title}, "
            f"зустрівся {u.hits} раз(и)"
        )
    lines.append(
        "<i>Під ці банки картка не підбереться, поки їх не додадуть у "
        "config/banks.py. Скажіть, який це банк — і його заведуть із "
        "лімітами з довідника.</i>"
    )
    return "\n".join(lines)


def reset() -> None:
    """Для тестів."""
    _seen.clear()
