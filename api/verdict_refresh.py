# api/verdict_refresh.py
"""
Свіжий вердикт ризик-движка на момент запиту, а не на момент циклу.

У Telegram це вирішено адресно: коли LLM або збирач відгуків закінчує
роботу, вони кличуть `notifier.redraw_alerts_for_merchant()`, і надіслане
повідомлення **редагується** новим вердиктом. Тобто «🔍 AI аналізує…» у
чаті рано чи пізно перетворюється на конкретне рішення.

На сайті такого не було. Ордер потрапляв у `state.opportunities` із тим
вердиктом, який був на момент циклу, і залишався з ним доти, доки сканер
не перезапише стан наступним колом. А якщо ядро зупинене або мерчант зник
зі стакану — «AI аналізує» лишалось назавжди, хоча вердикт давно є в базі.

Тут та сама ідея, але тягнемо, а не штовхаємо: на кожен запит дивимось у
кеш вердиктів і підставляємо актуальне. Push через WebSocket був би
доречніший, але він вимагає окремого каналу й живого з'єднання; для
сторінки, яка й так перепитує раз на кілька секунд, цього досить.

Вердикт складає `risk_engine._build_cached_flag` — та сама функція, що
формує прапорець для алерта. Другої копії формату тут свідомо немає.
"""
from __future__ import annotations

import logging
from typing import Any, Optional

logger = logging.getLogger(__name__)

# Прапорці, за яких є сенс іти в базу: рішення ще не ухвалене.
# Готовий вердикт (BLOCK / OK / LLM_SUSPICIOUS) перепитувати не треба —
# він і так найсвіжіший з того, що є.
_PENDING_MARKERS = ("PENDING", "NEEDS_LLM", "RECHECKING")


def is_pending(risk_flag: Optional[str]) -> bool:
    flag = (risk_flag or "").upper()
    return any(marker in flag for marker in _PENDING_MARKERS)


async def refresh_flags(db, orders: list[dict]) -> int:
    """
    Оновлює `riskFlag` там, де вердикт уже дозрів. Повертає кількість змін.

    `orders` — серіалізовані ордери з полями exchange / merchantId /
    riskFlag / tradeTerms. Міняються на місці.
    """
    if not db or not orders:
        return 0

    from core.engine.risk_engine import _build_cached_flag

    # Один мерчант зустрічається в кількох зв'язках одразу — без кешу та
    # сама пара давала б по два запити на кожну.
    seen: dict[tuple[str, str], Optional[str]] = {}
    changed = 0

    for order in orders:
        if not is_pending(order.get("riskFlag")):
            continue

        exchange = order.get("exchange") or ""
        merchant_id = order.get("merchantId") or ""
        if not exchange or not merchant_id:
            continue

        key = (exchange, merchant_id)
        if key not in seen:
            try:
                verdict = await db.get_verdict(
                    exchange, merchant_id, order.get("tradeTerms") or ""
                )
                seen[key] = (
                    await _build_cached_flag(verdict, exchange, merchant_id, db)
                    if verdict else None
                )
            except Exception as e:
                # Незмога дістати вердикт не має ламати видачу: лишається
                # те, що вже показано, тобто чесне «AI аналізує».
                logger.debug("verdict refresh %s/%s: %s", exchange, merchant_id, e)
                seen[key] = None

        fresh = seen[key]
        if fresh and fresh != order.get("riskFlag"):
            order["riskFlag"] = fresh
            changed += 1

    return changed


async def refresh_opportunity_flags(db, opps: list[dict[str, Any]]) -> int:
    """Те саме для спред-зв'язок: у кожної дві ноги."""
    legs: list[dict] = []
    for opp in opps:
        for side in ("buyOrder", "sellOrder"):
            leg = opp.get(side)
            if isinstance(leg, dict):
                legs.append(leg)
    return await refresh_flags(db, legs)
