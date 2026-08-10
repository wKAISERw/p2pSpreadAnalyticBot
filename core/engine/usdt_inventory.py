# core/engine/usdt_inventory.py
"""
Де лежить USDT — і що треба зробити, щоб продати його на потрібній біржі.

Задача прийшла як «вказувати, скільки й з якої біржі я закупив, щоб
рахувалась міжбіржова комісія». Але для самої комісії історія закупу не
потрібна: мережевий переказ коштує однаково незалежно від того, за скільки
монети куплені. Потрібно знати рівно одне — **де вони зараз**. А це вже
віддають підключені API-ключі.

Лоти лишаються потрібними для іншого: щоб рахувати ЧИСТИЙ ПРОФІТ, треба
знати ціну закупу. Це окремий шар, і він справді необов'язковий.

**Гаманець важить не менше за біржу.** Після купівлі на P2P монети падають
на спот, а щоб продати їх на P2P, вони мають бути на фандингу. `get_balance()`
у всіх клієнтах зливає обидва гаманці в одне число — тобто «на Bybit є 500
USDT» може означати «є, але не там, де треба». Тому тут гаманці рахуються
окремо: внутрішній переказ безкоштовний, але це крок, і людина має про
нього знати до того, як візьме ордер під таймер.

Кеш обов'язковий: сканер проходить стакан щохвилини, а кожен виклик балансу
— мережевий запит до біржі.
"""
from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from typing import Optional

from core.engine.network_fee_engine import NetworkFeeEngine

logger = logging.getLogger(__name__)

# Скільки живе знімок балансів. Дві хвилини — компроміс: баланс між угодами
# змінюється рідше, а сканер за цей час устигає кілька кіл.
CACHE_TTL_SECONDS = 120

_cache: dict[int, tuple[float, dict[str, "Wallets"]]] = {}


@dataclass
class Wallets:
    """
    USDT на одній біржі — сходинками готовності до продажу.

    Це не три однакові кошики, а три різні відстані до угоди:
    фандинг продається зараз, спот вимагає одного кліку, Earn — викупу.
    Locked-продукти сюди не потрапляють свідомо: достроково їх не забрати,
    тож видавати їх за доступні під 15-хвилинний таймер було б обіцянкою
    грошей, яких не буде.
    """
    funding: float = 0.0        # готовий до P2P-продажу
    spot: float = 0.0           # перекинути всередині біржі, миттєво й безкоштовно
    earn: float = 0.0           # гнучкий Earn: спершу викупити
    earn_known: bool = False    # чи ми взагалі бачили Earn на цій біржі

    @property
    def total(self) -> float:
        return self.funding + self.spot + self.earn


@dataclass
class TransferLeg:
    from_exchange: str
    amount_usdt: float
    network: str
    fee_usdt: float


@dataclass
class TransferPlan:
    """Що треба зробити, щоб USDT опинився там, де його продають."""
    sell_exchange: str
    needed_usdt: float
    on_funding_usdt: float = 0.0
    internal_usdt: float = 0.0      # зі споту на фандинг тієї ж біржі
    redeem_usdt: float = 0.0        # викупити з Earn
    legs: list[TransferLeg] = field(default_factory=list)
    shortfall_usdt: float = 0.0     # не набралось навіть по всіх біржах
    unknown: bool = False           # балансів дістати не вдалось
    earn_unseen: bool = False       # є біржі, чий Earn ми не бачимо

    @property
    def needs_internal_move(self) -> bool:
        return self.internal_usdt > 0

    @property
    def needs_redeem(self) -> bool:
        return self.redeem_usdt > 0

    @property
    def needs_transfer(self) -> bool:
        return bool(self.legs)

    @property
    def total_fee_usdt(self) -> float:
        return sum(leg.fee_usdt for leg in self.legs)

    @property
    def has_unroutable_leg(self) -> bool:
        """Хоч одна нога без спільної мережі — маршрут насправді неможливий."""
        return any(leg.network == "UNKNOWN" for leg in self.legs)

    @property
    def is_ready(self) -> bool:
        """Продавати можна прямо зараз, нічого не рухаючи."""
        return not self.needs_internal_move and not self.needs_redeem \
            and not self.needs_transfer and self.shortfall_usdt <= 0 \
            and not self.unknown


