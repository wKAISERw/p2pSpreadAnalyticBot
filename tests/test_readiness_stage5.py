"""
tests/test_readiness_stage5.py

Етап 5 плану PLAN_CARD_MATCHING.md — попередження в майстрі та онбординг.

Досі всі ці перевірки жили в циклі сканера: людина налаштовувала режим,
тиснула «Запустити» і чекала, а причина, з якої алерти не приходили, лежала
в налаштуваннях і була видна одразу. Тут перевіряється, що майстер називає
ту саму причину, яку потім назве движок — розбіжність «майстер сказав ок,
а сканер мовчить» була б гіршою за відсутність перевірки.
"""
import sys
import time
import uuid
import asyncio
import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

import pytest

from core.engine.readiness import (
    BLOCKER, NOTE, WARNING, check_taker_readiness, has_blockers,
    readiness_block, render_checks,
)
from core.storage.merchant_db import MerchantDB


TEST_USER_ID = 88801


@pytest.fixture
def event_loop():
    loop = asyncio.new_event_loop()
    yield loop
    loop.close()


@pytest.fixture
async def db(tmp_path):
    merchant_db = MerchantDB(db_path=tmp_path / "test_readiness.db")
    await merchant_db.start()
    yield merchant_db
    await merchant_db.stop()


async def _card_module(db: MerchantDB, **over) -> None:
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


async def _card(db: MerchantDB, bank: str, balance: float, last_four: str = "1111") -> str:
    card_id = str(uuid.uuid4())
    await db.add_card({
        "id": card_id, "owner_id": TEST_USER_ID, "bank_name": bank,
        "last_four": last_four, "label": "T", "is_own": 1,
        "balance": balance, "status": "active", "cooldown_until": 0,
        "last_monthly_reset": time.time(), "created_at": time.time(),
        "last_tx_timestamp": 0, "is_warmed_up": 1,
    })
    return card_id


def _user(**over) -> dict:
    base = {
        "user_id": TEST_USER_ID,
        "scanner_mode": "TAKER_BUY",
        "bank_codes": ["43"],
        "buy_bank_codes": ["43"],
        "taker_buy_amount": 500.0,
        "taker_buy_max_price": 44.0,
        "buy_balance_mode": "CARD_ENFORCED",
    }
    base.update(over)
    return base


def _codes(checks):
    return [(c.level, c.text) for c in checks]


# ═══════════════════════════════════════════════════════════════
# Блокери
# ═══════════════════════════════════════════════════════════════

@pytest.mark.asyncio
async def test_card_module_on_without_cards_is_a_blocker(db):
    """Найтихіший зі сценаріїв: модуль увімкнено, карток нема, алертів нуль."""
    await _card_module(db)

    checks = await check_taker_readiness(db, _user(), "TAKER_BUY")

    assert has_blockers(checks)
    assert "активних карток немає" in checks[0].text
    # Далі перевіряти безпредметно — жоден ордер не пройде.
    assert len(checks) == 1


@pytest.mark.asyncio
async def test_selected_banks_without_any_card_is_a_blocker(db):
    """Мерчант приймає оплату лише своїм банком — це не «менше ордерів», а нуль."""
    await _card_module(db)
    await _card(db, "pumb", 50000.0)

    checks = await check_taker_readiness(db, _user(bank_codes=["43"]), "TAKER_BUY")

    assert has_blockers(checks)
    assert any("Жоден з обраних банків" in c.text for c in checks)


@pytest.mark.asyncio
async def test_partial_bank_coverage_is_a_warning_not_a_blocker(db):
    await _card_module(db)
    await _card(db, "monobank", 50000.0)

    checks = await check_taker_readiness(
        db, _user(bank_codes=["43", "64"], buy_bank_codes=["43", "64"]), "TAKER_BUY"
    )

    assert not has_blockers(checks)
    assert any(c.level == WARNING and "Немає карток для 1" in c.text for c in checks)


# ═══════════════════════════════════════════════════════════════
# Обсяг проти капіталу
# ═══════════════════════════════════════════════════════════════

@pytest.mark.asyncio
async def test_volume_above_capital_warns_with_numbers(db):
    """Сценарій із логу: 700 USDT проти 21 298 ₴ на Монобанку."""
    await _card_module(db)
    await _card(db, "monobank", 21298.0)

    checks = await check_taker_readiness(
        db, _user(taker_buy_amount=700.0, taker_buy_max_price=44.35), "TAKER_BUY"
    )

    hit = next(c for c in checks if "Обсягу купівлі не вистачає" in c.text)
    assert hit.level == WARNING
    assert "21 298" in hit.hint
    assert "авто-масштабування" in hit.hint.lower()


