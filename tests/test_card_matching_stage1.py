"""
tests/test_card_matching_stage1.py

Регресія на етап 1 плану PLAN_CARD_MATCHING.md і на довідник банків (розділ 7).

Головне, що тут перевіряється: введена користувачем сума купівлі більше не
перезаписується. До цього авто-масштабування клало пораховану цифру в
`scanner_users.taker_buy_amount`, і введені 700 USDT зникали назавжди — навіть
після поповнення картки поверталась не бажана сума, а нова похідна від
балансів.
"""
import sys
import time
import uuid
import asyncio
from decimal import Decimal
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

import pytest

from config.banks import get_bank_profile, license_group_of, normalize_bank
from config.card_limits import (
    GLOBAL_DEFAULTS, LEGACY_DEFAULTS, default_limits_for_bank, merge_limits,
    night_cap_for_bank,
)
from core.engine.buy_budget import resolve_buy_budget
from core.engine.taker_scanner import TakerScanner
from core.storage.merchant_db import MerchantDB
from exchanges.base import Order


TEST_USER_ID = 55501


@pytest.fixture
def event_loop():
    loop = asyncio.new_event_loop()
    yield loop
    loop.close()


@pytest.fixture
async def db(tmp_path):
    merchant_db = MerchantDB(db_path=tmp_path / "test_stage1.db")
    await merchant_db.start()
    yield merchant_db
    await merchant_db.stop()


async def _add_card(db: MerchantDB, bank: str = "monobank", balance: float = 10000.0,
                    last_four: str = "1234") -> str:
    await db._db.execute(
        "INSERT OR IGNORE INTO user_card_settings "
        "(user_id, card_module_mode, max_cards_per_order, enable_in_single_modes) "
        "VALUES (?, 'full', 3, 1)",
        (TEST_USER_ID,),
    )
    await db._db.commit()
    card_id = str(uuid.uuid4())
    await db.add_card({
        "id": card_id, "owner_id": TEST_USER_ID, "bank_name": bank,
        "last_four": last_four, "label": f"Test {last_four}", "is_own": 1,
        "balance": balance, "status": "active", "cooldown_until": 0,
        "last_monthly_reset": time.time(), "created_at": time.time(),
        "last_tx_timestamp": 0, "is_warmed_up": 1,
    })
    return card_id


def _order(order_id: str, price: float, min_limit: float, max_limit: float,
           bank_codes: list | None = None) -> Order:
    return Order(
        id=order_id, price=Decimal(str(price)),
        available_amount=Decimal("100000.0"),
        min_limit=Decimal(str(min_limit)), max_limit=Decimal(str(max_limit)),
        merchant_id=f"m_{order_id}", merchant_name=f"Merchant {order_id}",
        month_order_count=500, finish_rate_pct=99.0, exchange="Binance",
        is_verified=True, bank_codes=bank_codes or ["43"],
    )


# ═══════════════════════════════════════════════════════════════
# Етап 1: недеструктивний автоскейл
# ═══════════════════════════════════════════════════════════════

@pytest.mark.asyncio
async def test_autoscale_never_overwrites_desired_amount(db):
    """
    Сценарій із логу користувача: виставлено 700 USDT, на картках 21 298 ₴.

    Ордер має пройти зі зменшеним обсягом, але `taker_buy_amount` у БД —
    лишитись недоторканим. Раніше сюди прилітав update на 480.22.
    """
    await _add_card(db, bank="monobank", balance=21298.0)

    writes = []

    async def _spy(user_id, amount):
        writes.append((user_id, amount))

    db.update_taker_buy_amount = _spy

    user = {
        "user_id": TEST_USER_ID,
        "scanner_mode": "TAKER_BUY",
        "bank_codes": ["43"],
        "taker_buy_amount": 700.0,
        "taker_buy_price_strategy": "any",
        "taker_buy_speed": "ANY",
        "buy_balance_mode": "AUTO_SCALE",
        "buy_auto_scale_down": 1,
        "buy_auto_scale_up": 1,
        "capital": 0.0,
    }

    scanner = TakerScanner(db)
    orders = await scanner.find_orders_for_user(
        user, {"43": [_order("o1", 44.35, 1000, 60000)]}, {}
    )

    assert writes == [], "Авто-масштабування не має писати в taker_buy_amount"
    assert user["taker_buy_amount"] == 700.0, "Бажаний обсяг має лишитись як є"
    assert [o.id for o in orders] == ["o1"], (
        "Ордер має пройти зі зменшеним обсягом, а не зникнути"
    )


