"""
tests/test_inter_bank_stage3.py

Етап 3 плану PLAN_CARD_MATCHING.md — кошики карток між банками.

Це те саме обмеження, через яке 31 000 ₴ на трьох картках перетворювались на
21 000 ₴ доступних: движок збирав суму лише в межах банку, який вказав
мерчант. Тепер він уміє більше, але лише за увімкненими експериментальними
фічами — з вимкненими поведінка має лишитись рівно тією, що була.

Приклад із розмови з користувачем перевіряється прямо: ордер на 5 000 ₴, у
ПУМБ рівно 5 000, у Сенсі 4 800 (не тягне), у Монобанку 21 000 (тягне).
Правильний вибір — ПУМБ: одна транзакція закриває картку, а великий залишок
Монобанку лишається під більший ордер.
"""
import sys
import time
import uuid
import asyncio
from decimal import Decimal
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

import pytest

from config.card_limits import (
    FEATURE_IGNORE_MERCHANT_BANKS, FEATURE_INTER_BANK,
    SPLIT_INTER_BANK, SPLIT_INTRA_BANK, available_split_modes,
)
from core.engine.card_matching_engine import CardMatchingEngine
from core.engine.card_routing import resolve_route
from core.engine.taker_scanner import TakerScanner
from core.storage.merchant_db import MerchantDB
from exchanges.base import Order


TEST_USER_ID = 44401


@pytest.fixture
def event_loop():
    loop = asyncio.new_event_loop()
    yield loop
    loop.close()


@pytest.fixture
async def db(tmp_path):
    merchant_db = MerchantDB(db_path=tmp_path / "test_inter_bank.db")
    await merchant_db.start()
    yield merchant_db
    await merchant_db.stop()


async def _settings(db: MerchantDB, **over) -> None:
    await db._db.execute(
        "INSERT OR IGNORE INTO user_card_settings "
        "(user_id, card_module_mode, enable_in_single_modes) VALUES (?, 'full', 1)",
        (TEST_USER_ID,),
    )
    await db._db.commit()
    if over:
        current = await db.get_user_card_settings(TEST_USER_ID) or {}
        current.update(over)
        await db.update_user_card_settings(TEST_USER_ID, current)


async def _enable(db: MerchantDB, *features: str) -> None:
    for key in features:
        await db._db.execute(
            "INSERT OR REPLACE INTO user_features (user_id, feature_key, is_enabled) "
            "VALUES (?, ?, 1)", (TEST_USER_ID, key),
        )
    await db._db.commit()


async def _card(db: MerchantDB, bank: str, balance: float, last_four: str) -> str:
    card_id = str(uuid.uuid4())
    await db.add_card({
        "id": card_id, "owner_id": TEST_USER_ID, "bank_name": bank,
        "last_four": last_four, "label": f"T{last_four}", "is_own": 1,
        "balance": balance, "status": "active", "cooldown_until": 0,
        "last_monthly_reset": time.time(), "created_at": time.time(),
        "last_tx_timestamp": 0, "is_warmed_up": 1,
    })
    return card_id


def _order(oid: str = "o1", price: float = 44.0, min_l: float = 1000.0,
           max_l: float = 60000.0, banks=None) -> Order:
    return Order(
        id=oid, price=Decimal(str(price)), available_amount=Decimal("100000"),
        min_limit=Decimal(str(min_l)), max_limit=Decimal(str(max_l)),
        merchant_id=f"m{oid}", merchant_name=f"M{oid}",
        month_order_count=500, finish_rate_pct=99.0, exchange="Binance",
        bank_codes=banks or ["43"],
    )


# ═══════════════════════════════════════════════════════════════
# Приклад користувача: ордер на 5 000 ₴
# ═══════════════════════════════════════════════════════════════

@pytest.mark.asyncio
async def test_exact_fit_card_wins_over_the_big_one(db):
    """
    ПУМБ має рівно 5 000 під ордер на 5 000 — одна транзакція закриває
    картку. Монобанк теж тягне, але відкусити 5 000 від 21 000 гірше:
    великий залишок стане в пригоді під більший ордер.
    """
    await _enable(db, FEATURE_INTER_BANK)
    await _settings(db, card_split_mode=SPLIT_INTRA_BANK)
    pumb = await _card(db, "pumb", 5000.0, "1111")
    await _card(db, "sense", 4800.0, "2222")
    await _card(db, "monobank", 21000.0, "3333")

    res = await CardMatchingEngine(db).run(
        TEST_USER_ID, "monobank", 5000.0, "buy",
        banks=["monobank", "pumb", "sense"],
    )

    assert res.status == "success"
    assert res.best_card["id"] == pumb