@pytest.mark.asyncio
async def test_autoscale_downgrades_the_volume_warning_to_a_note(db):
    """З увімкненим авто-масштабуванням це вже не проблема, а факт."""
    await _card_module(db)
    await _card(db, "monobank", 21298.0)

    checks = await check_taker_readiness(
        db,
        _user(taker_buy_amount=700.0, taker_buy_max_price=44.35,
              buy_balance_mode="AUTO_SCALE"),
        "TAKER_BUY",
    )

    hit = next(c for c in checks if "Обсягу купівлі не вистачає" in c.text)
    assert hit.level == NOTE


@pytest.mark.asyncio
async def test_money_in_other_banks_is_named_explicitly(db):
    """
    Різниця «є всього» проти «піде в одну угоду» — та сама, через яку
    користувач бачив 31 123 ₴ у меню й 21 298 ₴ в масштабуванні.
    """
    await _card_module(db)
    await _card(db, "monobank", 21298.0, "1111")
    await _card(db, "pumb", 9825.0, "2222")

    checks = await check_taker_readiness(
        db,
        _user(taker_buy_amount=700.0, taker_buy_max_price=44.35,
              bank_codes=["43", "64"], buy_bank_codes=["43", "64"]),
        "TAKER_BUY",
    )

    hit = next(c for c in checks if "Обсягу купівлі не вистачає" in c.text)
    assert "різних банках" in hit.hint


@pytest.mark.asyncio
async def test_warning_stops_claiming_one_bank_when_baskets_are_on(db):
    """
    Текст «зібрати їх в одну угоду движок поки не вміє» писався до етапу 3.
    Після вмикання кошиків він казав користувачу неправду про його ж
    налаштування — і саме це той побачив на дашборді.
    """
    from config.card_limits import FEATURE_INTER_BANK

    await _card_module(db)
    await _card(db, "monobank", 21298.0, "1111")
    await _card(db, "pumb", 5005.0, "2222")
    await _card(db, "sense", 4820.0, "3333")

    user = _user(taker_buy_amount=700.0, taker_buy_max_price=44.35,
                 bank_codes=["43", "64", "328"], buy_bank_codes=["43", "64", "328"])

    before = await check_taker_readiness(db, user, "TAKER_BUY")
    hit = next(c for c in before if "Обсягу купівлі" in c.text)
    assert "поки не вміє" in hit.hint
    assert "/features" in hit.hint, "маємо сказати, де це вмикається"

    await db._db.execute(
        "INSERT OR REPLACE INTO user_features (user_id, feature_key, is_enabled) "
        "VALUES (?, ?, 1)", (TEST_USER_ID, FEATURE_INTER_BANK),
    )
    await db._db.commit()

    after = await check_taker_readiness(db, user, "TAKER_BUY")
    hit = next((c for c in after if "Обсягу купівлі" in c.text), None)
    if hit:
        assert "поки не вміє" not in hit.hint
        assert "на всіх картках разом" in hit.hint


@pytest.mark.asyncio
async def test_capital_ceiling_follows_the_feature(db):
    """usable перестає бути «максимум по одному банку», коли кошики увімкнені."""
    from config.card_limits import FEATURE_INTER_BANK

    await _card_module(db)
    await _card(db, "monobank", 21298.0, "1111")
    await _card(db, "pumb", 5005.0, "2222")

    before = await db.get_user_capital_breakdown(TEST_USER_ID)
    assert before["usable"] == pytest.approx(21298.0)
    assert before["best_bank"] == "monobank"
    assert before["inter_bank"] is False

    await db._db.execute(
        "INSERT OR REPLACE INTO user_features (user_id, feature_key, is_enabled) "
        "VALUES (?, ?, 1)", (TEST_USER_ID, FEATURE_INTER_BANK),
    )
    await db._db.commit()

    after = await db.get_user_capital_breakdown(TEST_USER_ID)
    assert after["usable"] == pytest.approx(after["total"])
    assert after["best_bank"] == "", "маршрут з кількох банків, одна назва вводила б в оману"
    assert after["inter_bank"] is True


