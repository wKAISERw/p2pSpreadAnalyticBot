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


# Рекомендація LLM людською мовою — те саме, що бот пише в алерті.
_RECOMMENDATION_LABELS = {
    "APPROVE": "✅ Безпечно",
    "CONDITIONAL": "⚡ З обережністю",
    "REJECT": "🚫 Не торгувати",
    "RECHECKING": "🔄 AI перепровіряє",
    "PENDING": "🔍 AI аналізує",
}


async def _ai_view(db, exchange: str, merchant_id: str) -> Optional[dict]:
    """
    Що модель сказала про мерчанта: вердикт, пояснення, вижимка умов.

    Живе не в `risk_flag`, а в таблиці `merchant_verdict` — саме тому на
    сайті не було ні вижимки умов, ні пояснення для «безпечних». Бот бере
    їх звідси й пише в алерт («🧠 Buy: ✅ БЕЗПЕЧНО» плюс абзац тексту), а
    в API це поле не доходило взагалі.
    """
    try:
        rec, verdict, reason, terms_summary, reviews = (
            await db.get_trade_recommendation_full(exchange, merchant_id)
        )
    except Exception as e:
        logger.debug("ai view %s/%s: %s", exchange, merchant_id, e)
        return None

    if not any((reason, terms_summary, reviews)) and rec == "PENDING":
        return None

    return {
        "recommendation": rec,
        "recommendationLabel": _RECOMMENDATION_LABELS.get(rec, rec),
        "verdict": verdict,
        # Чому саме такий вердикт — головна цінність усього блоку.
        "reason": reason,
        # Вижимка умов оголошення: мерчанти пишуть їх абзацами, і модель
        # зводить до кількох рядків.
        "termsSummary": terms_summary,
        "reviewsAnalysis": reviews,
    }


def _drop_blind_terms(ai: dict, terms_status: Optional[str]) -> dict:
    """
    Прибирає вижимку умов, зроблену з умов, яких ми не бачили.

    Вердикт живе в кеші кілька днів і не перераховується, поки не змінився
    хеш умов. Але якщо умов не видно взагалі (немає сесії, біржа не віддала
    поле), хеш порожній і стабільний — тобто стара вижимка «Умови не
    вказані» переживе будь-яку кількість циклів і виглядатиме як свіжий
    факт про мерчанта.

    Це та сама підміна, що й раніше, тільки джерело інше: не поле умов, а
    кеш вердиктів. Тому при «сліпому» статусі вижимка не показується — її
    місце займає чесне «умов не видно».
    """
    from core.engine import terms_status as ts

    if not ai or not ts.is_blind(terms_status or ""):
        return ai
    if not ai.get("termsSummary"):
        return ai
    return {**ai, "termsSummary": ""}


def _drop_blind_reviews(ai: dict, reviews_summary: Optional[dict]) -> dict:
    """
    Те саме для вижимки ВІДГУКІВ.

    Симетрії тут довго не було, і причина повчальна: поле `reviewsAnalysis`
    віддавалось у API, але завжди порожнім — `llm_worker._parse_json` не
    повертав його зі словника, тож у базу лягав порожній рядок. Захищати
    було нічого, і відсутність перевірки нікому не заважала.

    Щойно розбір відповіді полагодили, поле ожило — разом із тією самою
    пасткою, від якої `_drop_blind_terms` рятує умови: вижимка з кешу
    описувала б відгуки, яких ми зараз не бачимо.

    Не показуємо тільки в найсуворішому стані (`is_dark`): свіжого не
    дістали І збереженого не маємо. Якщо збережені відгуки є, нехай і
    вчорашні, вижимка по них лишається чесною — треба лише пам'ятати, що
    вона про вчора.
    """
    from core.engine import reviews_status as rs

    if not ai or not rs.is_dark(reviews_summary):
        return ai
    if not ai.get("reviewsAnalysis"):
        return ai
    return {**ai, "reviewsAnalysis": ""}


async def refresh_flags(db, orders: list[dict]) -> int:
    """
    Оновлює `riskFlag` там, де вердикт дозрів, і додає блок `ai`.

    `orders` — серіалізовані ордери з полями exchange / merchantId /
    riskFlag / tradeTerms. Міняються на місці.
    """
    if not db or not orders:
        return 0

    from core.engine.risk_engine import _build_cached_flag

    # Один мерчант зустрічається в кількох зв'язках одразу — без кешу та
    # сама пара давала б по два запити на кожну.
    seen: dict[tuple[str, str], Optional[str]] = {}
    ai_seen: dict[tuple[str, str], Optional[dict]] = {}
    rev_seen: dict[tuple[str, str], Optional[dict]] = {}
    changed = 0

    for order in orders:
        exchange = order.get("exchange") or ""
        merchant_id = order.get("merchantId") or ""
        if not exchange or not merchant_id:
            continue

        key = (exchange, merchant_id)

        # Вижимка й пояснення потрібні завжди, а не лише поки вердикт
        # дозріває: саме їх бракувало «безпечним» ордерам, де прапорець
        # порожній і показувати без цього блоку нічого.
        if key not in ai_seen:
            ai_seen[key] = await _ai_view(db, exchange, merchant_id)
        if ai_seen[key]:
            # Стан відгуків тягнемо лише тоді, коли є що захищати: без
            # вижимки зайвий запит на кожен ордер нічого не дає.
            if ai_seen[key].get("reviewsAnalysis") and key not in rev_seen:
                try:
                    rev_seen[key] = await db.get_reviews_summary(exchange, merchant_id)
                except Exception as e:
                    logger.debug("reviews summary %s/%s: %s", exchange, merchant_id, e)
                    rev_seen[key] = None

            view = _drop_blind_terms(ai_seen[key], order.get("termsStatus"))
            order["ai"] = _drop_blind_reviews(view, rev_seen.get(key))

        if not is_pending(order.get("riskFlag")):
            continue

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