@pytest.mark.asyncio
async def test_card_that_cannot_cover_alone_is_not_picked(db):
    """Сенс із 4 800 не тягне 5 000 сам — одиночним вибором він бути не може."""
    await _settings(db)
    await _enable(db, FEATURE_INTER_BANK)
    await _card(db, "sense", 4800.0, "2222")

    res = await CardMatchingEngine(db).run(
        TEST_USER_ID, "sense", 5000.0, "buy", banks=["sense"],
    )
    assert res.status != "success"


# ═══════════════════════════════════════════════════════════════
# Кошик між банками
# ═══════════════════════════════════════════════════════════════

@pytest.mark.asyncio
async def test_basket_collects_across_banks(db):
    """31 000 ₴ на трьох картках нарешті працюють як 31 000, а не як 21 000."""
    # Фіча спершу, налаштування потім: без неї режим «між банками» просто
    # не збережеться — це перевіряє окремий тест нижче.
    await _enable(db, FEATURE_INTER_BANK)
    await _settings(db, card_split_mode=SPLIT_INTER_BANK, max_cards_per_order=3)
    await _card(db, "monobank", 21297.0, "1111")
    await _card(db, "pumb", 5005.0, "2222")
    await _card(db, "sense", 4820.0, "3333")

    res = await CardMatchingEngine(db).run(
        TEST_USER_ID, "monobank", 30000.0, "buy",
        banks=["monobank", "pumb", "sense"],
    )

    assert res.status == "needs_split"
    legs = res.split_options[0]
    assert len({leg["bank"] for leg in legs}) > 1, "кошик має бути міжбанківським"
    assert sum(leg["amount"] for leg in legs) == pytest.approx(30000.0, abs=1.0)


@pytest.mark.asyncio
async def test_basket_respects_max_cards(db):
    await _enable(db, FEATURE_INTER_BANK)
    await _settings(db, card_split_mode=SPLIT_INTER_BANK, max_cards_per_order=2)
    await _card(db, "monobank", 10000.0, "1111")
    await _card(db, "pumb", 10000.0, "2222")
    await _card(db, "sense", 10000.0, "3333")

    res = await CardMatchingEngine(db).run(
        TEST_USER_ID, "monobank", 25000.0, "buy",
        banks=["monobank", "pumb", "sense"],
    )
    assert res.status == "no_cards", "двома картками 25 000 не набрати"


@pytest.mark.asyncio
async def test_same_bank_split_is_preferred_over_cross_bank(db):
    """
    Ранжування 6.3: N карток одного банку краще за N карток різних.
    Менше пояснень із мерчантом і менше застосунків під таймер.
    """
    await _enable(db, FEATURE_INTER_BANK)
    await _settings(db, card_split_mode=SPLIT_INTER_BANK, max_cards_per_order=3)
    # Жодна картка не тягне 11 000 сама, але дві монобанківські разом — так.
    await _card(db, "monobank", 6000.0, "1111")
    await _card(db, "monobank", 6000.0, "2222")
    await _card(db, "pumb", 7000.0, "3333")

    res = await CardMatchingEngine(db).run(
        TEST_USER_ID, "monobank", 11000.0, "buy",
        banks=["monobank", "pumb"],
    )

    assert res.status == "needs_split"
    best = res.split_options[0]
    assert {leg["bank"] for leg in best} == {"monobank"}


@pytest.mark.asyncio
async def test_inter_bank_split_needs_the_mode(db):
    """Режим «між банками» не увімкнено — кошик збиратись не повинен."""
    await _settings(db, card_split_mode=SPLIT_INTRA_BANK, max_cards_per_order=3)
    await _enable(db, FEATURE_INTER_BANK)
    await _card(db, "monobank", 6000.0, "1111")
    await _card(db, "pumb", 6000.0, "2222")

    res = await CardMatchingEngine(db).run(
        TEST_USER_ID, "monobank", 11000.0, "buy", banks=["monobank", "pumb"],
    )
    assert res.status == "no_cards"


# ═══════════════════════════════════════════════════════════════
# Чому спліт не склався — на живих даних це виявилось найважливішим
# ═══════════════════════════════════════════════════════════════

