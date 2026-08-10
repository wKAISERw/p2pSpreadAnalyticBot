# core/engine/limit_watch.py
"""
Попередження про наближення до ліміту картки — в момент, коли це стається.

Перевірка на 80% добового ліміту в проєкті вже була, але жила в дашборді
карток: вона спрацьовувала, лише якщо людина сама відкриє меню. Тобто
дізнатись, що картка ось-ось упреться в стелю, можна було тільки якщо про
це здогадатись і піти подивитись.

Тут та сама межа, але перевіряється одразу після транзакції. Поруч уже
живе авто-кулдаун на 95% (`card_repo.confirm_transaction`) — власне, він і
показує, що момент запису транзакції це правильне місце для таких рішень.

Місячний ліміт додано окремо: він менший за добовий у більшості банків
довідника (Izibank 60к/міс проти 150к/добу), тож упирається першим — а
попереджав про нього досі ніхто.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Optional

from config.banks import bank_display_name

logger = logging.getLogger(__name__)

# Поріг попередження. 95% уже зайняте авто-кулдауном, тож тут ідеться саме
# про «скоро», а не «все».
WARN_RATIO = 0.8

# Одне попередження на картку/напрямок/період на добу. Транзакцій за день
# може бути десяток, і кожна після перетину порогу знову дала б сповіщення.
_last_warned: dict[tuple, str] = {}


@dataclass
class LimitWarning:
    card_id: str
    last_four: str
    bank: str
    direction: str      # "in" | "out"
    period: str         # "daily" | "monthly"
    used: float
    limit: float

    @property
    def ratio(self) -> float:
        return self.used / self.limit if self.limit > 0 else 0.0

    def render(self) -> str:
        dir_word = "прийому" if self.direction == "in" else "відправки"
        period_word = "добовий" if self.period == "daily" else "місячний"
        used = f"{self.used:,.0f}".replace(",", " ")
        limit = f"{self.limit:,.0f}".replace(",", " ")
        left = f"{max(0.0, self.limit - self.used):,.0f}".replace(",", " ")
        return (
            f"⚠️ <b>{bank_display_name(self.bank)} *{self.last_four}: "
            f"{period_word} ліміт {dir_word} майже вичерпано</b>\n\n"
            f"📊 Використано <b>{used} ₴</b> з {limit} ₴ ({self.ratio * 100:.0f}%)\n"
            f"💡 Лишилось приблизно <b>{left} ₴</b>\n\n"
            f"<i>Це ваша межа з довідника банків, а не банківський ліміт. "
            f"Змінити — /set_bank_limits.</i>"
        )


def _throttle_key(card_id: str, direction: str, period: str) -> tuple:
    return (card_id, direction, period)


async def check_card_limits(db, card_id: str, direction: str) -> Optional[LimitWarning]:
    """
    Чи перетнула картка поріг попередження після щойно записаної транзакції.

    Повертає найгостріше попередження (те, до межі якого лишилось менше) або
    None. Нічого не надсилає: рішення, кому й як сказати, за викликачем.
    """
    if not db or not card_id:
        return None

    try:
        cards = await db.get_cards_by_id(card_id) if hasattr(db, "get_cards_by_id") else None
        if cards is None:
            async with db._db.execute(
                "SELECT id, owner_id, bank_name, last_four FROM cards WHERE id=?", (card_id,)
            ) as cur:
                row = await cur.fetchone()
            if not row:
                return None
            card = dict(row)
        else:
            card = cards

        limits = await db.get_card_effective_limits(card_id)
        daily_max = limits[f"daily_{direction}_max"]
        monthly_max = limits[f"monthly_{direction}_max"]

        used_daily = await db.get_rolling_used(card_id, direction, hours=24)
        used_monthly = await db.get_monthly_used(card_id, direction)
    except Exception as e:
        logger.debug("Перевірка лімітів картки не виконалась: %s", e)
        return None

    candidates: list[LimitWarning] = []
    for period, used, cap in (("daily", used_daily, daily_max),
                              ("monthly", used_monthly, monthly_max)):
        # -1 означає «не обмежувати» — попереджати нема про що.
        if cap is None or cap <= 0:
            continue
        if used >= cap * WARN_RATIO:
            candidates.append(LimitWarning(
                card_id=card_id, last_four=card.get("last_four", "????"),
                bank=card.get("bank_name", ""), direction=direction,
                period=period, used=used, limit=cap,
            ))

    if not candidates:
        return None

    # Найгостріше — те, де залишок найменший у гривнях: саме він зупинить
    # наступну угоду першим.
    return min(candidates, key=lambda w: w.limit - w.used)


def should_notify(warning: LimitWarning, day: str) -> bool:
    """
    Чи це нова ситуація, чи та сама, про яку вже сказали сьогодні.

    Тротлінг за днем, а не за часом: добовий ліміт і рахується подобово, і
    людині зрозуміліше «сьогодні попередили один раз».
    """
    key = _throttle_key(warning.card_id, warning.direction, warning.period)
    if _last_warned.get(key) == day:
        return False
    _last_warned[key] = day
    return True


def reset_throttle() -> None:
    """Для тестів."""
    _last_warned.clear()
