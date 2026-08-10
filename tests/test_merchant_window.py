"""
tests/test_merchant_window.py

Сума угоди мусить лягати у вікно мерчанта [min_limit, max_limit].

Три сценарії з розбору 09.08.2026 на реальних балансах користувача:
Monobank 21 297.80, ПУМБ 5 005, Sense 4 820 — разом 31 122.80 ₴.

Дірка, яку вони виявили: автоскейл ужимав обсяг під наявні гроші й
відправляв алерт, НЕ перевіривши, чи прийме мерчант таку суму. Ордер «від
30 000 ₴» проходив із масштабом до 21 298 ₴ — тобто людині приходила угода,
яку неможливо взяти за жодних обставин.
"""
import sys
import time
import uuid
import asyncio
from decimal import Decimal
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

import pytest

from config.card_limits import FEATURE_IGNORE_MERCHANT_BANKS, FEATURE_INTER_BANK
from core.engine import rejection_codes as rc
from core.engine.taker_scanner import TakerScanner
from core.storage.merchant_db import MerchantDB
from exchanges.base import Order


TEST_USER_ID = 60601
PRICE = 44.28


@pytest.fixture
def event_loop():
    loop = asyncio.new_event_loop()
    yield loop
    loop.close()


@pytest.fixture
async def db(tmp_path):
    merchant_db = MerchantDB(db_path=tmp_path / "test_window.db")
    await merchant_db.start()
    await merchant_db._db.execute(
        "INSERT OR IGNORE INTO user_card_settings "
        "(user_id, card_module_mode, enable_in_single_modes, max_cards_per_order) "
        "VALUES (?, 'full', 1, 3)", (TEST_USER_ID,),
    )
    await merchant_db._db.commit()
    for bank, balance, last_four in (
        ("monobank", 21297.80, "1111"),
        ("pumb", 5005.0, "2222"),
        ("sense", 4820.0, "3333"),
    ):
        await merchant_db.add_card({
            "id": str(uuid.uuid4()), "owner_id": TEST_USER_ID, "bank_name": bank,
            "last_four": last_four, "label": bank, "is_own": 1, "balance": balance,
            "status": "active", "cooldown_until": 0,
            "last_monthly_reset": time.time(), "created_at": time.time(),
            "last_tx_timestamp": 0, "is_warmed_up": 1,
        })
    yield merchant_db
    await merchant_db.stop()


async def _enable(db: MerchantDB, *features: str) -> None:
    for key in features:
        await db._db.execute(
            "INSERT OR REPLACE INTO user_features (user_id, feature_key, is_enabled) "
            "VALUES (?, ?, 1)", (TEST_USER_ID, key),
        )
    await db._db.commit()
    current = await db.get_user_card_settings(TEST_USER_ID) or {}
    current["card_split_mode"] = "inter_bank"
    current["max_cards_per_order"] = 3
    await db.update_user_card_settings(TEST_USER_ID, current)


def _order(min_l: float, max_l: float, banks=None) -> Order:
    return Order(
        id="o1", price=Decimal(str(PRICE)), available_amount=Decimal("100000"),
        min_limit=Decimal(str(min_l)), max_limit=Decimal(str(max_l)),
        merchant_id="m1", merchant_name="Merchant",
        month_order_count=500, finish_rate_pct=99.0, exchange="Binance",
        bank_codes=banks or ["43"],
    )


def _user(amount_usdt: float) -> dict:
    return {
        "user_id": TEST_USER_ID, "chat_id": TEST_USER_ID,
        "scanner_mode": "TAKER_BUY", "bank_codes": ["43", "64", "328"],
        "taker_buy_amount": amount_usdt, "taker_buy_price_strategy": "any",
        "taker_buy_speed": "ANY", "buy_balance_mode": "AUTO_SCALE",
        "buy_auto_scale_down": 1, "capital": 0.0,
    }


