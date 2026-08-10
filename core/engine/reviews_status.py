# core/engine/reviews_status.py
"""
Чому відгуки саме такі, які є.

Двійник `terms_status` — для другого джерела, яким рівно так само можна
збрехати мовчанням. Порожній список відгуків означав дві протилежні речі:
«мерчанту ніхто не писав» і «ми не змогли подивитись». Перше — факт про
мерчанта, друге — про нас.

Відколи невдалий фетч перестав затирати вже зібране
(`MerchantRepo.mark_reviews_unavailable`), станів стало три, а не два:

    OK + дані          — перевірили щойно, це поточна картина;
    сліпий + дані      — свіжих не дістали, але вчорашні знаємо;
    сліпий без даних   — не знаємо нічого, висновку не буде.

Середній стан найлегше загубити, а він найцінніший: мерчант зі вчорашньою
скаргою на скам не перестає бути небезпечним через те, що сьогодні впала
сесія біржі.

Модуль свідомо не знає ні про базу, ні про рендер — тільки про словник
зведення, який ходить між ними.
"""
from __future__ import annotations

OK = "OK"
NO_FEEDBACK = "NO_FEEDBACK"
NO_SESSION = "NO_SESSION"
SESSION_EXPIRED = "SESSION_EXPIRED"
API_ERROR = "API_ERROR"
UNAVAILABLE = "UNAVAILABLE"
NOT_SUPPORTED = "NOT_SUPPORTED"
NO_AUTH = "NO_AUTH"
PENDING = "PENDING"
UNKNOWN = "UNKNOWN"

# Статуси, за яких СВІЖИХ відгуків у нас немає.
#
# NO_FEEDBACK сюди не входить свідомо: це успішна відповідь біржі, яка
# каже, що відгуків справді нема. Такий нуль — факт про мерчанта, і
# ховати його за «не перевірили» було б протилежною помилкою.
BLIND = frozenset({
    NO_SESSION, SESSION_EXPIRED, API_ERROR, UNAVAILABLE,
    NOT_SUPPORTED, NO_AUTH, PENDING, UNKNOWN,
})

LABELS: dict[str, str] = {
    NO_FEEDBACK: "на біржі відгуків немає",
    NO_SESSION: "немає сесії біржі — відгуків не видно",
    SESSION_EXPIRED: "сесія біржі протухла — відгуків не видно",
    API_ERROR: "біржа відповіла помилкою",
    UNAVAILABLE: "біржа не відповідає",
    NOT_SUPPORTED: "біржа не віддає відгуки через API",
    NO_AUTH: "біржа не авторизує запит",
    PENDING: "перевірка ще не завершилась",
    UNKNOWN: "у відповіді біржі відгуків не було",
}


def is_blind(status: str) -> bool:
    """Чи означає цей статус «свіжого не дістали»."""
    return (status or UNKNOWN) in BLIND


def has_data(summary: dict | None) -> bool:
    """
    Чи є в зведенні хоч щось, зібране успішно — байдуже коли.

    `data_at` — момент останнього УСПІШНОГО збору, окремий від `updated_at`
    (той рухається і при невдачі, бо на ньому тримається бекоф). Нуль
    означає, що успішного збору не було жодного разу, і тоді нулі в
    лічильниках чесні.
    """
    if not summary:
        return False
    if not float(summary.get("data_at", 0) or 0) > 0:
        return False
    counted = (
        int(summary.get("positive", 0) or 0)
        + int(summary.get("negative", 0) or 0)
        + int(summary.get("neutral", 0) or 0)
    )
    return counted > 0 or bool(summary.get("bad_texts"))


def is_dark(summary: dict | None) -> bool:
    """
    Найсуворіший стан: свіжого не дістали І старого не маємо.

    Саме тут не можна показувати ані вижимку відгуків, ані будь-яке
    твердження про репутацію: вони були б висновком нізвідки.
    """
    status = (summary or {}).get("status", UNKNOWN)
    return is_blind(status) and not has_data(summary)


def label(status: str) -> str:
    return LABELS.get(status or UNKNOWN, LABELS[UNKNOWN])


def blind_label(status: str) -> str:
    """Пояснення — тільки коли відгуків НЕ ВИДНО. Інакше порожньо."""
    if not is_blind(status):
        return ""
    return label(status)