@pytest.mark.asyncio
async def test_failed_split_names_the_missing_mode(db):
    """
    Половина реальних відмов користувача була саме цією: гроші є, лежать на
    різних банках, фіча увімкнена — а режим спліту лишився «в межах банку».
    Повідомлення «Не вдалось скласти спліт» не казало, що робити.
    """
    from core.engine import rejection_codes as rc

    await _settings(db, card_split_mode=SPLIT_INTRA_BANK, max_cards_per_order=3)
    await _enable(db, FEATURE_INTER_BANK)
    await _card(db, "monobank", 6000.0, "1111")
    await _card(db, "pumb", 6000.0, "2222")

    res = await CardMatchingEngine(db).run(
        TEST_USER_ID, "monobank", 11000.0, "buy", banks=["monobank", "pumb"],
    )

    final = res.rejection_report[-1]
    assert final["code"] == rc.SPLIT_NEEDS_INTER_BANK
    assert "різних банках" in final["reason"]
    assert "Спліт: між банками" in final["reason"]


@pytest.mark.asyncio
async def test_failed_split_names_the_card_limit(db):
    """Грошей вистачає, але потрібно більше карток, ніж дозволено."""
    from core.engine import rejection_codes as rc

    await _enable(db, FEATURE_INTER_BANK)
    await _settings(db, card_split_mode=SPLIT_INTER_BANK, max_cards_per_order=2)
    await _card(db, "monobank", 4000.0, "1111")
    await _card(db, "pumb", 4000.0, "2222")
    await _card(db, "sense", 4000.0, "3333")

    res = await CardMatchingEngine(db).run(
        TEST_USER_ID, "monobank", 11000.0, "buy",
        banks=["monobank", "pumb", "sense"],
    )

    final = res.rejection_report[-1]
    assert final["code"] == rc.SPLIT_NEEDS_MORE_CARDS
    assert "3 картками" in final["reason"]
    assert "Максимум карток" in final["reason"]


@pytest.mark.asyncio
async def test_unknown_bank_code_is_not_reported_as_missing_card(db):
    """
    Код 545 із реального стакана: його немає в реєстрі, тож картка під нього
    не знайдеться ніколи. Досі це подавалось як «немає активних карток» —
    тобто проблему шукали б у картках, а вона в config/banks.py.
    """
    from core.engine import rejection_codes as rc

    await _settings(db)
    await _card(db, "monobank", 50000.0, "1111")

    res = await CardMatchingEngine(db).run(TEST_USER_ID, "545", 5000.0, "buy")

    assert res.rejection_report[0]["code"] == rc.UNKNOWN_BANK_CODE
    assert "545" in res.rejection_report[0]["reason"]
    assert "реєстрі" in res.rejection_report[0]["reason"]


def test_known_codes_are_not_flagged_as_unknown():
    from config.banks import bank_display_name, is_unmapped_code

    assert is_unmapped_code("545") is True
    assert is_unmapped_code("43") is False, "Monobank у реєстрі"
    assert is_unmapped_code("monobank") is False
    assert bank_display_name("545") == "код 545", "не вигадуємо назву"


# ═══════════════════════════════════════════════════════════════
# «Спитайте в чаті»
# ═══════════════════════════════════════════════════════════════

@pytest.mark.asyncio
async def test_route_outside_declared_banks_is_flagged(db):
    await _settings(db)
    await _enable(db, FEATURE_INTER_BANK)
    await _card(db, "pumb", 20000.0, "1111")

    res = await CardMatchingEngine(db).run(
        TEST_USER_ID, "pumb", 5000.0, "buy",
        banks=["pumb"], declared_banks=["monobank"],
    )

    assert res.status == "success"
    assert res.needs_confirmation_banks == ["pumb"]


@pytest.mark.asyncio
async def test_declared_bank_needs_no_confirmation(db):
    await _settings(db)
    await _card(db, "monobank", 20000.0, "1111")

    res = await CardMatchingEngine(db).run(
        TEST_USER_ID, "monobank", 5000.0, "buy",
        banks=["monobank"], declared_banks=["monobank", "pumb"],
    )
    assert res.needs_confirmation_banks == []


@pytest.mark.asyncio
async def test_no_declared_list_means_no_flagging(db):
    """Список банків мерчанта невідомий — мовчимо, а не вигадуємо попередження."""
    await _settings(db)
    await _card(db, "monobank", 20000.0, "1111")

    res = await CardMatchingEngine(db).run(TEST_USER_ID, "monobank", 5000.0, "buy")
    assert res.needs_confirmation_banks == []


# ═══════════════════════════════════════════════════════════════
# Маршрутизація й фічі
# ═══════════════════════════════════════════════════════════════

@pytest.mark.asyncio
async def test_route_is_single_bank_without_features(db):
    await _settings(db)
    await _card(db, "monobank", 10000.0, "1111")
    await _card(db, "pumb", 10000.0, "2222")

    route = await resolve_route(db, TEST_USER_ID, ["43"], primary_bank="monobank")
    assert route.banks == ["monobank"]
    assert route.inter_bank is False


