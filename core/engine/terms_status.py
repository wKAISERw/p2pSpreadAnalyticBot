# core/engine/terms_status.py
"""
Чому умови мерчанта саме такі, які є.

Порожні умови означали дві протилежні речі: «мерчант нічого не написав»
і «ми не змогли дістати». Перше — факт про мерчанта, друге — про нас. У
алерті обидва виглядали як «Умови не вказані», і людина робила висновок
про контрагента там, де насправді бачила межу власної видимості.

Дві біржі — Wallet і CryptoBot — намагались це розрізняти, але писали
причину **всередину поля умов**:

    order.trade_terms = "не вдалося отримати доступ до умов через ..."

Наслідок гірший за початкову проблему: `risk_engine` проганяє `trade_terms`
через регекси й віддає LLM, тобто службове речення аналізувалось як текст
мерчанта. Тут статус винесено в окреме поле, а `trade_terms` лишається тим,
чим має бути, — словами мерчанта або порожнім рядком.
"""
from __future__ import annotations

OK = "OK"
EMPTY = "EMPTY"
NO_SESSION = "NO_SESSION"
SESSION_EXPIRED = "SESSION_EXPIRED"
FETCH_FAILED = "FETCH_FAILED"
NOT_SUPPORTED = "NOT_SUPPORTED"
UNKNOWN = "UNKNOWN"

# Статуси, за яких про умови мерчанта нам НЕ відомо нічого. Саме вони не
# сміють читатись як «умов немає».
BLIND = frozenset({NO_SESSION, SESSION_EXPIRED, FETCH_FAILED, NOT_SUPPORTED, UNKNOWN})

LABELS: dict[str, str] = {
    EMPTY: "мерчант не вказав умов",
    NO_SESSION: "немає сесії біржі — умов не видно",
    SESSION_EXPIRED: "сесія біржі протухла — умов не видно",
    FETCH_FAILED: "біржа не віддала умови",
    NOT_SUPPORTED: "біржа не віддає умови через API",
    UNKNOWN: "умов у відповіді біржі не було",
}


def from_payload(payload: dict, *keys: str) -> tuple[str, str]:
    """
    (текст умов, статус) з сирої відповіді біржі.

    Ключова відмінність від `payload.get(key, "")`: якщо ключа немає
    ЗОВСІМ — це UNKNOWN, а не EMPTY. Присутній, але порожній ключ — це
    справді EMPTY, тобто мерчант нічого не написав.
    """
    if not isinstance(payload, dict):
        return "", UNKNOWN

    for key in keys:
        if key not in payload:
            continue
        raw = payload.get(key)
        text = str(raw or "").strip().lower()
        return text, (OK if text else EMPTY)

    return "", UNKNOWN


def is_blind(status: str) -> bool:
    """Чи означає цей статус «ми не знаємо», а не «умов немає»."""
    return (status or UNKNOWN) in BLIND


def label(status: str) -> str:
    return LABELS.get(status or UNKNOWN, LABELS[UNKNOWN])


def blind_label(status: str) -> str:
    """
    Пояснення — тільки коли умов НЕ ВИДНО. Інакше порожньо.

    Коли мерчант справді нічого не написав, рядок про це зайвий: відсутність
    умов і так видно. А от «не змогли дістати» треба сказати вголос, інакше
    людина прочитає межу нашої видимості як факт про контрагента.
    """
    if not is_blind(status):
        return ""
    return label(status)
