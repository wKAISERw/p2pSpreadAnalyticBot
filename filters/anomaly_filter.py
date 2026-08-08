"""
Фільтр цінових аномалій.

Навіщо він у P2P: у стакані регулярно трапляються оголошення з ціною, яка
різко вибивається з ринку. Природа в них різна, але для нас однакова —
угоду за такою ціною або не дадуть закрити, або закриють не тим способом:

  * замануха: ціна помітно краща за ринок, щоб витягнути тебе в чат;
  * помилка мерчанта: зайвий нуль, переплутані копійки;
  * зрізаний залишок оголошення, який зникне до моменту угоди.

Головне, що тут виправлено проти першої версії, — фільтр був
**симетричним**. Він однаково відкидав і підозріло вигідні, і підозріло
невигідні ціни. Але невигідна ціна нам просто нецікава: вона й так не
пройде пороги спреду. А от найкраща пропозиція в стакані — це саме те, за
чим сканер існує, і викидати її як «аномалію» означає працювати проти себе.

Тому фільтр односторонній: він дивиться тільки на той бік, де аномалія
означає ризик.

  * side="buy"  (ти купуєш USDT) — підозріло НИЗЬКА ціна продавця;
  * side="sell" (ти продаєш USDT) — підозріло ВИСОКА ціна покупця.

Друге виправлення — сенс параметра. У першій версії `multiplier` означав
різні речі залежно від методу: для median це були відсотки (2.5 = 2.5%), а
для mad і mean — множник розкиду. Одне число з трьома значеннями
неможливо налаштувати свідомо. Тепер це завжди «скільки розкидів від
центру», а метод впливає лише на те, як цей розкид виміряти.
"""
from __future__ import annotations

import logging
import statistics
from dataclasses import dataclass
from typing import Iterable, Literal, Sequence

from config import settings
from exchanges.base import Order

logger = logging.getLogger("AnomalyFilter")

Method = Literal["mad", "median", "mean"]
Side = Literal["buy", "sell"]

# Менше цієї кількості ордерів статистика не має сенсу: медіана з трьох
# значень сама по собі шум, і фільтр почне різати випадкові ціни.
MIN_SAMPLE = 5

# Мінімальний відрив, який взагалі може вважатись аномалією, — у відсотках
# від ціни ринку.
#
# Без цієї межі фільтр непридатний, і саме тому перша версія не працювала б
# у бою. P2P-стакан щільний: ціни стоять у межах копійок одна від одної, і
# MAD на такому наборі виходить мізерним — скажімо, 3 копійки. Помножений
# навіть на 2, він дає поріг 6 копійок, після чого «аномалією» стає звичайна
# найкраща пропозиція, нижча за медіану на 13 копійок. Тобто фільтр викидав
# би рівно те, заради чого сканер працює.
#
# 3% — це вже не «трохи дешевше за ринок», а рівень, на якому нормальний
# мерчант не стоїть: при курсі 41 ₴ це понад 1.2 ₴ різниці.
MIN_GAP_PCT = 3.0

METHOD_LABELS: dict[str, str] = {
    "mad": "медіанне відхилення (стійке до викидів)",
    "median": "відхилення від медіани",
    "mean": "стандартне відхилення від середнього",
}


@dataclass(frozen=True)
class Rejected:
    """Відкинутий ордер разом із поясненням — для логів і для UI."""
    order: Order
    price: float
    center: float
    deviation: float
    threshold: float

    @property
    def reason(self) -> str:
        direction = "нижча" if self.price < self.center else "вища"
        return (
            f"ціна {self.price:.2f} ₴ {direction} за ринок "
            f"({self.center:.2f} ₴) на {self.deviation:.2f} ₴ "
            f"при допустимих {self.threshold:.2f} ₴"
        )


@dataclass(frozen=True)
class AnomalyResult:
    kept: list[Order]
    rejected: list[Rejected]
    center: float
    threshold: float
    method: str

    @property
    def is_active(self) -> bool:
        """Чи фільтр справді щось рахував (а не пропустив усе через вибірку)."""
        return self.threshold > 0

    def describe(self) -> str:
        if not self.is_active:
            return f"вибірка замала ({len(self.kept)} ордерів) — фільтр не застосовано"
        gap_pct = (self.threshold / self.center * 100.0) if self.center else 0.0
        return (
            f"{METHOD_LABELS.get(self.method, self.method)}: "
            f"ринок ≈ {self.center:.2f} ₴, допуск ±{self.threshold:.2f} ₴ "
            f"({gap_pct:.1f}%), відкинуто {len(self.rejected)}"
        )


