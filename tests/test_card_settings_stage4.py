"""
tests/test_card_settings_stage4.py

Етап 4 плану PLAN_CARD_MATCHING.md — налаштування матчингу карток.

Три речі, які досі або не існували, або існували лише в схемі:

* `max_cards_per_order` лежав у таблиці з дефолтом 3, движок сплітів його
  читав, `card_notifier` радив «перевірте max_cards_per_order у
  налаштуваннях» — а меню, яке дозволяє його змінити, не було, і жоден
  запис у це поле не доходив;
* режим спліту не існував узагалі: движок завжди робив те, що вмів;
* показ відхилених ордерів не існував, бо до етапу 2 не було й причин.
"""
import sys
import time
import uuid
import asyncio
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

import pytest

from config.card_limits import (
    SPLIT_INTER_BANK, SPLIT_INTRA_BANK, SPLIT_MODES, SPLIT_MODES_AVAILABLE,
)
from core.engine import rejection_codes as rc
from core.engine.card_matching_engine import CardMatchingEngine
from core.storage.merchant_db import MerchantDB


TEST_USER_ID = 77701


@pytest.fixture
def event_loop():
    loop = asyncio.new_event_loop()
    yield loop
    loop.close()


@pytest.fixture
async def db(tmp_path):
    merchant_db = MerchantDB(db_path=tmp_path / "test_stage4.db")
    await merchant_db.start()
    yield merchant_db
    await merchant_db.stop()


async def _settings(db: MerchantDB, **over) -> None:
    await db._db.execute(
        "INSERT OR IGNORE INTO user_card_settings (user_id, card_module_mode) "
        "VALUES (?, 'full')", (TEST_USER_ID,),
    )
    await db._db.commit()
    if over:
        current = await db.get_user_card_settings(TEST_USER_ID) or {}
        current.update(over)
        await db.update_user_card_settings(TEST_USER_ID, current)


async def _card(db: MerchantDB, balance: float, last_four: str,
                bank: str = "monobank") -> str:
    card_id = str(uuid.uuid4())
    await db.add_card({
        "id": card_id, "owner_id": TEST_USER_ID, "bank_name": bank,
        "last_four": last_four, "label": f"Test {last_four}", "is_own": 1,
        "balance": balance, "status": "active", "cooldown_until": 0,
        "last_monthly_reset": time.time(), "created_at": time.time(),
        "last_tx_timestamp": 0, "is_warmed_up": 1,
    })
    return card_id


# ═══════════════════════════════════════════════════════════════
# Режим спліту
# ═══════════════════════════════════════════════════════════════

@pytest.mark.asyncio
async def test_split_off_rejects_what_one_card_cannot_cover(db):
    """
    «Вимкнено» — це свідомий вибір, а не збій.

    Мерчанти часто пишуть «тільки одним платежем», і три перекази через три
    застосунки за 15 хвилин таймера — реальний ризик апеляції.
    """
    await _settings(db, card_split_mode="off")
    await _card(db, 6000.0, "1111")
    await _card(db, 6000.0, "2222")

    res = await CardMatchingEngine(db).run(TEST_USER_ID, "monobank", 10000.0, "buy")

    assert res.status == "no_cards"
    assert res.rejection_report[-1]["code"] == rc.SPLIT_DISABLED
    assert "спліт вимкнено" in res.rejection_report[-1]["reason"].lower()


@pytest.mark.asyncio
async def test_split_off_still_takes_a_single_sufficient_card(db):
    await _settings(db, card_split_mode="off")
    await _card(db, 20000.0, "1111")

    res = await CardMatchingEngine(db).run(TEST_USER_ID, "monobank", 10000.0, "buy")
    assert res.status == "success"


@pytest.mark.asyncio
async def test_intra_bank_split_is_the_default(db):
    """Дефолт дорівнює поточній поведінці — налаштованим людям нічого не ламає."""
    await _settings(db)
    settings = await db.get_user_card_settings(TEST_USER_ID)
    assert (settings.get("card_split_mode") or SPLIT_INTRA_BANK) == SPLIT_INTRA_BANK

    await _card(db, 6000.0, "1111")
    await _card(db, 6000.0, "2222")
    res = await CardMatchingEngine(db).run(TEST_USER_ID, "monobank", 10000.0, "buy")
    assert res.status == "needs_split"


@pytest.mark.asyncio
async def test_inter_bank_is_not_accepted_while_engine_cannot_do_it(db):
    """
    Движок збирає суму лише в межах одного банку. Поки це так, зберігати
    вибір «між банками» означало б записати налаштування, яке нічого не
    змінює, — мовчазна брехня інтерфейсу.
    """
    assert SPLIT_INTER_BANK in SPLIT_MODES
    assert SPLIT_INTER_BANK not in SPLIT_MODES_AVAILABLE

    await _settings(db, card_split_mode=SPLIT_INTER_BANK)
    settings = await db.get_user_card_settings(TEST_USER_ID)
    assert settings["card_split_mode"] == SPLIT_INTRA_BANK