# ═══════════════════════════════════════════════════════════════
# Сценарій 1: мерчант приймає від 30 000 ₴
# ═══════════════════════════════════════════════════════════════

@pytest.mark.asyncio
async def test_scaled_amount_below_merchant_minimum_is_rejected(db):
    """
    В одному банку 21 298 ₴, мерчант приймає від 30 000 ₴. Раніше алерт
    приходив — на угоду, яку неможливо взяти.
    """
    scan = await TakerScanner(db).scan(_user(700.0), {"43": [_order(30000, 60000)]}, {})

    assert scan.orders == []
    assert [r.code for r in scan.rejections] == [rc.BELOW_MERCHANT_MIN]
    assert "30 000" in scan.rejections[0].reason
    assert "21 298" in scan.rejections[0].reason
    assert scan.rejections[0].shortfall_uah == pytest.approx(8702.2, abs=1.0)


@pytest.mark.asyncio
async def test_basket_across_banks_makes_the_same_order_workable(db):
    """
    Той самий ордер із увімкненими кошиками: 31 122 ₴ по трьох банках
    перекривають мінімум мерчанта, і угода стає можливою.
    """
    await _enable(db, FEATURE_INTER_BANK, FEATURE_IGNORE_MERCHANT_BANKS)

    scan = await TakerScanner(db).scan(_user(700.0), {"43": [_order(30000, 60000)]}, {})

    assert [o.id for o in scan.orders] == ["o1"]
    assert scan.rejections == []
    assert scan.observations == [], "обсяг ужимати не довелось"


@pytest.mark.asyncio
async def test_inter_bank_alone_does_not_help_when_merchant_named_one_bank(db):
    """
    Чесна межа: сам по собі кошик лишається в межах заявлених мерчантом
    банків. Якщо в оголошенні лише Monobank, гроші в ПУМБ не дістати —
    для цього потрібен окремий перемикач «ігнорувати фільтр банків».
    """
    await _enable(db, FEATURE_INTER_BANK)

    scan = await TakerScanner(db).scan(_user(700.0), {"43": [_order(30000, 60000)]}, {})

    assert scan.orders == []
    assert [r.code for r in scan.rejections] == [rc.BELOW_MERCHANT_MIN]


# ═══════════════════════════════════════════════════════════════
# Сценарій 2: стеля мерчанта нижча за бажаний обсяг
# ═══════════════════════════════════════════════════════════════

@pytest.mark.asyncio
async def test_amount_is_clamped_to_merchant_ceiling(db):
    """
    Хочемо 700 USDT, мерчант бере максимум 7 000 ₴. Перевіряти картки під
    31 000 ₴ безглуздо — стільки він однаково не візьме.
    """
    scan = await TakerScanner(db).scan(_user(700.0), {"43": [_order(1000, 7000)]}, {})

    assert [o.id for o in scan.orders] == ["o1"]
    # Обсяг ужато стелею мерчанта, а не браком грошей — тож це не
    # спостереження про стелю одного банку.
    assert scan.observations == []
    assert scan.rejections == []


@pytest.mark.asyncio
async def test_small_cards_cover_a_small_order(db):
    """Ордер на 3 000 ₴ проходить картками ПУМБ або Sense без жодних кошиків."""
    scan = await TakerScanner(db).scan(_user(68.0), {"64": [_order(3000, 3000, ["64"])]}, {})

    assert [o.id for o in scan.orders] == ["o1"]
    assert scan.rejections == []


@pytest.mark.asyncio
async def test_volume_below_merchant_minimum_is_filtered_before_cards(db):
    """
    Обсяг користувача менший за мінімум мерчанта — ордер відпадає ще на
    ціновому фільтрі, до карток. Це не картковий дефект, тож у статистику
    причин він свідомо не потрапляє.
    """
    scan = await TakerScanner(db).scan(_user(158.0), {"43": [_order(7000, 7000)]}, {})

    assert scan.orders == []
    assert scan.rejections == [], "картки тут ні до чого"
