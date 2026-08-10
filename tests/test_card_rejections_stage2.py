"""
tests/test_card_rejections_stage2.py

Етап 2 плану PLAN_CARD_MATCHING.md — причини відмов назовні.

Раніше відкинутий ордер зникав без сліду: ні в боті, ні в API, ні в логах не
було видно, що саме не зійшлось, тож «сканер нічого не знаходить» не
відрізнялось від «ринку немає». Тут перевіряється, що причина доїжджає до
викликача, має стабільний код і рахується один раз на ордер, а не на кожне
коло сканера.

Плюс IBAN-гейт: вихідні виводять картку з гри лише тоді, коли мерчант
просить переказ на рахунок, а не на картку.
"""
import sys
import time
import uuid
import asyncio
import datetime
from decimal import Decimal
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

import pytest

from core.engine import rejection_codes as rc
from core.engine.card_matching_engine import CardMatchingEngine
from core.engine.taker_scanner import TakerScanner
from core.engine.terms_signals import mentions_iban
from core.storage.merchant_db import MerchantDB
from exchanges.base import Order


TEST_USER_ID = 66601


@pytest.fixture
def event_loop():
    loop = asyncio.new_event_loop()
    yield loop
    loop.close()


@pytest.fixture
async def db(tmp_path):
    merchant_db = MerchantDB(db_path=tmp_path / "test_rejections.db")
    await merchant_db.start()
    yield merchant_db
    await merchant_db.stop()


async def _add_card(db: MerchantDB, bank: str = "monobank", balance: float = 10000.0,
                    last_four: str = "1234", cooldown_until: float = 0) -> str:
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
        "balance": balance, "status": "active", "cooldown_until": cooldown_until,
        "last_monthly_reset": time.time(), "created_at": time.time(),
        "last_tx_timestamp": 0, "is_warmed_up": 1,
    })
    return card_id


def _order(order_id: str = "o1", price: float = 44.0, min_limit: float = 1000,
           max_limit: float = 60000, bank_codes=None, terms: str = "") -> Order:
    return Order(
        id=order_id, price=Decimal(str(price)),
        available_amount=Decimal("100000.0"),
        min_limit=Decimal(str(min_limit)), max_limit=Decimal(str(max_limit)),
        merchant_id=f"m_{order_id}", merchant_name=f"Merchant {order_id}",
        month_order_count=500, finish_rate_pct=99.0, exchange="Binance",
        is_verified=True, bank_codes=bank_codes or ["43"], trade_terms=terms,
    )


def _user(**over) -> dict:
    base = {
        "user_id": TEST_USER_ID, "chat_id": TEST_USER_ID,
        "scanner_mode": "TAKER_BUY", "bank_codes": ["43"],
        "taker_buy_amount": 500.0, "taker_buy_price_strategy": "any",
        "taker_buy_speed": "ANY", "buy_balance_mode": "MANUAL_STRICT",
        "capital": 0.0,
    }
    base.update(over)
    return base


# ═══════════════════════════════════════════════════════════════
# Коди відмов
# ═══════════════════════════════════════════════════════════════

@pytest.mark.asyncio
async def test_engine_reports_code_and_numbers(db):
    """Причина має і стабільний код, і цифри — «не вистачає» без суми марне."""
    await _add_card(db, balance=5000.0)

    engine = CardMatchingEngine(db)
    res = await engine.run(TEST_USER_ID, "monobank", 30000.0, "buy")

    assert res.status == "no_cards"
    codes = {r["code"] for r in res.rejection_report}
    assert rc.INSUFFICIENT_BALANCE in codes

    hit = next(r for r in res.rejection_report if r["code"] == rc.INSUFFICIENT_BALANCE)
    assert hit["shortfall_uah"] == pytest.approx(25000.0)
    assert "25 000" in hit["reason"]