# ═══════════════════════════════════════════════════════════════
# Максимум карток на угоду
# ═══════════════════════════════════════════════════════════════

@pytest.mark.asyncio
async def test_max_cards_per_order_is_finally_persisted(db):
    """Поле було в схемі, але запис у нього не доходив — меню його не бачило."""
    await _settings(db, max_cards_per_order=1)
    assert (await db.get_user_card_settings(TEST_USER_ID))["max_cards_per_order"] == 1


@pytest.mark.asyncio
async def test_max_cards_per_order_is_clamped(db):
    await _settings(db, max_cards_per_order=99)
    assert (await db.get_user_card_settings(TEST_USER_ID))["max_cards_per_order"] == 3

    await _settings(db, max_cards_per_order=0)
    assert (await db.get_user_card_settings(TEST_USER_ID))["max_cards_per_order"] == 1


@pytest.mark.asyncio
async def test_max_cards_limits_the_split_width(db):
    """Три картки по 4к: під 10к треба три, при ліміті 2 — не складається."""
    await _settings(db, max_cards_per_order=2)
    await _card(db, 4000.0, "1111")
    await _card(db, 4000.0, "2222")
    await _card(db, 4000.0, "3333")

    engine = CardMatchingEngine(db)
    assert (await engine.run(TEST_USER_ID, "monobank", 10000.0, "buy")).status == "no_cards"

    await _settings(db, max_cards_per_order=3)
    res = await engine.run(TEST_USER_ID, "monobank", 10000.0, "buy")
    assert res.status == "needs_split"
    assert any(len(s) == 3 for s in res.split_options)


# ═══════════════════════════════════════════════════════════════
# Показ відхилених ордерів
# ═══════════════════════════════════════════════════════════════

@pytest.mark.asyncio
async def test_show_rejected_orders_round_trip(db):
    await _settings(db, show_rejected_orders="hide")
    assert (await db.get_user_card_settings(TEST_USER_ID))["show_rejected_orders"] == "hide"

    await _settings(db, show_rejected_orders="казна-що")
    assert (await db.get_user_card_settings(TEST_USER_ID))["show_rejected_orders"] == "with_reason"


@pytest.mark.asyncio
async def test_hide_silences_the_digest():
    from core.engine import scanner_helpers
    from core.engine.taker_scanner import OrderRejection

    sent = []

    class FakeNotifier:
        async def send_plain(self, chat_id, text):
            sent.append(text)

    rejections = [OrderRejection(
        order_id="o1", merchant_name="M", exchange="Binance", bank="monobank",
        price=44.0, min_limit=1000.0, code=rc.INSUFFICIENT_BALANCE,
        reason="не вистачає 8 702 ₴", shortfall_uah=8702.0,
    )]
    user = {"user_id": TEST_USER_ID, "chat_id": TEST_USER_ID}

    await scanner_helpers._notify_all_rejected(
        FakeNotifier(), user, "TAKER_BUY", rejections, show_rejected="hide"
    )
    assert sent == []

    scanner_helpers._last_rejection_digest.clear()
    await scanner_helpers._notify_all_rejected(
        FakeNotifier(), user, "TAKER_BUY", rejections, show_rejected="with_reason"
    )
    assert len(sent) == 1
    assert "8 702" in sent[0]
    assert "M" in sent[0]


@pytest.mark.asyncio
async def test_digest_is_throttled_per_reason():
    from core.engine import scanner_helpers
    from core.engine.taker_scanner import OrderRejection

    scanner_helpers._last_rejection_digest.clear()
    sent = []

    class FakeNotifier:
        async def send_plain(self, chat_id, text):
            sent.append(text)

    def _rej(code):
        return OrderRejection(
            order_id="o1", merchant_name="M", exchange="Binance", bank="monobank",
            price=44.0, min_limit=1000.0, code=code, reason="причина",
        )

    user = {"user_id": TEST_USER_ID + 1, "chat_id": TEST_USER_ID + 1}
    for _ in range(3):
        await scanner_helpers._notify_all_rejected(
            FakeNotifier(), user, "TAKER_BUY", [_rej(rc.COOLDOWN)]
        )
    assert len(sent) == 1, "та сама причина не має повторюватись щоцикла"

    # Інша причина — інша ситуація, про неї сказати варто.
    await scanner_helpers._notify_all_rejected(
        FakeNotifier(), user, "TAKER_BUY", [_rej(rc.MAX_TX_PER_DAY)]
    )
    assert len(sent) == 2
