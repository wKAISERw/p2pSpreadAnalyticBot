# tests/test_blacklist_integration.py
import sys
import time
import asyncio
from pathlib import Path
from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock

sys.path.insert(0, str(Path(__file__).parent.parent))

import pytest
import aiosqlite
from core.storage.merchant_db import MerchantDB
from core.engine.taker_scanner import TakerScanner
from core.engine.risk_engine import RiskEngine
from exchanges.base import Order
from bot.handlers.filters import cmd_ban, cmd_unban, _db as bot_db

USER_ID = 5001
OTHER_ID = 5002
ADMIN_ID = 5003


@pytest.fixture
def event_loop():
    loop = asyncio.new_event_loop()
    yield loop
    loop.close()


@pytest.fixture
async def db(tmp_path):
    db_path = tmp_path / "test_blacklist.db"
    merchant_db = MerchantDB(db_path=db_path)
    await merchant_db.start()
    
    # Bind the bot handler's DB reference to this test database
    import bot.handlers.filters
    bot.handlers.filters._db = merchant_db
    
    yield merchant_db
    await merchant_db.stop()


def make_order(merchant_name: str, exchange: str = "CryptoBot") -> Order:
    return Order(
        id="order123",
        price=Decimal("40.0"),
        available_amount=Decimal("100.0"),
        min_limit=Decimal("500"),
        max_limit=Decimal("10000"),
        merchant_id="merchant_123",
        merchant_name=merchant_name,
        month_order_count=100,
        finish_rate_pct=99.0,
        exchange=exchange,
        bank_codes=["43"],
    )


@pytest.mark.asyncio
async def test_blacklist_fallback_matching(db):
    # Додаємо тестові записи в блекліст
    await db.add_to_blacklist("Binance", "unk_tether_poshtuchno", "Tether_Poshtuchno", "Ref from Binance")
    await db.add_to_blacklist("CryptoBot", "unk_unborn_deer", "Unborn Deer", "Fake receipt")

    # 1. Точний збіг по ID та Біржі
    is_bl, reason = await db.is_blacklisted("Binance", "unk_tether_poshtuchno", "Tether_Poshtuchno")
    assert is_bl is True
    assert "Ref from Binance" in reason

    # 2. Фолбек-пошук за ім'ям (case-insensitive) на тій самій біржі
    is_bl, reason = await db.is_blacklisted("CryptoBot", "any_random_id", "unborn deer")
    assert is_bl is True
    assert "Fake receipt" in reason

    # 3. Міжбіржова перевірка (Cross-exchange warning)
    is_bl, reason = await db.is_blacklisted("CryptoBot", "any_random_id", "Tether_Poshtuchno")
    assert is_bl is True
    assert "blacklist на Binance" in reason

    # 4. Розумне очищення від спецсимволів/емодзі (Fuzzy match)
    # Перевіряємо збіг "unborn_deer", "💎 Unborn Deer", "Unborn Deer ⚡"
    is_bl, reason = await db.is_blacklisted("CryptoBot", "any_random_id", "unborn_deer")
    assert is_bl is True
    assert "Fake receipt" in reason

    is_bl, reason = await db.is_blacklisted("CryptoBot", "any_random_id", "💎 Unborn Deer")
    assert is_bl is True
    assert "Fake receipt" in reason

    is_bl, reason = await db.is_blacklisted("CryptoBot", "any_random_id", "Unborn Deer ⚡")
    assert is_bl is True
    assert "Fake receipt" in reason


@pytest.mark.asyncio
async def test_taker_scanner_blacklist_modes(db):
    scanner = TakerScanner(db)
    risk_engine = RiskEngine(db=db)

    # Додаємо Unborn Deer до блеклісту на CryptoBot
    await db.add_to_blacklist("CryptoBot", "unk_unborn_deer", "Unborn Deer", "Fake receipt")

    # Створюємо ордер
    order = make_order("Unborn Deer")

    # Запускаємо RiskEngine аналіз
    await risk_engine.analyze_for_spread([order])
    assert "BLOCK:BLACKLIST" in order.risk_flag

    # 1. Режим фільтрації "block" (За замовчуванням): ордер повністю приховується
    user_block = {
        "user_id": 111,
        "scanner_mode": "TAKER_BUY",
        "bank_codes": ["43"],
        "capital": 10000.0,
        "merchant_filters": {"blacklist_mode": "block"}
    }
    buy_grouped = {"43": [order]}
    orders_block = await scanner.find_orders_for_user(user_block, buy_grouped, {})
    assert len(orders_block) == 0

    # 2. Режим фільтрації "warn": ордер проходить, але попередження зберігається
    user_warn = {
        "user_id": 111,
        "scanner_mode": "TAKER_BUY",
        "bank_codes": ["43"],
        "capital": 10000.0,
        "merchant_filters": {"blacklist_mode": "warn"}
    }
    orders_warn = await scanner.find_orders_for_user(user_warn, buy_grouped, {})
    assert len(orders_warn) == 1
    assert orders_warn[0].merchant_name == "Unborn Deer"
    assert "BLOCK:BLACKLIST" in orders_warn[0].risk_flag