@pytest.mark.asyncio
async def test_inter_bank_stays_within_declared_banks(db):
    """Без «ігнорувати фільтр» кошик не виходить за межі оголошення."""
    await _settings(db)
    await _enable(db, FEATURE_INTER_BANK)
    await _card(db, "monobank", 10000.0, "1111")
    await _card(db, "pumb", 10000.0, "2222")
    await _card(db, "sense", 10000.0, "3333")

    route = await resolve_route(db, TEST_USER_ID, ["43", "64"], primary_bank="monobank")
    assert set(route.banks) == {"monobank", "pumb"}
    assert "sense" not in route.banks


@pytest.mark.asyncio
async def test_ignoring_merchant_filter_opens_all_own_banks(db):
    await _settings(db)
    await _enable(db, FEATURE_INTER_BANK, FEATURE_IGNORE_MERCHANT_BANKS)
    await _card(db, "monobank", 10000.0, "1111")
    await _card(db, "pumb", 10000.0, "2222")
    await _card(db, "sense", 10000.0, "3333")

    route = await resolve_route(db, TEST_USER_ID, ["43"], primary_bank="monobank")
    assert set(route.banks) == {"monobank", "pumb", "sense"}
    assert route.declared == ["monobank"]


@pytest.mark.asyncio
async def test_split_mode_inter_bank_requires_the_feature(db):
    await _settings(db, card_split_mode=SPLIT_INTER_BANK)
    assert (await db.get_user_card_settings(TEST_USER_ID))["card_split_mode"] == SPLIT_INTRA_BANK

    await _enable(db, FEATURE_INTER_BANK)
    await _settings(db, card_split_mode=SPLIT_INTER_BANK)
    assert (await db.get_user_card_settings(TEST_USER_ID))["card_split_mode"] == SPLIT_INTER_BANK


def test_available_split_modes_follows_the_feature():
    assert SPLIT_INTER_BANK not in available_split_modes(False)
    assert SPLIT_INTER_BANK in available_split_modes(True)


# ═══════════════════════════════════════════════════════════════
# Наскрізний прохід сканера
# ═══════════════════════════════════════════════════════════════

@pytest.mark.asyncio
async def test_scanner_ignores_merchant_bank_filter_when_asked(db):
    """
    Мерчант приймає лише Monobank, картки в нас лише в ПУМБ. Досі це була
    беззаперечна відмова; з увімкненою фічею ордер проходить, а домовлятись
    іде людина.
    """
    await _settings(db)
    await _card(db, "pumb", 50000.0, "1111")

    user = {
        "user_id": TEST_USER_ID, "chat_id": TEST_USER_ID,
        "scanner_mode": "TAKER_BUY", "bank_codes": ["43"],
        "taker_buy_amount": 100.0, "taker_buy_price_strategy": "any",
        "taker_buy_speed": "ANY", "buy_balance_mode": "MANUAL_STRICT",
        "capital": 0.0,
    }
    scanner = TakerScanner(db)

    before = await scanner.scan(user, {"43": [_order()]}, {})
    assert before.orders == [], "без фічі — стара поведінка"

    await _enable(db, FEATURE_INTER_BANK, FEATURE_IGNORE_MERCHANT_BANKS)
    after = await scanner.scan(user, {"43": [_order()]}, {})
    assert [o.id for o in after.orders] == ["o1"]


@pytest.mark.asyncio
async def test_scanner_capital_spans_banks_with_the_feature(db):
    """
    Ордер вимагає більше, ніж є в одному банку, але менше за суму по всіх.
    Без фічі — відмова, з фічею — угода.
    """
    await _settings(db, max_cards_per_order=3)
    await _card(db, "monobank", 12000.0, "1111")
    await _card(db, "pumb", 12000.0, "2222")

    user = {
        "user_id": TEST_USER_ID, "chat_id": TEST_USER_ID,
        "scanner_mode": "TAKER_BUY", "bank_codes": ["43", "64"],
        "taker_buy_amount": 500.0, "taker_buy_price_strategy": "any",
        "taker_buy_speed": "ANY", "buy_balance_mode": "CARD_ENFORCED",
        "capital": 0.0,
    }
    order = _order("o1", price=44.0, banks=["43", "64"])
    scanner = TakerScanner(db)

    before = await scanner.scan(user, {"43": [order]}, {})
    assert before.orders == [], "22 000 ₴ в одному банку не набереться"

    await _enable(db, FEATURE_INTER_BANK)
    await _settings(db, card_split_mode=SPLIT_INTER_BANK, max_cards_per_order=3)
    after = await scanner.scan(user, {"43": [order]}, {})
    assert [o.id for o in after.orders] == ["o1"]
