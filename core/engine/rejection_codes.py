# core/engine/rejection_codes.py
"""
Чому картка або ордер не пройшли — коди й людські формулювання.

Причини відмов існували й раніше (`CardMatchResult.rejection_report`), але
жили вільним текстом на суміші двох мов і нікуди не виводились. Через це
типовий сценарій виглядав так: користувач бачить у стакані ордер, бот його
не показує, і дізнатись чому неможливо ні з інтерфейсу, ні з логів.

Код потрібен для статистики (щоб «не вистачає балансу» і «не вистачає
балансу на 8 702 ₴» рахувались як одна причина), формулювання — для людини.
Етап 3 плану спирається на те, які саме коди переважають за тиждень: якщо
90% відмов має одну просту причину, складна система кошиків надлишкова.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

# ── Коди ──────────────────────────────────────────────────────────────────
NO_CARDS_FOR_BANK = "no_cards_for_bank"      # мерчант приймає банк, якого в нас немає
NO_ACTIVE_CARDS = "no_active_cards"
UNKNOWN_BANK_CODE = "unknown_bank_code"      # код банку, якого немає в реєстрі
COOLDOWN = "cooldown"
INSUFFICIENT_BALANCE = "insufficient_balance"
LIMITS_EXHAUSTED = "limits_exhausted"        # добовий або місячний ліміт
MAX_TX_PER_DAY = "max_tx_per_day"
COLD_CARD = "cold_card"                      # не пройдено прогрів
NIGHT_WINDOW = "night_window"                # банк не проводить перекази вночі
BUSINESS_DAYS_ONLY = "business_days_only"    # IBAN у вихідний
BELOW_MIN_TRADE = "below_min_trade"          # навіть мінімалка не набирається
BELOW_MERCHANT_MIN = "below_merchant_min"    # набралось менше, ніж мерчант приймає
SPLIT_IMPOSSIBLE = "split_impossible"
SPLIT_DISABLED = "split_disabled"            # користувач вимкнув спліт сам
# Гроші є, але зібрати їх заважає налаштування, а не баланс. Окремі коди,
# бо дія користувача різна: перемкнути режим проти підняти ліміт карток.
SPLIT_NEEDS_INTER_BANK = "split_needs_inter_bank"
SPLIT_NEEDS_MORE_CARDS = "split_needs_more_cards"
NO_CRYPTO = "no_crypto"

# ── Спостереження ─────────────────────────────────────────────────────────
#
# Ордер пройшов, але не таким, як його задумали. Це НЕ відмова, і в частку
# відмов воно потрапляти не повинно — інакше цифри почнуть суперечити самі
# собі: «90% відмов» при тому, що всі ці ордери людина отримала.
#
# Але для етапу 3 плану це найцінніші дані, які взагалі є. Питання етапу 3
# звучить «чи варто вчити движок збирати суму з карток різних банків», і
# відповідає на нього рівно це: як часто стеля одного банку коштувала нам
# обсягу. Досі не записувалось ніде: автоскейл ужимав обсяг, ордер проходив,
# і в статистиці лишалась порожнеча.
VOLUME_SCALED_DOWN = "volume_scaled_down"

OBSERVATION_CODES: frozenset[str] = frozenset({VOLUME_SCALED_DOWN})

# Порядок від конкретного до загального — таким і показуємо в дайджесті.
CODE_LABELS: dict[str, str] = {
    INSUFFICIENT_BALANCE: "Не вистачає балансу",
    LIMITS_EXHAUSTED: "Вичерпано ліміт обороту",
    MAX_TX_PER_DAY: "Вичерпано переказів за добу",
    COLD_CARD: "Картка не прогріта",
    COOLDOWN: "Картка на кулдауні",
    NIGHT_WINDOW: "Нічні обмеження банку",
    BUSINESS_DAYS_ONLY: "IBAN не піде у вихідний",
    NO_CARDS_FOR_BANK: "Немає картки цього банку",
    NO_ACTIVE_CARDS: "Немає активних карток",
    UNKNOWN_BANK_CODE: "Банк не в реєстрі бота",
    BELOW_MIN_TRADE: "Замало навіть на мінімальну угоду",
    BELOW_MERCHANT_MIN: "Менше за мінімум мерчанта",
    SPLIT_DISABLED: "Спліт вимкнено в налаштуваннях",
    SPLIT_IMPOSSIBLE: "Ліміти переказів не дають скласти суму",
    SPLIT_NEEDS_INTER_BANK: "Потрібен спліт між банками (вимкнено)",
    SPLIT_NEEDS_MORE_CARDS: "Потрібно більше карток, ніж дозволено",
    VOLUME_SCALED_DOWN: "Обсяг ужато під один банк",
    NO_CRYPTO: "Немає криптовалюти на балансі",
}


def _uah(value: float) -> str:
    """12 345 ₴ — з нерозривними пробілами замість ком."""
    return f"{value:,.0f}".replace(",", " ") + " ₴"


@dataclass
class Rejection:
    """Одна причина відмови — по картці або по ордеру загалом."""
    code: str
    reason: str                              # готовий рядок для людини
    card_id: Optional[str] = None
    last_four: str = ""
    bank: str = ""
    shortfall_uah: float = 0.0               # скільки саме не вистачило

    def as_dict(self) -> dict:
        # Ключ `reason` лишається на місці: на нього спираються вже написані
        # перевірки й старі споживачі звіту.
        return {
            "code": self.code,
            "reason": self.reason,
            "card_id": self.card_id,
            "last_four": self.last_four,
            "bank": self.bank,
            "shortfall_uah": round(self.shortfall_uah, 2),
        }


def label(code: str) -> str:
    return CODE_LABELS.get(code, code)


# ── Конструктори з цифрами ────────────────────────────────────────────────
#
# Цифри в тексті — не прикраса: «не вистачає балансу» і «не вистачає 8 702 ₴
# з 30 000 ₴» ведуть до різних дій користувача.

def insufficient_balance(needed: float, have: float, **kw) -> Rejection:
    return Rejection(
        code=INSUFFICIENT_BALANCE,
        reason=f"Потрібно {_uah(needed)}, доступно {_uah(have)} — "
               f"не вистачає {_uah(max(0.0, needed - have))}",
        shortfall_uah=max(0.0, needed - have),
        **kw,
    )


def limits_exhausted(available: float, needed: float, **kw) -> Rejection:
    return Rejection(
        code=LIMITS_EXHAUSTED,
        reason=f"Ліміт обороту лишає {_uah(available)} з потрібних {_uah(needed)}",
        shortfall_uah=max(0.0, needed - available),
        **kw,
    )


def max_tx_per_day(tx_count: int, limit, **kw) -> Rejection:
    return Rejection(
        code=MAX_TX_PER_DAY,
        reason=f"Переказів за добу: {tx_count} з {limit}",
        **kw,
    )


def cold_card(warmup_limit: float, needed: float, **kw) -> Rejection:
    return Rejection(
        code=COLD_CARD,
        reason=f"Непрогріта картка: стеля {_uah(warmup_limit)}, "
               f"потрібно {_uah(needed)}",
        shortfall_uah=max(0.0, needed - warmup_limit),
        **kw,
    )


def cooldown(minutes_left: int, **kw) -> Rejection:
    return Rejection(
        code=COOLDOWN,
        reason=f"Кулдаун ще {minutes_left} хв",
        **kw,
    )


def night_window(cap: float, **kw) -> Rejection:
    if cap <= 0:
        return Rejection(code=NIGHT_WINDOW,
                         reason="Банк не проводить перекази в нічні години", **kw)
    return Rejection(code=NIGHT_WINDOW,
                     reason=f"Нічний ліміт банку: не більше {_uah(cap)}", **kw)


def below_merchant_min(available_uah: float, merchant_min_uah: float, **kw) -> Rejection:
    return Rejection(
        code=BELOW_MERCHANT_MIN,
        reason=f"Мерчант приймає від {_uah(merchant_min_uah)}, а зібрати вийшло "
               f"{_uah(available_uah)} — не вистачає {_uah(merchant_min_uah - available_uah)}",
        shortfall_uah=max(0.0, merchant_min_uah - available_uah),
        **kw,
    )


def unknown_bank_code(code: str, **kw) -> Rejection:
    return Rejection(
        code=UNKNOWN_BANK_CODE,
        reason=f"Мерчант приймає банк із кодом {code}, якого немає в реєстрі бота — "
               f"картка під нього не підбереться, поки банк не додадуть",
        bank=str(code),
        **kw,
    )


def volume_scaled_down(desired_uah: float, effective_uah: float, **kw) -> Rejection:
    return Rejection(
        code=VOLUME_SCALED_DOWN,
        reason=f"Хотіли {_uah(desired_uah)}, пішли в угоду з {_uah(effective_uah)} — "
               f"більше в одному банку немає",
        shortfall_uah=max(0.0, desired_uah - effective_uah),
        **kw,
    )


def business_days_only(bank_title: str, **kw) -> Rejection:
    return Rejection(
        code=BUSINESS_DAYS_ONLY,
        reason=f"{bank_title} відправляє IBAN лише в робочі дні, "
               f"а мерчант просить саме IBAN",
        **kw,
    )


def summarize(rejections: list[dict]) -> str:
    """
    Один рядок для алерта: найчастіша причина плюс скільки ще було.

    Показувати весь список по кожній картці — це стіна тексту в кожному
    повідомленні. Людині потрібна головна причина й цифра.
    """
    if not rejections:
        return ""

    # Найінформативніша — та, де є конкретний недобір.
    with_numbers = [r for r in rejections if r.get("shortfall_uah")]
    head = max(with_numbers, key=lambda r: r["shortfall_uah"]) if with_numbers else rejections[0]

    text = head.get("reason") or label(head.get("code", ""))
    rest = len(rejections) - 1
    if rest > 0:
        text += f" (та ще {rest} {_plural_reasons(rest)})"
    return text


def _plural_reasons(n: int) -> str:
    if 11 <= n % 100 <= 14:
        return "причин"
    last = n % 10
    if last == 1:
        return "причина"
    if last in (2, 3, 4):
        return "причини"
    return "причин"