@pytest.mark.asyncio
async def test_bot_ban_writes_to_personal_list(db):
    """
    Звичайний користувач банить СОБІ.

    Раніше /ban від будь-кого писав у global_blacklist, тобто одна людина
    вимикала мерчанта всім користувачам бота.
    """
    message = MagicMock()
    message.answer = AsyncMock()
    message.from_user.id = USER_ID

    message.text = "/ban CryptoBot Spiky Blowfish : реф і скам"
    await cmd_ban(message)

    message.answer.assert_called_once()
    answer = message.answer.call_args[0][0]
    assert "Заблоковано в Чорному списку" in answer
    assert "особистий" in answer

    # У спільному списку його немає…
    is_bl, _ = await db.is_blacklisted("CryptoBot", "any_id", "Spiky Blowfish")
    assert is_bl is False
    # …а у власному — є.
    is_bl, reason = await db.is_user_blacklisted(
        USER_ID, "CryptoBot", "any_id", "Spiky Blowfish"
    )
    assert is_bl is True
    assert "реф і скам" in reason

    # Чужі алерти це не зачіпає.
    is_bl, _ = await db.is_user_blacklisted(
        OTHER_ID, "CryptoBot", "any_id", "Spiky Blowfish"
    )
    assert is_bl is False

    # /unban чистить власний список.
    message.answer.reset_mock()
    message.text = "/unban CryptoBot Spiky Blowfish"
    await cmd_unban(message)

    message.answer.assert_called_once()
    assert "Розблоковано" in message.answer.call_args[0][0]

    is_bl, _ = await db.is_user_blacklisted(
        USER_ID, "CryptoBot", "any_id", "Spiky Blowfish"
    )
    assert is_bl is False


@pytest.mark.asyncio
async def test_bot_ban_from_admin_is_shared(db, monkeypatch):
    """Бан адміністратора лишається спільним — він і має діяти на всіх."""
    monkeypatch.setattr("bot.handlers.filters._is_admin", lambda uid: uid == ADMIN_ID)

    message = MagicMock()
    message.answer = AsyncMock()
    message.from_user.id = ADMIN_ID
    message.text = "/ban CryptoBot Shared Scammer : скам з чеком"
    await cmd_ban(message)

    assert "спільний" in message.answer.call_args[0][0]
    is_bl, reason = await db.is_blacklisted("CryptoBot", "any_id", "Shared Scammer")
    assert is_bl is True
    assert "скам з чеком" in reason


@pytest.mark.asyncio
async def test_bot_blacklist_command_still_lists_shared(db):
    message = MagicMock()
    message.answer = AsyncMock()
    message.from_user.id = USER_ID

    await db.add_to_blacklist("CryptoBot", "unk_unborn_deer", "Unborn Deer", "Fake receipt")

    # /blacklist без аргументів — список
    from bot.handlers.filters import cmd_blacklist
    message.answer.reset_mock()
    message.text = "/blacklist"
    await cmd_blacklist(message)
    message.answer.assert_called_once()
    response_text = message.answer.call_args[0][0]
    assert "Чорний список мерчантів" in response_text
    assert "Unborn Deer" in response_text

    # /blacklist із пошуковим запитом
    message.answer.reset_mock()
    message.text = "/blacklist Deer"
    await cmd_blacklist(message)
    message.answer.assert_called_once()
    response_search = message.answer.call_args[0][0]
    assert "Результати пошуку для" in response_search
    assert "Unborn Deer" in response_search