async def _wallets_for_client(exchange: str, client) -> Optional[Wallets]:
    """
    Фандинг і спот окремо. None — біржа не відповіла.

    Клієнти вміють обидва гаманці (`get_funding_balance` є у Binance, Bybit і
    OKX), просто `get_balance` їх зливає. MEXC віддає лише спот — тоді фандинг
    лишається нулем, і план чесно скаже, що треба перекинути.
    """
    def _usdt(rows) -> float:
        return sum(
            float(r.get("free", 0) or 0) for r in (rows or [])
            if str(r.get("coin", "")).upper() == "USDT"
        )

    funding = spot = earn = 0.0
    seen = False
    earn_known = False

    getter = getattr(client, "get_funding_balance", None)
    if callable(getter):
        try:
            funding = _usdt(await getter())
            seen = True
        except Exception as e:
            logger.debug("Фандинг %s недоступний: %s", exchange, e)

    # Earn читаємо окремо: він не входить ні в спот, ні в комбінований
    # баланс. Прапорець потрібен, щоб відрізнити «в Earn нуль» від «Earn ми
    # не бачимо» — у другому випадку твердити «не вистачає» не можна.
    getter = getattr(client, "get_earn_balance", None)
    if callable(getter):
        try:
            earn = _usdt(await getter())
            earn_known = True
            seen = True
        except Exception as e:
            logger.debug("Earn %s недоступний: %s", exchange, e)

    for name in ("get_spot_balance", "get_trading_balance", "get_balance"):
        getter = getattr(client, name, None)
        if not callable(getter):
            continue
        try:
            rows = await getter()
        except Exception as e:
            logger.debug("Баланс %s (%s) недоступний: %s", exchange, name, e)
            continue
        value = _usdt(rows)
        seen = True
        if name == "get_balance":
            # Комбінований баланс: спот — це залишок понад фандинг, а не
            # вся сума. Інакше монети порахувались би двічі.
            spot = max(0.0, value - funding)
        else:
            spot = value
        break

    return Wallets(funding=funding, spot=spot, earn=earn,
                   earn_known=earn_known) if seen else None


async def usdt_by_exchange(db, user_id: int, force: bool = False) -> Optional[dict[str, Wallets]]:
    """
    Скільки вільного USDT на кожній підключеній біржі, по гаманцях.

    None означає «не знаємо» — ключів немає або біржі не відповіли. Це не те
    саме, що «нуль»: нуль веде до висновку «треба переказувати», а невідоме
    не веде ні до якого висновку, і вигадувати його не можна.
    """
    if not db or not user_id:
        return None

    cached = _cache.get(user_id)
    if cached and not force and (time.time() - cached[0]) < CACHE_TTL_SECONDS:
        return cached[1]

    try:
        from core.engine.credentials import load_credentials

        clients = (await load_credentials(db, user_id)).as_dict()
    except Exception as e:
        logger.debug("Не вдалось завантажити ключі бірж: %s", e)
        return None

    balances: dict[str, Wallets] = {}
    for exchange, client in clients.items():
        if not getattr(client, "is_authenticated", False):
            continue
        try:
            async with client:
                wallets = await _wallets_for_client(exchange, client)
        except Exception as e:
            # Одна біржа впала — решта знімка лишається корисною.
            logger.debug("Баланс %s недоступний: %s", exchange, e)
            continue
        if wallets and wallets.total > 0:
            balances[exchange] = wallets

    if not balances:
        return None

    _cache[user_id] = (time.time(), balances)
    return balances