@pytest.mark.asyncio
async def test_cooldown_reason_has_minutes(db):
    await _add_card(db, balance=50000.0, cooldown_until=time.time() + 3600)

    engine = CardMatchingEngine(db)
    res = await engine.run(TEST_USER_ID, "monobank", 1000.0, "buy")

    hit = next(r for r in res.rejection_report if r["code"] == rc.COOLDOWN)
    assert "хв" in hit["reason"]


@pytest.mark.asyncio
async def test_no_active_cards_code(db):
    await db._db.execute(
        "INSERT OR IGNORE INTO user_card_settings (user_id, card_module_mode) "
        "VALUES (?, 'full')", (TEST_USER_ID,),
    )
    await db._db.commit()

    engine = CardMatchingEngine(db)
    res = await engine.run(TEST_USER_ID, "monobank", 1000.0, "buy")
    assert res.rejection_report[0]["code"] == rc.NO_ACTIVE_CARDS


def test_summarize_picks_the_biggest_shortfall():
    report = [
        {"code": rc.COOLDOWN, "reason": "Кулдаун ще 5 хв", "shortfall_uah": 0},
        {"code": rc.INSUFFICIENT_BALANCE, "reason": "не вистачає 8 702 ₴", "shortfall_uah": 8702},
        {"code": rc.MAX_TX_PER_DAY, "reason": "Переказів за добу: 3 з 3", "shortfall_uah": 0},
    ]
    text = rc.summarize(report)
    assert "8 702" in text
    assert "та ще 2 причини" in text


def test_summarize_plural_forms():
    def _n(n):
        return rc.summarize([{"code": "x", "reason": "r"}] * (n + 1))

    assert "1 причина" in _n(1)
    assert "3 причини" in _n(3)
    assert "5 причин" in _n(5)
    assert "11 причин" in _n(11)


# ═══════════════════════════════════════════════════════════════
# Відмови доїжджають зі сканера
# ═══════════════════════════════════════════════════════════════

@pytest.mark.asyncio
async def test_scan_returns_rejections_for_missing_bank(db):
    """Мерчант приймає банк, якого в нас немає, — це причина, а не тиша."""
    await _add_card(db, bank="pumb", balance=50000.0)

    scanner = TakerScanner(db)
    scan = await scanner.scan(_user(bank_codes=["43"]), {"43": [_order()]}, {})

    assert scan.orders == []
    assert [r.code for r in scan.rejections] == [rc.NO_CARDS_FOR_BANK]
    assert scan.rejections[0].order_id == "o1"


@pytest.mark.asyncio
async def test_scan_returns_rejections_from_final_matching(db):
    """Ордер, який відсіявся на лімітах карток, більше не зникає мовчки."""
    await _add_card(db, bank="monobank", balance=100.0)

    scanner = TakerScanner(db)
    scan = await scanner.scan(_user(taker_buy_amount=500.0), {"43": [_order()]}, {})

    assert scan.orders == []
    assert len(scan.rejections) == 1
    assert scan.rejections[0].code == rc.INSUFFICIENT_BALANCE
    assert scan.rejections[0].shortfall_uah > 0
    # Розклад по картках лишається доступним для API
    assert scan.rejections[0].details


@pytest.mark.asyncio
async def test_find_orders_for_user_still_returns_plain_list(db):
    """Старий інтерфейс не зламався — на нього спираються чотири викликачі."""
    await _add_card(db, bank="monobank", balance=100000.0)

    scanner = TakerScanner(db)
    orders = await scanner.find_orders_for_user(_user(), {"43": [_order()]}, {})
    assert [o.id for o in orders] == ["o1"]


# ═══════════════════════════════════════════════════════════════
# Статистика
# ═══════════════════════════════════════════════════════════════

