# core/engine/alert_dedup.py
"""
Коли той самий спред можна показати ще раз.

Досі дедуп ключувався ціною:

    spread:{route}|{buy_ex}|{buy_id}|{buy_price}|{min}|{max}|{link}|…|{entry_uah}

У P2P ціни тікають щосекунди. Мерчант переставив 45.44 → 45.45, і ключ став
іншим — тобто для дедупу це «новий спред», хоча пара мерчантів та сама.
Той самий `CHRØME HEARTS → Saint_Frank` прилітав знову і знову, з різницею
в сотих відсотка. Фільтр стабільності не рятував: він теж ключується ціною
(`stability.py:29`), тож нова ціна просто починала лічити свої два хіти.

Тут дедуп ключується **парою мерчантів**, а ціна перестає бути частиною
тотожності й стає приводом. Повторний алерт по тій самій парі має сенс
лише тоді, коли спред **помітно виріс**: 2.1% → 2.15% людині нічого не
дає, а 2.1% → 3.4% дає.
"""
from __future__ import annotations

from dataclasses import dataclass

# Наскільки має підрости спред, щоб про ту саму пару варто було сказати ще
# раз. У відсоткових пунктах.
#
# Півпункта — це помітна зміна для угоди на 20–30 тисяч, і водночас
# достатньо, щоб відсіяти дрижання ринку: у логах сусідні цикли дають
# розкид у сотих.
DEFAULT_IMPROVEMENT_PP = 0.5


def pair_key(opp: dict) -> str:
    """
    Тотожність зв'язки — хто з ким, без цін і лімітів.

    Саме мерчанти, а не оголошення: мерчант може зняти оголошення й
    виставити нове з тим самим змістом, і для людини це та сама пропозиція.
    """
    b = opp["buy_order"]
    s = opp["sell_order"]
    route = opp.get("route_type", "UNKNOWN")
    return f"{route}|{b.exchange}:{b.merchant_id}|{s.exchange}:{s.merchant_id}"


@dataclass(slots=True)
class AlertGate:
    """
    Вирішує, чи показувати зв'язку зараз.

    Тримає останній показаний спред по парі. Кеш із TTL передається ззовні —
    він же визначає, як довго діє «вже показували».
    """
    cache: object                       # core.utils.cache.TTLCache
    improvement_pp: float = DEFAULT_IMPROVEMENT_PP

    def allow(self, opp: dict) -> tuple[bool, str]:
        """(показувати, причина відмови)."""
        key = pair_key(opp)
        try:
            spread = float(opp.get("net_spread_pct") or 0.0)
        except (TypeError, ValueError):
            spread = 0.0

        previous = self.cache.get(key)
        if previous is None:
            self.cache.set(key, spread)
            return True, ""

        gain = spread - float(previous)
        if gain >= self.improvement_pp:
            self.cache.set(key, spread)
            return True, f"спред виріс на {gain:.2f} п.п."

        # Ціна могла й упасти — тоді запам'ятовуємо гірше значення, інакше
        # після падіння 3.4% → 2.0% ми чекали б 2.5%, щоб сказати про 2.0%.
        if spread < float(previous):
            self.cache.set(key, spread)
        return False, f"вже показували ({previous:.2f}%), приріст {gain:+.2f} п.п."