@pytest.mark.asyncio
async def test_autoscale_survives_into_final_matching(db):
    """
    Масштаб, порахований у пре-фільтрі, має дожити до фінального матчингу.

    Роль «перенести суму далі» раніше грав запис у БД. Без нього фінальний
    прохід перевіряв би картки під повні 700 USDT і викидав ордер, який сам
    же щойно визнав придатним.
    """
    await _add_card(db, bank="monobank", balance=21298.0)

    user = {
        "user_id": TEST_USER_ID, "scanner_mode": "TAKER_BUY",
        "bank_codes": ["43"], "taker_buy_amount": 700.0,
        "taker_buy_price_strategy": "any", "taker_buy_speed": "ANY",
        "buy_balance_mode": "AUTO_SCALE", "buy_auto_scale_down": 1,
        "capital": 0.0,
    }

    scanner = TakerScanner(db)
    orders = await scanner.find_orders_for_user(
        user, {"43": [_order("o1", 44.35, 1000, 60000)]}, {}
    )
    assert len(orders) == 1


@pytest.mark.asyncio
async def test_scale_down_disabled_keeps_order_out(db):
    """Без авто-зменшення ордер, на який не вистачає, лишається відкинутим."""
    await _add_card(db, bank="monobank", balance=5000.0)

    user = {
        "user_id": TEST_USER_ID, "scanner_mode": "TAKER_BUY",
        "bank_codes": ["43"], "taker_buy_amount": 700.0,
        "taker_buy_price_strategy": "any", "taker_buy_speed": "ANY",
        "buy_balance_mode": "CARD_ENFORCED", "capital": 0.0,
    }

    scanner = TakerScanner(db)
    orders = await scanner.find_orders_for_user(
        user, {"43": [_order("o1", 44.35, 1000, 60000)]}, {}
    )
    assert orders == []


def test_resolve_buy_budget_keeps_desired_when_affordable():
    b = resolve_buy_budget(700, 40000, 44.35)
    assert b.effective_usdt == 700
    assert not b.scaled and not b.blocked


def test_resolve_buy_budget_scales_down():
    b = resolve_buy_budget(700, 21298, 44.35)
    assert b.scaled
    assert b.desired_usdt == 700, "Бажана сума — незмінний вхід"
    assert b.effective_usdt == pytest.approx(480.23, abs=0.01)


def test_resolve_buy_budget_tolerates_small_shortfall():
    """Недобір у 40 ₴ — це округлення курсу, а не привід переписувати угоду."""
    b = resolve_buy_budget(100, 100 * 44.35 - 40, 44.35)
    assert not b.scaled
    assert b.effective_usdt == 100


def test_resolve_buy_budget_blocks_below_minimum():
    b = resolve_buy_budget(700, 100, 44.35)
    assert b.blocked
    assert b.desired_usdt == 700


def test_resolve_buy_budget_blocks_when_scaling_forbidden():
    b = resolve_buy_budget(700, 21298, 44.35, allow_scale_down=False)
    assert b.blocked
    assert not b.scaled


def test_resolve_buy_budget_restores_desired_when_balance_returns():
    """Ключова властивість недеструктивної моделі: 700 повертаються самі."""
    low = resolve_buy_budget(700, 21298, 44.35)
    assert low.effective_usdt < 700

    topped_up = resolve_buy_budget(low.desired_usdt, 40000, 44.35)
    assert topped_up.effective_usdt == 700


# ═══════════════════════════════════════════════════════════════
# Розділ 7: довідник банків і єдині дефолти
# ═══════════════════════════════════════════════════════════════