@pytest.mark.asyncio
async def test_rejection_log_dedups_by_order_and_day(db):
    """
    Сканер проходить стакан щохвилини. Без дедупу статистика показувала б
    не «які причини переважають», а «скільки кіл встиг зробити сканер».
    """
    await _add_card(db, bank="monobank", balance=100.0)
    scanner = TakerScanner(db)

    for _ in range(5):
        scan = await scanner.scan(_user(), {"43": [_order()]}, {})
        await db.log_rejections(TEST_USER_ID, "TAKER_BUY", scan.rejections)

    stats = await db.get_rejection_stats(TEST_USER_ID, days=7)
    assert stats["total"] == 1, "П'ять кіл по тому самому ордеру — одна одиниця"
    assert stats["codes"][0]["code"] == rc.INSUFFICIENT_BALANCE
    assert stats["codes"][0]["share_pct"] == 100.0


@pytest.mark.asyncio
async def test_rejection_stats_counts_distinct_orders(db):
    await _add_card(db, bank="monobank", balance=100.0)
    scanner = TakerScanner(db)

    orders = [_order("o1"), _order("o2"), _order("o3")]
    scan = await scanner.scan(_user(), {"43": orders}, {})
    added = await db.log_rejections(TEST_USER_ID, "TAKER_BUY", scan.rejections)

    assert added == 3
    stats = await db.get_rejection_stats(TEST_USER_ID, days=7)
    assert stats["total"] == 3
    assert stats["codes"][0]["avg_shortfall"] > 0


@pytest.mark.asyncio
async def test_scaled_down_order_is_recorded_as_an_observation(db):
    """
    Сценарій, через який статистика лишалась порожньою тиждень поспіль.

    Автоскейл ужимає обсяг під один банк, ордер проходить — і досі це не
    лишало в журналі нічого. Хоча саме тут стеля одного банку коштує обсягу,
    і саме це має вирішити, чи потрібен етап 3.
    """
    await _add_card(db, bank="monobank", balance=21297.80)

    scanner = TakerScanner(db)
    scan = await scanner.scan(
        _user(taker_buy_amount=700.0, buy_balance_mode="AUTO_SCALE",
              buy_auto_scale_down=1),
        {"43": [_order("o1", 44.28, 1000, 60000)]}, {},
    )

    assert len(scan.orders) == 1, "ордер має пройти — просто меншим"
    assert scan.rejections == [], "це не відмова"
    assert [o.code for o in scan.observations] == [rc.VOLUME_SCALED_DOWN]
    assert scan.observations[0].shortfall_uah == pytest.approx(9698.2, abs=1.0)


@pytest.mark.asyncio
async def test_observations_do_not_inflate_the_rejection_share(db):
    """
    «90% відмов» по ордерах, які людина насправді отримала, було б цифрою,
    що суперечить сама собі.
    """
    await _add_card(db, bank="monobank", balance=21297.80)
    scanner = TakerScanner(db)

    scan = await scanner.scan(
        _user(taker_buy_amount=700.0, buy_balance_mode="AUTO_SCALE",
              buy_auto_scale_down=1),
        {"43": [_order("o1", 44.28, 1000, 60000)]}, {},
    )
    await db.log_rejections(TEST_USER_ID, "TAKER_BUY", scan.logged)

    stats = await db.get_rejection_stats(TEST_USER_ID, days=7)
    assert stats["total"] == 0, "відмов не було"
    assert stats["observed_total"] == 1
    assert [r["code"] for r in stats["observations"]] == [rc.VOLUME_SCALED_DOWN]
    assert stats["observations"][0]["avg_shortfall"] > 0


@pytest.mark.asyncio
async def test_observation_is_dropped_when_the_order_dies_later(db):
    """
    Масштабування ордера, який потім усе одно відсіявся, не є фактом
    «стеля коштувала нам обсягу» — до угоди справа не дійшла.
    """
    await _add_card(db, bank="monobank", balance=21297.80)
    scanner = TakerScanner(db)

    # Ліміт одного переказу нижче за суму, і спліт не врятує: одна картка.
    await db.set_user_bank_limit(TEST_USER_ID, "monobank", "monthly_out_max", 100.0)

    scan = await scanner.scan(
        _user(taker_buy_amount=700.0, buy_balance_mode="AUTO_SCALE",
              buy_auto_scale_down=1),
        {"43": [_order("o1", 44.28, 1000, 60000)]}, {},
    )

    assert scan.orders == []
    assert scan.observations == [], "ордер не дійшов до користувача"
    assert scan.rejections, "натомість має бути причина відмови"


