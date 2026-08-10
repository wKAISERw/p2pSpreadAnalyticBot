# core/engine/risk_flags.py
"""
Розбір `order.risk_flag` — в одному місці.

`risk_flag` — це список прапорів, склеєний комами:

    "LOW_STATS,BLOCK:TRIANGLE:третi особи,STALE_REVIEWS:NO_SESSION:30h"

Читали його всі по-різному, і найпоширеніший спосіб був підрядковий:

    if "BLOCK" in risk_flags:      # core/engine/alert_dispatcher.py:284
        return False               # bot/handlers/monitoring.py:369, 455
                                   # core/engine/taker_scanner.py:555

Підрядок `BLOCK` міститься всередині слова `BLOCKED`. А `FOP_TOV_BLOCKED` і
`BANKA_JAR_BLOCKED` — це не блоки, це **метадані** для персональних фільтрів:
користувач сам обирає, ховати такі ордери, попереджати чи показувати.

Наслідок: ордер із ФОП або банкою відкидався ЗАВЖДИ, незалежно від
налаштування. Режими «⚠️ попереджати» і «показувати» в меню існували, але
були недосяжні — гілка `filter_fop == "hide"` вище просто давала іншу
причину в лозі, а результат був один.

Тут прапори розбираються як список, а не шукаються як підрядок.

Друга функція модуля — `scrub`. У прапор вбудовується вільний текст
(причина блокування, цитата з відгуку, пояснення моделі), і кома в ньому
розриває прапор навпіл. `_build_cached_flag` це врахував і замінював коми,
решта місць — ні.
"""
from __future__ import annotations

SEP = ","

# Префікс справжнього блокування. Усе інше — метадані або м'які сигнали.
_BLOCK = "BLOCK"
_BLACKLIST = "BLOCK:BLACKLIST"


def scrub(text: str | None, limit: int = 900) -> str:
    """
    Готує вільний текст до вбудовування в прапор.

    Коми стають крапками з комою, переноси рядків — пробілами: інакше текст
    розриває і сам прапор, і рядок, у який його склеїли.
    """
    clean = (text or "").replace(SEP, ";").replace("\n", " ").replace("\r", " ")
    return clean.strip()[:limit]


def parse(risk_flag: str | None) -> list[str]:
    """Прапори списком. Порожні фрагменти відкидаються."""
    if not risk_flag:
        return []
    return [p.strip() for p in str(risk_flag).split(SEP) if p.strip()]


def has_block(risk_flag: str | None) -> bool:
    """
    Чи є серед прапорів справжнє блокування.

    Саме `BLOCK` або `BLOCK:щось` — не `..._BLOCKED` і не `LLM_BLOCK` без
    префікса. Причина з комами всередині не заважає: розрив припадає на
    хвіст, а префікс лишається на першому фрагменті.
    """
    for part in parse(risk_flag):
        if part == _BLOCK or part.startswith(_BLOCK + ":"):
            return True
    return False


def is_blacklist_block(risk_flag: str | None) -> bool:
    """Блок саме за чорним списком — у нього окремий режим у налаштуваннях."""
    return any(p.startswith(_BLACKLIST) for p in parse(risk_flag))


def has(risk_flag: str | None, name: str) -> bool:
    """
    Чи є конкретний прапор — точним збігом або як `name:значення`.

    Замінює перевірки виду `"FOP_TOV_BLOCKED" in risk_flags`, які так само
    ловили б будь-який прапор, де ця назва трапилась усередині тексту.
    """
    prefix = name + ":"
    return any(p == name or p.startswith(prefix) for p in parse(risk_flag))
