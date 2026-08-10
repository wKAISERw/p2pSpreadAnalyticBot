# core/engine/buy_budget.py
"""
Скільки USDT брати в конкретну угоду — не чіпаючи того, що ввів користувач.

До цього авто-масштабування працювало записом у БД: побачивши, що на картках
менше грошей, ніж треба на 700 USDT, воно клало в `scanner_users.
taker_buy_amount` пораховані 480.22. Введені 700 після цього не існували
ніде — ні в базі, ні в інтерфейсі. І назад вони не поверталися: після
поповнення картки `auto_scale_up` рахував нову цифру від нових балансів, а
не відновлював бажану.

Тепер бажана сума — незмінний вхід, а «скільки виходить сьогодні» —
похідна величина, яку рахують на льоту під кожен ордер. Балансу побільшало —
шукаємо повні 700 без жодних дій користувача.
"""
from __future__ import annotations

from dataclasses import dataclass

# Менше цього угода не має сенсу — мінімалка бірж по нижньому краю.
MIN_TRADE_USDT = 5.0

# Наскільки можна не дотягнути до бажаної суми, щоб не масштабувати взагалі.
# Округлення курсу й копійки на картці не привід переписувати угоду.
TOLERANCE_UAH = 50.0
TOLERANCE_PCT = 0.95


@dataclass(frozen=True)
class BuyBudget:
    desired_usdt: float     # що ввів користувач — незмінне
    effective_usdt: float   # з чим іти в цей ордер
    available_uah: float
    price: float
    scaled: bool            # ефективна менша за бажану
    blocked: bool           # не набирається навіть мінімалка

    @property
    def effective_uah(self) -> float:
        return self.effective_usdt * self.price

    @property
    def desired_uah(self) -> float:
        return self.desired_usdt * self.price


def resolve_buy_budget(
    desired_usdt: float,
    available_uah: float,
    price: float,
    *,
    allow_scale_down: bool = True,
    min_usdt: float = MIN_TRADE_USDT,
) -> BuyBudget:
    """
    Бажана сума + доступні гроші + курс → з чим реально йти в цей ордер.

    Нічого не зберігає і зберігати не повинна: єдине джерело правди про
    бажану суму — те, що ввів користувач.
    """
    desired_usdt = float(desired_usdt or 0.0)
    available_uah = float(available_uah or 0.0)
    price = float(price or 0.0)

    def _as_is(blocked: bool = False) -> BuyBudget:
        return BuyBudget(
            desired_usdt=desired_usdt, effective_usdt=desired_usdt,
            available_uah=available_uah, price=price,
            scaled=False, blocked=blocked,
        )

    # Без курсу або без бажаної суми масштабувати нема від чого.
    if price <= 0 or desired_usdt <= 0:
        return _as_is()

    needed_uah = desired_usdt * price
    if available_uah >= needed_uah:
        return _as_is()

    # Недобір у межах похибки — беремо бажану суму як є.
    if (needed_uah - available_uah) <= TOLERANCE_UAH or available_uah >= needed_uah * TOLERANCE_PCT:
        return _as_is()

    if not allow_scale_down:
        return _as_is(blocked=True)

    fitted = round(available_uah / price, 2)
    if fitted < min_usdt:
        return BuyBudget(
            desired_usdt=desired_usdt, effective_usdt=0.0,
            available_uah=available_uah, price=price,
            scaled=False, blocked=True,
        )

    return BuyBudget(
        desired_usdt=desired_usdt, effective_usdt=fitted,
        available_uah=available_uah, price=price,
        scaled=True, blocked=False,
    )