@pytest.mark.asyncio
async def test_alert_dispatcher_blacklist_modes(db):
    from core.engine.alert_dispatcher import AlertDispatcher
    # Create alert dispatcher mock notifier
    notifier = MagicMock()
    dispatcher = AlertDispatcher(notifier=notifier, db=db)

    # Create dummy user, buy order (safe), sell order (blacklisted)
    buy_order = make_order("Safe Merchant", exchange="CryptoBot")
    sell_order = make_order("Unborn Deer", exchange="CryptoBot")
    sell_order.risk_flag = "BLOCK:BLACKLIST:Fake receipt"

    opp = {
        "buy_order": buy_order,
        "sell_order": sell_order,
        "net_spread_pct": 2.5,
        "actual_entry_uah": 1000.0,
        "buy_bank": "43",
        "sell_bank": "43",
        "buy_banks_fit": ["43"],
        "sell_banks_fit": ["43"],
        "route_type": "SPREAD"
    }

    # 1. User with blacklist_mode="block" -> should be filtered (is_ok is False)
    user_block = {
        "user_id": 111,
        "capital": 5000.0,
        "min_spread": 1.0,
        "bank_codes": ["43"],
        "buy_bank_codes": ["43"],
        "sell_bank_codes": ["43"],
        "merchant_filters": {"blacklist_mode": "block"}
    }
    is_ok, reason, _ = dispatcher._user_wants(user_block, opp)
    assert is_ok is False
    assert "BLACKLIST merchant" in reason

    # 2. User with blacklist_mode="warn" -> should pass (is_ok is True)
    user_warn = {
        "user_id": 222,
        "capital": 5000.0,
        "min_spread": 1.0,
        "bank_codes": ["43"],
        "buy_bank_codes": ["43"],
        "sell_bank_codes": ["43"],
        "merchant_filters": {"blacklist_mode": "warn"}
    }
    is_ok, reason, _ = dispatcher._user_wants(user_warn, opp)
    assert is_ok is True


@pytest.mark.asyncio
async def test_blacklist_ui_callbacks_and_inputs(db):
    from bot.handlers.filters import (
        on_blacklist_menu, on_blacklist_list, on_blacklist_unban_button,
        on_blacklist_search_input, on_blacklist_add_input
    )
    
    # 1. Test menu callback
    call = MagicMock()
    call.from_user.id = USER_ID
    call.message = MagicMock()
    call.message.edit_text = AsyncMock()
    await on_blacklist_menu(call)
    call.message.edit_text.assert_called_once()
    assert "Керування Чорним списком" in call.message.edit_text.call_args[0][0]

    # 2. Test list callback (when empty)
    call.message.edit_text.reset_mock()
    call.data = "blacklist:list:0"
    await on_blacklist_list(call)
    call.message.edit_text.assert_called_once()
    assert "порожній" in call.message.edit_text.call_args[0][0]

    # 3. Test add manual merchant via UI input
    message = MagicMock()
    message.answer = AsyncMock()
    message.from_user.id = USER_ID
    message.text = "CryptoBot Spiky Blowfish : реф"
    state = AsyncMock()
    await on_blacklist_add_input(message, state)
    message.answer.assert_called_once()
    assert "додано до Чорного списку" in message.answer.call_args[0][0]
    state.clear.assert_called_once()

    # 4. Test list callback (after adding)
    call.message.edit_text.reset_mock()
    await on_blacklist_list(call)
    call.message.edit_text.assert_called_once()
    assert "Spiky Blowfish" in call.message.edit_text.call_args[0][0]

    # 5. Test search input via UI
    message.answer.reset_mock()
    message.text = "Blowfish"
    state = AsyncMock()
    await on_blacklist_search_input(message, state)
    message.answer.assert_called_once()
    assert "Результати пошуку" in message.answer.call_args[0][0]
    assert "Spiky Blowfish" in message.answer.call_args[0][0]
    state.clear.assert_called_once()

    # 6. Test unban button callback
    call.answer = AsyncMock()
    call.data = "bl_u:personal:CryptoBot:unk_spiky_blowfish"
    call.message.edit_text.reset_mock()
    await on_blacklist_unban_button(call)
    call.answer.assert_called_once()
    assert "вилучено" in call.answer.call_args[0][0]
    assert "порожній" in call.message.edit_text.call_args[0][0]


