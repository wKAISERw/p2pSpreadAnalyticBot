# core/engine/card_routing.py
"""
З яких банків можна брати картки під конкретний ордер.

Одна функція на весь проєкт: її питає і сканер (щоб порахувати капітал і
матчинг), і будівник алерта (щоб намалювати картковий блок). Дві копії цієї
логіки розійшлись би — сканер вважав би ордер придатним, а алерт малював би
під нього інші картки. Цей проєкт уже п'ять разів наступав на скопійовану
константу, і тут ціна помилки вища: розбіжність видно лише в момент угоди.

Без експериментальних фіч (`/features` → КАРТКИ) поведінка рівно та, що й
була: один банк — той, який вказав мерчант.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field

from config.banks import is_any_bank, normalize_bank
from config.card_limits import FEATURE_IGNORE_MERCHANT_BANKS, FEATURE_INTER_BANK

logger = logging.getLogger(__name__)


@dataclass
class Route:
    banks: list[str] = field(default_factory=list)      # звідки можна брати картки
    declared: list[str] = field(default_factory=list)   # що мерчант вказав сам
    inter_bank: bool = False
    ignore_merchant_banks: bool = False

    @property
    def primary(self) -> str:
        return self.banks[0] if self.banks else ""


def _declared_banks(order_bank_codes) -> list[str]:
    from bot.formatters import _bank_code_to_db

    seen: list[str] = []
    for code in (order_bank_codes or []):
        slug = normalize_bank(_bank_code_to_db(code))
        if slug and slug not in seen:
            seen.append(slug)
    return seen


async def resolve_route(db, user_id: int, order_bank_codes,
                        primary_bank: str = "",
                        card_banks: set[str] | None = None) -> Route:
    """
    Банки маршруту під цей ордер.

    `card_banks` — банки активних карток користувача; сканер має їх у пам'яті
    й передає, щоб не ходити в базу вдруге.
    """
    declared = _declared_banks(order_bank_codes)

    # «Bank Transfer» / «Банковский перевод» — не банк, а спосіб оплати:
    # переказ приймається з будь-якого. Такий метод не звужує вибір карток,
    # а знімає обмеження — і трактувати його як черговий невідомий банк
    # означало б відмовляти на ордерах, які насправді підходять усім.
    accepts_any = any(is_any_bank(b) for b in declared)
    if accepts_any:
        declared = [b for b in declared if not is_any_bank(b)]

    primary = normalize_bank(primary_bank) or (declared[0] if declared else "")

    inter_bank = ignore_merchant = False
    if db and user_id:
        try:
            inter_bank = await db.get_feature_status(user_id, FEATURE_INTER_BANK)
            ignore_merchant = await db.get_feature_status(user_id, FEATURE_IGNORE_MERCHANT_BANKS)
        except Exception as e:
            logger.debug("Не вдалось прочитати стан фіч карток: %s", e)

    # Банки карток потрібні і без кошиків: коли мерчант приймає переказ
    # звідки завгодно, «свій банк» треба ще вибрати.
    if card_banks is None:
        card_banks = set()
        if db and user_id:
            try:
                cards = await db.get_cards(user_id, status="active")
                card_banks = {normalize_bank(c.get("bank_name", "")) for c in cards}
            except Exception as e:
                logger.debug("Не вдалось перелічити банки карток: %s", e)

    mine = sorted(b for b in card_banks if b)

    # Переказ приймається з будь-якого банку — отже всі свої заявлені.
    # Без цього ордер із самим лише «Bank Transfer» лишався без жодного
    # придатного банку, хоча підходить будь-яка картка.
    if accepts_any:
        declared = sorted(set(declared) | set(mine))
        if not primary:
            primary = mine[0] if mine else ""

    if not inter_bank:
        return Route(banks=[primary] if primary else [], declared=declared)

    if ignore_merchant or accepts_any:
        extra = [b for b in mine if b != primary]
    else:
        # Без «ігнорувати фільтр» кошик лишається в межах заявленого
        # мерчантом: інакше перший же маршрут вимагав би узгодження.
        extra = [b for b in mine if b != primary and b in declared]

    banks = ([primary] if primary else []) + extra
    return Route(banks=banks, declared=declared,
                 inter_bank=inter_bank, ignore_merchant_banks=ignore_merchant)
