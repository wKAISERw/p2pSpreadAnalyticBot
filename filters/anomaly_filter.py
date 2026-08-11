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
from core.utils.cache import TTLCache
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

# Як часто повторювати в лозі незмінну картину.
#
# Не «ніколи»: рядок, який не з'являвся годину, читається як «фільтр
# вимкнувся», а не як «нічого не змінилось». П'ять хвилин — компроміс між
# тишею і підтвердженням життя.
LOG_REPEAT_SEC = 300.0


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

    # ── Лог ──────────────────────────────────────────────────────────────
    #
    # Кеш класовий, а не на екземплярі, свідомо: `AnomalyFilter()` створюється
    # заново на кожному проході сканера (`taker_scanner.py`), тож пам'ять на
    # екземплярі не пережила б жодного циклу й не дедуплювала б нічого.
    _log_cache: TTLCache = TTLCache(ttl_seconds=LOG_REPEAT_SEC, max_size=64)

    def _log_result(
        self, side: Side, label: str, n_rejected: int, n_total: int,
        center: float, threshold: float,
    ) -> None:
        """
        Рядок у лог — лише коли картина змінилась.

        Раніше це був беззастережний INFO на кожен виклик. Цикл сканера
        триває близько трьох секунд, а викликів шість (три групи × два боки),
        тож у лог ішло ~120 однакових рядків на хвилину:

            🎯 Аномалії (mad, buy): відкинуто 5 із 204 — ринок ≈ 45.44 ₴…
            🎯 Аномалії (mad, buy): відкинуто 5 із 204 — ринок ≈ 45.44 ₴…

        Ті самі числа, бо ринок за три секунди не рухається. Такий лог не
        просто незручний — він ховає в собі те, заради чого лог і читають.

        Дедуп по сигнатурі, той самий прийом, що й для підозри на бота
        (`_should_log_behavior_alert`): зміна відразу видно, стабільний стан
        повторюється не частіше ніж раз на `LOG_REPEAT_SEC`. Ціна округлена
        до десятої гривні — інакше кожен тік у копійку рахувався б за зміну
        й дедуп не дедуплював би нічого.
        """
        # Сигнатура — про КАРТИНУ ринку, не про точні числа.
        #
        # `n_total` тут свідомо немає, хоч у повідомленні він є: розмір
        # стакану пливе щоцикла на один-два ордери (204 → 205 → 204), і
        # варто йому потрапити в сигнатуру, як дедуп перестає дедуплювати —
        # двадцять циклів дають двадцять рядків, тільки з іншим числом.
        # Ціна округлена до десятої з тієї ж причини: 45.49 і 45.50 — це не
        # «ринок змінився».
        key = (self.method, side, label)
        signature = f"{n_rejected}|{center:.1f}|{threshold:.1f}"

        if self._log_cache.get(key) == signature:
            logger.debug(
                "🎯 Аномалії (%s, %s%s): без змін — відкинуто %d із %d",
                self.method, side, f", {label}" if label else "", n_rejected, n_total,
            )
            return

        self._log_cache.set(key, signature)
        logger.info(
            "🎯 Аномалії (%s, %s%s): відкинуто %d із %d — ринок ≈ %.2f ₴, допуск ±%.2f ₴",
            self.method, side, f", {label}" if label else "",
            n_rejected, n_total, center, threshold,
        )

    # ── Публічний API ────────────────────────────────────────────────────

    def analyze(self, orders: Sequence[Order], side: Side, label: str = "") -> AnomalyResult:
        """
        Розбирає стакан на «нормальні» й «аномальні» ціни.

        `label` — чий це стакан (код банку). Не косметика: стакани різних
        банків мають різні ціни й аналізуються окремо, а в лозі вони
        виглядали однаково — «відкинуто 5 із 204» без жодної згадки, ЯКОГО
        ринку це стосується. Він же дає стабільний ключ для дедупу: розмір
        стакану пливе щоцикла на одиницю, і ключувати ним означало б
        писати новий рядок щоразу, коли з'явився один ордер.

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
            self._log_result(side, label, len(rejected), len(orders), center, threshold)
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