def plan_transfer(balances: Optional[dict[str, Wallets]], sell_exchange: str,
                  needed_usdt: float) -> TransferPlan:
    """
    Що треба зробити під цей ордер: нічого, перекинути всередині біржі чи
    везти з іншої.

    Чиста функція: баланси приходять ззовні, щоб її можна було перевірити
    без мережі й без ключів.
    """
    plan = TransferPlan(sell_exchange=sell_exchange, needed_usdt=needed_usdt)

    if balances is None:
        plan.unknown = True
        return plan
    if needed_usdt <= 0:
        return plan

    here = balances.get(sell_exchange) or Wallets()
    plan.on_funding_usdt = here.funding
    missing = needed_usdt - here.funding
    if missing <= 0:
        return plan

    # Крок 1: свій же спот. Безкоштовно і швидко — завжди перед мережею.
    if here.spot > 0:
        take = min(here.spot, missing)
        plan.internal_usdt = round(take, 2)
        missing -= take
        if missing <= 0:
            return plan

    # Крок 2: свій Earn. Теж без комісії, але викуп — окрема дія, і в
    # деяких продуктах він не миттєвий, тож іде після споту.
    if here.earn > 0:
        take = min(here.earn, missing)
        plan.redeem_usdt = round(take, 2)
        missing -= take
        if missing <= 0:
            return plan

    # Крок 3: інші біржі, за спаданням загального балансу — менше ніг,
    # менше комісій. Комісія мережі фіксована за переказ, тож два перекази
    # по 100 USDT коштують удвічі дорожче за один на 200.
    donors = sorted(
        ((ex, w) for ex, w in balances.items() if ex != sell_exchange and w.total > 0),
        key=lambda kv: -kv[1].total,
    )

    for exchange, wallets in donors:
        if missing <= 0:
            break
        take = min(wallets.total, missing)
        network, fee = NetworkFeeEngine.get_optimal_network(exchange, sell_exchange)
        plan.legs.append(TransferLeg(
            from_exchange=exchange, amount_usdt=round(take, 2),
            network=network, fee_usdt=float(fee),
        ))
        missing -= take

    plan.shortfall_usdt = max(0.0, round(missing, 2))

    # Не вистачило — але, можливо, гроші просто в Earn, якого ми не бачимо
    # (стара версія клієнта, біржа без такого ендпоінта, немає прав у ключа).
    # Тоді «не вистачає» — надто сильне твердження.
    if plan.shortfall_usdt > 0:
        plan.earn_unseen = any(not w.earn_known for w in balances.values())

    return plan


def render_plan(plan: TransferPlan, price_uah: float = 0.0) -> str:
    """Рядок для алерта продажу. Порожньо — коли казати нема чого."""
    if plan.unknown or plan.needed_usdt <= 0:
        return ""

    if plan.is_ready:
        return (
            f"⛓ USDT уже на {plan.sell_exchange}, гаманець P2P — "
            f"продавати можна одразу\n"
        )

    lines = []

    # Внутрішній переказ називаємо окремо: він безкоштовний, але це крок,
    # і під 15-хвилинним таймером угоди про нього треба знати заздалегідь.
    if plan.needs_internal_move:
        lines.append(
            f"⛓ Перекинути всередині {plan.sell_exchange}: "
            f"<b>{plan.internal_usdt:.2f} USDT</b> зі споту на P2P-гаманець "
            f"<i>(без комісії)</i>"
        )

    if plan.needs_redeem:
        lines.append(
            f"🏦 Викупити з Earn на {plan.sell_exchange}: "
            f"<b>{plan.redeem_usdt:.2f} USDT</b> "
            f"<i>(без комісії, але не завжди миттєво)</i>"
        )

    if plan.has_unroutable_leg:
        sources = ", ".join(leg.from_exchange for leg in plan.legs
                            if leg.network == "UNKNOWN")
        lines.append(
            f"⚠️ Спільної мережі з {sources} не знайдено — переказ доведеться "
            f"робити вручну"
        )
    elif plan.needs_transfer:
        parts = [
            f"{leg.amount_usdt:.2f} USDT з {leg.from_exchange} ({leg.network})"
            for leg in plan.legs
        ]
        fee = plan.total_fee_usdt
        fee_uah = f" (~{fee * price_uah:,.0f} ₴)".replace(",", " ") if price_uah > 0 else ""
        lines.append(f"⛓ Перевести: {', '.join(parts)}")
        lines.append(f"   └ Комісія мережі: <b>{fee:.4f} USDT</b>{fee_uah}")

    if plan.shortfall_usdt > 0:
        line = (f"   └ ⚠️ Не вистачає <b>{plan.shortfall_usdt:.2f} USDT</b> "
                f"за тим, що видно боту")
        if plan.earn_unseen:
            # Твердити «немає грошей», не побачивши Earn, — це видавати
            # межу власної видимості за факт про чужий гаманець.
            line += " <i>(Earn і стейкінг бот не бачить — перевірте там)</i>"
        lines.append(line)

    return "\n".join(lines) + "\n" if lines else ""


def reset_cache() -> None:
    """Для тестів і для примусового оновлення після угоди."""
    _cache.clear()