def test_bank_profile_overrides_global_defaults():
    izi = default_limits_for_bank("izibank")
    assert izi["max_tx_per_day"] == 3, "Izibank: безпечно 2–3 перекази, а не 15"
    assert izi["monthly_out_max"] == 60000.0

    mono = default_limits_for_bank("43")
    assert mono["max_single_tx_out"] == -1.0, "Monobank — без стелі одного переказу"
    assert mono["monthly_out_max"] == 100000.0


def test_unknown_bank_falls_back_to_global_defaults():
    unknown = default_limits_for_bank("зовсім-невідомий-банк")
    assert unknown == GLOBAL_DEFAULTS
    assert unknown["monthly_out_max"] == 50000.0


def test_profile_blank_field_does_not_invent_a_number():
    """ПУМБ: місячна межа в довіднику є, добової немає — і не вигадується."""
    pumb = default_limits_for_bank("пумб")
    assert pumb["monthly_out_max"] == 70000.0
    assert pumb["daily_out_max"] == GLOBAL_DEFAULTS["daily_out_max"]


def test_user_value_beats_reference_book():
    merged = merge_limits("izibank", {"max_tx_per_day": 10}, None)
    assert merged["max_tx_per_day"] == 10
    # Незаданого поля шар користувача не чіпає
    assert merged["monthly_out_max"] == 60000.0


def test_none_in_layer_means_not_set():
    merged = merge_limits("izibank", {"max_tx_per_day": None})
    assert merged["max_tx_per_day"] == 3


def test_license_groups():
    assert license_group_of("izibank") == license_group_of("таскомбанк") == "tascombank"
    assert license_group_of("бвр") == license_group_of("банк восток") == "vostok"
    assert license_group_of("monobank") == ""


def test_night_window_only_bites_at_night():
    import datetime
    assert night_cap_for_bank("oschadbank", datetime.datetime(2026, 8, 8, 23)) == 5000.0
    assert night_cap_for_bank("oschadbank", datetime.datetime(2026, 8, 8, 3)) == 5000.0
    assert night_cap_for_bank("oschadbank", datetime.datetime(2026, 8, 8, 12)) is None
    assert night_cap_for_bank("monobank", datetime.datetime(2026, 8, 8, 23)) is None


def test_new_bank_aliases_normalize():
    assert normalize_bank("Таскомбанк") == "taskombank"
    assert normalize_bank("БВР") == "bvr"
    assert normalize_bank("Глобус") == "globus"


def test_tier_breaks_ties_but_does_not_outweigh_warmth():
    from core.engine.card_matching_engine import CardMatchingEngine

    engine = CardMatchingEngine(db=None)
    cold_tier1 = {"id": "a", "bank_name": "monobank", "is_own": 1,
                  "_is_warm": False, "_max_avail": 50000.0, "_tx_count": 0}
    warm_tier2 = {"id": "b", "bank_name": "izibank", "is_own": 1,
                  "_is_warm": True, "_max_avail": 50000.0, "_tx_count": 0}
    assert engine._score_and_pick([cold_tier1, warm_tier2], 1000.0)["id"] == "b"

    cold_tier2 = {"id": "c", "bank_name": "izibank", "is_own": 1,
                  "_is_warm": False, "_max_avail": 50000.0, "_tx_count": 0}
    assert engine._score_and_pick([cold_tier2, dict(cold_tier1)], 1000.0)["id"] == "a"


# ═══════════════════════════════════════════════════════════════
# 7.7: міграція user_bank_limits
# ═══════════════════════════════════════════════════════════════