@pytest.mark.asyncio
async def test_missing_bank_cards_are_not_a_blocker_when_filter_is_off(db):
    """
    З «Ігнорувати фільтр банків мерчанта» відсутність картки обраного банку
    вже не блокер: ордер підбереться на інші картки.
    """
    from config.card_limits import FEATURE_IGNORE_MERCHANT_BANKS, FEATURE_INTER_BANK

    await _card_module(db)
    await _card(db, "pumb", 50000.0, "2222")

    user = _user(bank_codes=["43"], buy_bank_codes=["43"], taker_buy_amount=0.0)
    assert has_blockers(await check_taker_readiness(db, user, "TAKER_BUY"))

    for key in (FEATURE_INTER_BANK, FEATURE_IGNORE_MERCHANT_BANKS):
        await db._db.execute(
            "INSERT OR REPLACE INTO user_features (user_id, feature_key, is_enabled) "
            "VALUES (?, ?, 1)", (TEST_USER_ID, key),
        )
    await db._db.commit()

    checks = await check_taker_readiness(db, user, "TAKER_BUY")
    assert not has_blockers(checks)
    assert any("фільтр банків мерчанта вимкнено" in c.text for c in checks)


@pytest.mark.asyncio
async def test_enough_capital_produces_no_volume_warning(db):
    await _card_module(db)
    await _card(db, "monobank", 50000.0)

    checks = await check_taker_readiness(
        db, _user(taker_buy_amount=500.0, taker_buy_max_price=44.0), "TAKER_BUY"
    )
    assert not any("Обсягу купівлі" in c.text for c in checks)
    assert not has_blockers(checks)


# ═══════════════════════════════════════════════════════════════
# Місячна межа довідника
# ═══════════════════════════════════════════════════════════════

@pytest.mark.asyncio
async def test_warns_when_monthly_turnover_is_nearly_spent(db):
    """
    Межа з довідника, а не банківський ліміт: обсяг упреться в стелю
    раніше, ніж скінчаться гроші, і це варто знати наперед.
    """
    await _card_module(db)
    card_id = await _card(db, "monobank", 200000.0)

    # Monobank: рекомендовано 100 000 ₴/міс. Проводимо 85 000 ₴.
    await db.confirm_transaction(card_id, 85000.0, "out", "work", source="test")

    checks = await check_taker_readiness(
        db, _user(taker_buy_amount=100.0, taker_buy_max_price=44.0), "TAKER_BUY"
    )

    hit = next(c for c in checks if "місячний оборот" in c.text.lower())
    assert hit.level == WARNING
    assert "85 000" in hit.text and "100 000" in hit.text
    assert "/set_bank_limits" in hit.hint


# ═══════════════════════════════════════════════════════════════
# Налаштування, які самі себе обмежують
# ═══════════════════════════════════════════════════════════════

@pytest.mark.asyncio
async def test_split_off_is_reported_as_a_note(db):
    await _card_module(db, card_split_mode="off")
    await _card(db, "monobank", 50000.0)

    checks = await check_taker_readiness(
        db, _user(taker_buy_amount=100.0, taker_buy_max_price=44.0), "TAKER_BUY"
    )
    assert any(c.level == NOTE and "Спліт карток вимкнено" in c.text for c in checks)


@pytest.mark.asyncio
async def test_module_off_says_cards_are_not_checked(db):
    await _card_module(db)
    await db._db.execute(
        "UPDATE user_card_settings SET enable_in_single_modes=0 WHERE user_id=?",
        (TEST_USER_ID,),
    )
    await db._db.commit()

    checks = await check_taker_readiness(db, _user(), "TAKER_BUY")
    assert [c.level for c in checks] == [NOTE]
    assert "не бере участі" in checks[0].text


# ═══════════════════════════════════════════════════════════════
# Форма подачі
# ═══════════════════════════════════════════════════════════════

def test_render_orders_blockers_before_notes():
    from core.engine.readiness import Check

    text = render_checks([
        Check(NOTE, "нотатка"),
        Check(BLOCKER, "блокер"),
        Check(WARNING, "ворнінг"),
    ])
    assert text.index("блокер") < text.index("ворнінг") < text.index("нотатка")


def test_render_is_empty_when_everything_fits():
    assert render_checks([]) == ""


@pytest.mark.asyncio
async def test_readiness_block_never_breaks_the_launch(db):
    """Перевірка допоміжна: якщо вона впала, режим усе одно має запуститись."""
    class _BrokenDb:
        async def get_user_by_id(self, user_id):
            raise RuntimeError("база впала")

    assert await readiness_block(_BrokenDb(), TEST_USER_ID, "TAKER_BUY") == ""
    assert await readiness_block(None, TEST_USER_ID, "TAKER_BUY") == ""


@pytest.mark.asyncio
async def test_non_taker_modes_are_not_checked(db):
    await _card_module(db)
    assert await check_taker_readiness(db, _user(), "SPREAD") == []
    assert await check_taker_readiness(db, _user(), "MAKER_BUY") == []
