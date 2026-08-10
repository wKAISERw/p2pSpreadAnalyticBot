"""
tests/test_usdt_inventory.py

Етап 5.5 плану PLAN_CARD_MATCHING.md — що треба зробити, щоб продати USDT.

Задача прийшла як «вказувати, з якої біржі я закупив». Але для комісії
історія закупу не потрібна: мережевий переказ коштує однаково незалежно від
ціни монет. Потрібно знати, ДЕ вони зараз.

І «де» — це не лише біржа. Після купівлі на P2P монети падають на спот, а
щоб продати їх на P2P, вони мають бути на фандингу. `get_balance()` у всіх
клієнтах зливає обидва гаманці в одне число, тож «на Bybit є 500 USDT» цілком
може означати «є, але не там, де треба».
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

import pytest

from core.engine.usdt_inventory import (
    Wallets, plan_transfer, render_plan, reset_cache,
)


@pytest.fixture(autouse=True)
def _clean_cache():
    reset_cache()
    yield
    reset_cache()


# ═══════════════════════════════════════════════════════════════
# Гаманець важить не менше за біржу
# ═══════════════════════════════════════════════════════════════

def test_funding_balance_means_ready_to_sell():
    plan = plan_transfer({"Bybit": Wallets(funding=700.0)}, "Bybit", 500.0)

    assert plan.is_ready
    assert not plan.needs_internal_move
    assert "продавати можна одразу" in render_plan(plan)


def test_spot_only_needs_an_internal_move_first():
    """
    Головний випадок після купівлі на P2P: монети на споті. Раніше це
    рахувалось як «USDT уже тут, переказ не потрібен» — і людина дізнавалась
    правду вже під таймером угоди.
    """
    plan = plan_transfer({"Bybit": Wallets(funding=0.0, spot=700.0)}, "Bybit", 500.0)

    assert not plan.is_ready
    assert plan.needs_internal_move
    assert plan.internal_usdt == pytest.approx(500.0)
    assert not plan.needs_transfer, "усередині біржі, мережа ні до чого"
    assert plan.total_fee_usdt == 0.0

    text = render_plan(plan)
    assert "зі споту на P2P-гаманець" in text
    assert "без комісії" in text


def test_funding_is_used_before_spot():
    plan = plan_transfer({"Bybit": Wallets(funding=300.0, spot=500.0)}, "Bybit", 500.0)

    assert plan.on_funding_usdt == pytest.approx(300.0)
    assert plan.internal_usdt == pytest.approx(200.0)


def test_own_spot_is_used_before_another_exchange():
    """Внутрішній переказ безкоштовний — тягнути мережею, маючи свій спот, безглуздо."""
    plan = plan_transfer(
        {"Bybit": Wallets(funding=0.0, spot=600.0), "Binance": Wallets(funding=900.0)},
        "Bybit", 500.0,
    )

    assert plan.internal_usdt == pytest.approx(500.0)
    assert plan.legs == []
    assert plan.total_fee_usdt == 0.0


def test_spot_plus_network_when_own_balance_is_short():
    plan = plan_transfer(
        {"Bybit": Wallets(funding=100.0, spot=100.0), "Binance": Wallets(funding=900.0)},
        "Bybit", 500.0,
    )

    assert plan.internal_usdt == pytest.approx(100.0)
    assert len(plan.legs) == 1
    assert plan.legs[0].from_exchange == "Binance"
    assert plan.legs[0].amount_usdt == pytest.approx(300.0)


# ═══════════════════════════════════════════════════════════════
# Міжбіржовий переказ
# ═══════════════════════════════════════════════════════════════

def test_biggest_donor_goes_first():
    """
    Комісія мережі фіксована за переказ, тож два перекази по 100 USDT
    коштують удвічі дорожче за один на 200. Менше ніг — дешевше.
    """
    plan = plan_transfer(
        {"Bybit": Wallets(), "Binance": Wallets(funding=300.0),
         "OKX": Wallets(funding=50.0), "MEXC": Wallets(spot=20.0)},
        "Bybit", 250.0,
    )

    assert [leg.from_exchange for leg in plan.legs] == ["Binance"]
    assert plan.legs[0].amount_usdt == pytest.approx(250.0)


def test_multiple_donors_when_one_is_not_enough():
    plan = plan_transfer(
        {"Bybit": Wallets(), "Binance": Wallets(funding=300.0),
         "OKX": Wallets(funding=300.0)},
        "Bybit", 500.0,
    )

    assert len(plan.legs) == 2
    assert sum(leg.amount_usdt for leg in plan.legs) == pytest.approx(500.0)
    assert plan.total_fee_usdt > 0


def test_donor_spot_counts_too():
    """На чужій біржі гаманець неважливий — звідти все одно виводити."""
    plan = plan_transfer(
        {"Bybit": Wallets(), "Binance": Wallets(funding=100.0, spot=400.0)},
        "Bybit", 500.0,
    )
    assert plan.legs[0].amount_usdt == pytest.approx(500.0)


def test_shortfall_is_reported_not_hidden():
    plan = plan_transfer(
        {"Bybit": Wallets(funding=100.0, earn_known=True),
         "Binance": Wallets(funding=100.0, earn_known=True)},
        "Bybit", 500.0,
    )

    assert plan.shortfall_usdt == pytest.approx(300.0)
    assert "Не вистачає" in render_plan(plan)
    assert plan.earn_unseen is False


# ═══════════════════════════════════════════════════════════════
# Earn — третій стан готовності
# ═══════════════════════════════════════════════════════════════

def test_earn_is_used_after_spot():
    """
    Викуп із Earn безкоштовний, але це окрема дія й не завжди миттєва —
    тож спот іде першим.
    """
    plan = plan_transfer(
        {"Bybit": Wallets(funding=0.0, spot=200.0, earn=1000.0, earn_known=True)},
        "Bybit", 500.0,
    )

    assert plan.internal_usdt == pytest.approx(200.0)
    assert plan.redeem_usdt == pytest.approx(300.0)
    assert plan.legs == [], "усе своє, мережа ні до чого"

    text = render_plan(plan)
    assert "Викупити з Earn" in text
    assert "не завжди миттєво" in text


def test_own_earn_beats_another_exchange():
    """Викуп безкоштовний, мережевий переказ — ні."""
    plan = plan_transfer(
        {"Bybit": Wallets(earn=900.0, earn_known=True),
         "Binance": Wallets(funding=900.0, earn_known=True)},
        "Bybit", 500.0,
    )

    assert plan.redeem_usdt == pytest.approx(500.0)
    assert plan.legs == []
    assert plan.total_fee_usdt == 0.0


def test_earn_counts_toward_the_total_on_donor_exchanges():
    plan = plan_transfer(
        {"Bybit": Wallets(), "Binance": Wallets(funding=100.0, earn=400.0, earn_known=True)},
        "Bybit", 500.0,
    )
    assert plan.legs[0].amount_usdt == pytest.approx(500.0)


def test_shortfall_does_not_claim_more_than_we_can_see():
    """
    Earn не видно (стара версія клієнта, немає прав у ключа) — твердити
    «немає грошей» означало б видавати межу власної видимості за факт про
    чужий гаманець.
    """
    plan = plan_transfer(
        {"Bybit": Wallets(funding=100.0, earn_known=False)}, "Bybit", 500.0,
    )

    assert plan.shortfall_usdt == pytest.approx(400.0)
    assert plan.earn_unseen is True

    text = render_plan(plan)
    assert "за тим, що видно боту" in text
    assert "Earn і стейкінг бот не бачить" in text


# ═══════════════════════════════════════════════════════════════
# Невідоме лишається невідомим
# ═══════════════════════════════════════════════════════════════

def test_unknown_balances_produce_no_claims():
    """
    Ключів немає або біржі не відповіли. Це не те саме, що нуль: нуль веде
    до висновку «треба переказувати», а невідоме не веде ні до якого.
    """
    plan = plan_transfer(None, "Bybit", 500.0)

    assert plan.unknown is True
    assert plan.legs == []
    assert render_plan(plan) == "", "мовчимо, а не вигадуємо"


def test_zero_amount_says_nothing():
    assert render_plan(plan_transfer({"Bybit": Wallets(funding=100.0)}, "Bybit", 0.0)) == ""


def test_missing_common_network_is_called_out():
    """
    Спільної мережі немає — маршрут насправді неможливий, і подавати це як
    комісію 999 USDT було б брехнею.
    """
    plan = plan_transfer(
        {"Bybit": Wallets(), "НевідомаБіржа": Wallets(funding=900.0)}, "Bybit", 500.0,
    )

    assert plan.has_unroutable_leg
    text = render_plan(plan)
    assert "Спільної мережі" in text
    assert "вручну" in text


# ═══════════════════════════════════════════════════════════════
# Читання гаманців із клієнтів бірж
# ═══════════════════════════════════════════════════════════════

@pytest.mark.asyncio
async def test_wallets_split_funding_spot_and_earn():
    from core.engine.usdt_inventory import _wallets_for_client

    class _Client:
        async def get_funding_balance(self):
            return [{"coin": "USDT", "free": 200.0}]

        async def get_spot_balance(self):
            return [{"coin": "USDT", "free": 800.0}]

        async def get_earn_balance(self):
            return [{"coin": "USDT", "free": 1500.0}]

    wallets = await _wallets_for_client("Binance", _Client())
    assert wallets.funding == pytest.approx(200.0)
    assert wallets.spot == pytest.approx(800.0)
    assert wallets.earn == pytest.approx(1500.0)
    assert wallets.earn_known is True


@pytest.mark.asyncio
async def test_client_without_earn_support_is_marked_unknown():
    """Порожній Earn і невидимий Earn — різні речі, і плутати їх не можна."""
    from core.engine.usdt_inventory import _wallets_for_client

    class _Client:
        async def get_funding_balance(self):
            return [{"coin": "USDT", "free": 200.0}]

    wallets = await _wallets_for_client("MEXC", _Client())
    assert wallets.earn == 0.0
    assert wallets.earn_known is False


@pytest.mark.asyncio
async def test_combined_balance_is_not_double_counted():
    """
    Якщо клієнт уміє лише комбінований `get_balance` (він уже включає
    фандинг), спот — це залишок понад фандинг, а не вся сума.
    """
    from core.engine.usdt_inventory import _wallets_for_client

    class _Client:
        async def get_funding_balance(self):
            return [{"coin": "USDT", "free": 200.0}]

        async def get_balance(self):
            return [{"coin": "USDT", "free": 1000.0}]

    wallets = await _wallets_for_client("Bybit", _Client())
    assert wallets.funding == pytest.approx(200.0)
    assert wallets.spot == pytest.approx(800.0)
    assert wallets.total == pytest.approx(1000.0)


@pytest.mark.asyncio
async def test_spot_only_exchange_reports_zero_funding():
    """MEXC віддає лише спот — план має чесно сказати, що треба перекинути."""
    from core.engine.usdt_inventory import _wallets_for_client

    class _Client:
        async def get_balance(self):
            return [{"coin": "USDT", "free": 500.0}]

    wallets = await _wallets_for_client("MEXC", _Client())
    assert wallets.funding == 0.0
    assert wallets.spot == pytest.approx(500.0)


@pytest.mark.asyncio
async def test_dead_exchange_is_not_reported_as_zero():
    from core.engine.usdt_inventory import _wallets_for_client

    class _Client:
        async def get_funding_balance(self):
            raise RuntimeError("api key expired")

        async def get_balance(self):
            raise RuntimeError("api key expired")

    assert await _wallets_for_client("Binance", _Client()) is None


@pytest.mark.asyncio
async def test_inventory_returns_none_without_credentials():
    from core.engine.usdt_inventory import usdt_by_exchange

    assert await usdt_by_exchange(None, 1) is None
    assert await usdt_by_exchange(object(), 0) is None