@pytest.mark.asyncio
async def test_migration_clears_legacy_defaults_but_keeps_user_values(tmp_path):
    import aiosqlite

    db_path = tmp_path / "legacy.db"

    # Піднімаємо базу, потім імітуємо стан «до довідника»: рядок зі старими
    # спільними DEFAULT-ами плюс одне свідомо задане користувачем поле.
    first = MerchantDB(db_path=db_path)
    await first.start()
    await first._db.execute(
        "INSERT OR REPLACE INTO user_bank_limits "
        "(user_id, bank_name, daily_out_max, daily_in_max, monthly_out_max, "
        " monthly_in_max, max_single_tx_out, max_single_tx_in, max_tx_per_day, "
        " cooldown_hours) VALUES (?, 'izibank', ?, ?, ?, ?, ?, ?, ?, ?)",
        (TEST_USER_ID,
         LEGACY_DEFAULTS["daily_out_max"], LEGACY_DEFAULTS["daily_in_max"],
         LEGACY_DEFAULTS["monthly_out_max"], LEGACY_DEFAULTS["monthly_in_max"],
         LEGACY_DEFAULTS["max_single_tx_out"], LEGACY_DEFAULTS["max_single_tx_in"],
         7,  # ← користувач це поле ввів власноруч
         LEGACY_DEFAULTS["cooldown_hours"]),
    )
    # Прапорець міграції знімаємо, щоб вона відпрацювала на цьому рядку.
    await first._db.execute(
        "DELETE FROM bot_settings WHERE key = '_migration_bank_limits_nullable_v1'"
    )
    await first._db.commit()
    await first.stop()

    second = MerchantDB(db_path=db_path)
    await second.start()
    row = await second.get_user_bank_limits(TEST_USER_ID, "izibank")

    assert row["monthly_out_max"] is None, "Старий спільний дефолт має звільнити місце"
    assert row["max_tx_per_day"] == 7, "Введене користувачем чіпати не можна"

    effective = merge_limits("izibank", row)
    assert effective["monthly_out_max"] == 60000.0, "Тепер діє довідник"
    assert effective["max_tx_per_day"] == 7

    await second.stop()


@pytest.mark.asyncio
async def test_setting_one_limit_does_not_freeze_the_rest(db):
    """
    Правка одного поля не має мовчки зафіксувати решту сім.

    Саме через це профіль банку не спрацював би: рядок, створений заради
    `max_tx_per_day`, ніс ще сім значень зі старого спільного дефолту.
    """
    await db.set_user_bank_limit(TEST_USER_ID, "izibank", "max_tx_per_day", 7)
    row = await db.get_user_bank_limits(TEST_USER_ID, "izibank")

    assert row["max_tx_per_day"] == 7
    assert row["monthly_out_max"] is None
    assert merge_limits("izibank", row)["monthly_out_max"] == 60000.0


# ═══════════════════════════════════════════════════════════════
# 7.6: комісії з довідника
# ═══════════════════════════════════════════════════════════════

def test_threshold_fee_respects_free_limit():
    from core.utils.fees import get_calculator

    calc = get_calculator("328", "43", "Binance", "Binance")  # Sense: до 20к — 0%
    assert calc.calculate_net(10_000.0, 41.0)[1] == 0.0
    assert calc.calculate_net(30_000.0, 41.0)[1] == pytest.approx(30_000 * 0.01 + 5.0)


def test_monobank_has_no_domestic_transfer_fee():
    """У коді стояло 0.5% крос-банк, у довіднику — 0% по Україні."""
    from core.utils.fees import get_calculator

    calc = get_calculator("43", "64", "Binance", "Binance")
    assert calc.calculate_net(10_000.0, 41.0)[1] == 0.0


def test_cross_bank_only_fee_skips_same_bank():
    from core.utils.fees import get_calculator

    same = get_calculator("14", "14", "Binance", "Binance")
    cross = get_calculator("14", "43", "Binance", "Binance")
    assert same.calculate_net(10_000.0, 41.0)[1] == 0.0
    assert cross.calculate_net(10_000.0, 41.0)[1] == pytest.approx(50.0)


def test_banks_outside_the_old_hardcoded_five_now_have_fees():
    """Ощадбанк, KredoBank і Таскомбанк система вважала безкоштовними."""
    from core.utils.fees import get_calculator

    oschad = get_calculator("99", "43", "Binance", "Binance")
    assert oschad.calculate_net(10_000.0, 41.0)[1] == pytest.approx(105.0)
