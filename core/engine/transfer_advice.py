# core/engine/transfer_advice.py
"""
Які перекази між власними картками покриють нестачу під угоду.

Розрахунок жив усередині `card_notifier` і повертав одразу готовий HTML для
Telegram. Через це порада існувала рівно в одному місці: на сайті картковий
блок був порожній, хоча в чат приходило «Перекажіть 3 655 ₴ з Monobank
*6251 на Pumb *9664».

Тут — самі числа. Форматує їх той, кому треба: бот у HTML, дашборд у свою
розмітку. Логіка одна, і розійтись копіями їй нема як.

Перевіряються обидва боки переказу: джерело мусить мати змогу ВІДПРАВИТИ
цю суму (одноразовий, добовий, місячний OUT і лічильник транзакцій), а
призначення — ПРИЙНЯТИ її (ті самі ліміти по IN). Порада, яка впирається в
ліміт на другому кроці, гірша за відсутність поради: людина почне переказ і
дізнається про межу вже в застосунку банку.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass

logger = logging.getLogger(__name__)

# -1 у полі ліміту означає «не обмежувати» (config.banks.UNLIMITED).
UNLIMITED = -1


def _is_unlimited(value) -> bool:
    try:
        return float(value) == UNLIMITED
    except (TypeError, ValueError):
        return False


def _fits(used: float, amount: float, cap) -> bool:
    """Чи влізе `amount` у ліміт `cap` з урахуванням уже витраченого."""
    if cap is None or _is_unlimited(cap):
        return True
    try:
        return (float(used) + amount) <= float(cap)
    except (TypeError, ValueError):
        return True


@dataclass
class TransferSuggestion:
    from_card_id: str
    from_bank: str
    from_last_four: str
    to_card_id: str
    to_bank: str
    to_last_four: str
    amount_uah: float

    def as_dict(self) -> dict:
        return {
            "fromCardId": self.from_card_id,
            "fromBank": self.from_bank,
            "fromLastFour": self.from_last_four,
            "toCardId": self.to_card_id,
            "toBank": self.to_bank,
            "toLastFour": self.to_last_four,
            "amountUah": round(self.amount_uah, 2),
        }


async def suggest_transfers(
    db, user_id: int, target_bank: str, target_amount: float,
) -> list[TransferSuggestion]:
    """
    Пари «звідки → куди», які доводять картку потрібного банку до суми.

    Порожньо означає рівно «таких пар немає»: або картки того банку вже
    вистачає, або жодне джерело не проходить за лімітами.
    """
    if not db or target_amount <= 0:
        return []

    try:
        all_cards = await db.get_cards(owner_id=user_id, status="active")
    except Exception as e:
        logger.debug("suggest_transfers cards: %s", e)
        return []

    if not all_cards:
        return []

    wanted = (target_bank or "").strip().lower()
    destinations = [
        c for c in all_cards
        if str(c.get("bank_name", "")).lower() == wanted
        and float(c.get("balance", 0.0) or 0.0) < target_amount
    ]
    if not destinations:
        return []

    out: list[TransferSuggestion] = []

    for dest in destinations:
        dest_id = dest["id"]
        gap = target_amount - float(dest.get("balance", 0.0) or 0.0)
        if gap <= 0:
            continue

        try:
            dest_limits = await db.get_card_effective_limits(dest_id)
            dest_used_daily = await db.get_rolling_used(dest_id, "in", hours=24)
            dest_used_monthly = await db.get_monthly_used(dest_id, "in")
            dest_tx = await db.get_card_transactions_count(dest_id, hours=24)
        except Exception as e:
            logger.debug("suggest_transfers dest %s: %s", dest_id, e)
            continue

        # Чи прийме призначення цей переказ.
        if not _fits(0, gap, dest_limits.get("max_single_tx_in")):
            continue
        if not _fits(dest_used_daily, gap, dest_limits.get("daily_in_max")):
            continue
        if not _fits(dest_used_monthly, gap, dest_limits.get("monthly_in_max")):
            continue
        max_tx = dest_limits.get("max_tx_per_day")
        if not _is_unlimited(max_tx) and max_tx is not None and dest_tx >= float(max_tx):
            continue

        for src in all_cards:
            if src["id"] == dest_id:
                continue
            if float(src.get("balance", 0.0) or 0.0) < gap:
                continue

            try:
                src_limits = await db.get_card_effective_limits(src["id"])
                src_used_daily = await db.get_rolling_used(src["id"], "out", hours=24)
                src_used_monthly = await db.get_monthly_used(src["id"], "out")
                src_tx = await db.get_card_transactions_count(src["id"], hours=24)
            except Exception as e:
                logger.debug("suggest_transfers src %s: %s", src["id"], e)
                continue

            if not _fits(0, gap, src_limits.get("max_single_tx_out")):
                continue
            if not _fits(src_used_daily, gap, src_limits.get("daily_out_max")):
                continue
            if not _fits(src_used_monthly, gap, src_limits.get("monthly_out_max")):
                continue
            src_max_tx = src_limits.get("max_tx_per_day")
            if not _is_unlimited(src_max_tx) and src_max_tx is not None and src_tx >= float(src_max_tx):
                continue

            out.append(TransferSuggestion(
                from_card_id=src["id"],
                from_bank=str(src.get("bank_name", "")),
                from_last_four=str(src.get("last_four", "")),
                to_card_id=dest_id,
                to_bank=str(dest.get("bank_name", "")),
                to_last_four=str(dest.get("last_four", "")),
                amount_uah=gap,
            ))

    return out