@pytest.mark.asyncio
async def test_prune_rejection_log_keeps_recent(db):
    old_day = (datetime.date.today() - datetime.timedelta(days=30)).isoformat()
    await db._db.execute(
        "INSERT INTO card_rejection_log (user_id, day, mode, order_id, code, bank, "
        "reason, shortfall_uah, first_seen) VALUES (?, ?, 'TAKER_BUY', 'old', ?, '', '', 0, 0)",
        (TEST_USER_ID, old_day, rc.COOLDOWN),
    )
    await db._db.execute(
        "INSERT INTO card_rejection_log (user_id, day, mode, order_id, code, bank, "
        "reason, shortfall_uah, first_seen) VALUES (?, ?, 'TAKER_BUY', 'new', ?, '', '', 0, 0)",
        (TEST_USER_ID, datetime.date.today().isoformat(), rc.COOLDOWN),
    )
    await db._db.commit()

    deleted = await db.prune_rejection_log(retention_days=14)
    assert deleted == 1
    assert (await db.get_rejection_stats(TEST_USER_ID, days=7))["total"] == 1


# ═══════════════════════════════════════════════════════════════
# IBAN і робочі дні
# ═══════════════════════════════════════════════════════════════

def test_mentions_iban_detects_request():
    assert mentions_iban("оплата тільки на IBAN")
    assert mentions_iban("переказ на рахунок, реквізити в чаті")
    assert mentions_iban("UA12 3456 7890 1234")


def test_mentions_iban_respects_negation():
    assert not mentions_iban("без IBAN, тільки картка")
    assert not mentions_iban("IBAN не приймаю")
    assert not mentions_iban("тільки на картку")


def test_mentions_iban_on_empty_terms_is_not_a_yes():
    """
    Порожні умови — це «невідомо», а не «так». Глушити картки у вихідні на
    здогадці означало б втрачати придатні ордери.
    """
    assert not mentions_iban("")
    assert not mentions_iban(None)


@pytest.mark.asyncio
async def test_weekend_blocks_only_iban_orders(db, monkeypatch):
    """
    БВР не відправляє IBAN у вихідні, але картка на картку в нього працює
    щодня. Тому суботу відчуває лише той ордер, де мерчант просить рахунок.
    """
    import core.engine.card_matching_engine as cme

    class _Saturday(datetime.date):
        @classmethod
        def today(cls):
            return datetime.date(2026, 8, 8)  # субота

    monkeypatch.setattr(cme.datetime, "date", _Saturday)

    await _add_card(db, bank="bvr", balance=50000.0)
    engine = CardMatchingEngine(db)

    card_to_card = await engine.run(TEST_USER_ID, "bvr", 5000.0, "buy",
                                    trade_terms="оплата з картки на картку")
    assert card_to_card.status == "success"

    by_iban = await engine.run(TEST_USER_ID, "bvr", 5000.0, "buy",
                               trade_terms="переказ тільки на IBAN")
    assert by_iban.status == "no_cards"
    assert by_iban.rejection_report[0]["code"] == rc.BUSINESS_DAYS_ONLY


@pytest.mark.asyncio
async def test_weekday_ignores_business_days_flag(db, monkeypatch):
    import core.engine.card_matching_engine as cme

    class _Monday(datetime.date):
        @classmethod
        def today(cls):
            return datetime.date(2026, 8, 10)  # понеділок

    monkeypatch.setattr(cme.datetime, "date", _Monday)

    await _add_card(db, bank="bvr", balance=50000.0)
    engine = CardMatchingEngine(db)

    res = await engine.run(TEST_USER_ID, "bvr", 5000.0, "buy",
                           trade_terms="переказ тільки на IBAN")
    assert res.status == "success"