class AnomalyFilter:
    """
    Односторонній фільтр цінових аномалій.

    :param method: як міряти розкид ринку.
    :param multiplier: скільки розкидів від центру вважати нормою. Значення
        однакове за змістом для всіх методів — більше означає м'якше.
    :param min_gap_pct: відрив у відсотках, менший за який ніколи не
        вважається аномалією (див. MIN_GAP_PCT).
    """

    def __init__(
        self,
        method: Method | str = None,
        multiplier: float | None = None,
        min_gap_pct: float = MIN_GAP_PCT,
    ):
        self.method = str(method or settings.anomaly_method).lower()
        self.multiplier = float(
            multiplier if multiplier is not None else settings.anomaly_threshold_multiplier
        )
        self.min_gap_pct = float(min_gap_pct)

        if self.method not in METHOD_LABELS:
            logger.warning(
                "Невідомий метод аномалій %r — використовую 'mad'. Доступні: %s",
                self.method, ", ".join(METHOD_LABELS),
            )
            self.method = "mad"

    # ── Публічний API ────────────────────────────────────────────────────

    def analyze(self, orders: Sequence[Order], side: Side) -> AnomalyResult:
        """
        Розбирає стакан на «нормальні» й «аномальні» ціни.

        Повертає результат із поясненням, а не голий список: причина
        відкидання потрібна і в логах, і в інтерфейсі — інакше зникнення
        ордера виглядає як загублений баг.
        """
        orders = list(orders)
        if len(orders) < MIN_SAMPLE:
            return AnomalyResult(orders, [], 0.0, 0.0, self.method)

        prices = [float(o.price) for o in orders]
        center, spread = self._center_and_spread(prices)

        # Нульовий розкид означає, що всі ціни однакові — відхилятись нема від чого.
        if spread <= 0:
            return AnomalyResult(orders, [], center, 0.0, self.method)

        # Поріг — більший із двох: статистичний і абсолютний.
        #
        # Статистичний ловить викид відносно того, як щільно стоїть саме цей
        # стакан; абсолютний не дає фільтру зірватись на щільному ринку, де
        # розкид мізерний і аномалією стала б будь-яка найкраща ціна.
        threshold = max(spread * self.multiplier, center * self.min_gap_pct / 100.0)

        kept: list[Order] = []
        rejected: list[Rejected] = []
        for order in orders:
            price = float(order.price)
            deviation = price - center

            # Односторонність: аномалією вважаємо лише «занадто добре, щоб
            # бути правдою». Протилежний бік — просто погана ціна, і її
            # відсіють звичайні пороги спреду.
            suspicious = deviation < 0 if side == "buy" else deviation > 0

            if suspicious and abs(deviation) > threshold:
                rejected.append(Rejected(order, price, center, abs(deviation), threshold))
            else:
                kept.append(order)

        if rejected:
            logger.info(
                "🎯 Аномалії (%s, %s): відкинуто %d із %d — ринок ≈ %.2f ₴, допуск ±%.2f ₴",
                self.method, side, len(rejected), len(orders), center, threshold,
            )
            for item in rejected[:5]:
                logger.debug("   🗑 %s — %s", item.order.merchant_name, item.reason)

        return AnomalyResult(kept, rejected, center, threshold, self.method)

    def filter_orders(self, orders: Iterable[Order], side: Side = "buy") -> list[Order]:
        """Скорочення для випадків, де потрібен лише очищений список."""
        return self.analyze(list(orders), side).kept

    # ── Внутрішнє ────────────────────────────────────────────────────────

    def _center_and_spread(self, prices: list[float]) -> tuple[float, float]:
        """
        Центр ринку і міра його розкиду.

        Для mad і median центром є медіана: вона не з'їжджає від однієї
        дикої ціни, а саме такі ціни ми й ловимо. Середнє лишається для
        сумісності з тими, у кого воно вже виставлене в .env.
        """
        if self.method == "mean":
            center = statistics.mean(prices)
            spread = statistics.stdev(prices) if len(prices) > 1 else 0.0
            return center, spread

        center = statistics.median(prices)

        if self.method == "median":
            # Розкид як середнє абсолютне відхилення: простіше за MAD і
            # ближче до інтуїції «наскільки взагалі гуляють ціни».
            spread = sum(abs(p - center) for p in prices) / len(prices)
            return center, spread

        # mad: медіана абсолютних відхилень — найстійкіша до викидів міра.
        spread = statistics.median([abs(p - center) for p in prices])
        return center, spread
