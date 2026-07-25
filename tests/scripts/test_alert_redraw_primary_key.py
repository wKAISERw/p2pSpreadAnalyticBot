import asyncio
import os
import sys
from pathlib import Path
from decimal import Decimal

# Add project root to path
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "../..")))

from core.storage.merchant_db import MerchantDB
from exchanges.base import Order
from bot.alert_builder import SpreadAlert

TEST_DB_PATH = Path("data/test_merchants_redraw_3.db")

async def run_tests():
    print("Starting tests...")
    
    # 1. Cleanup old test DB if exists
    for suffix in ("", "-wal", "-shm"):
        p = Path(str(TEST_DB_PATH) + suffix)
        if p.exists():
            try:
                p.unlink()
            except Exception:
                pass

    # 2. Instantiate and start DB (runs _init_schema)
    db = MerchantDB(TEST_DB_PATH)
    await db.start()
    print("DB started and initialized schema.")

    # Verify table schema of sent_alerts
    async with db._db.execute("PRAGMA table_info(sent_alerts)") as cur:
        rows = await cur.fetchall()
    
    pk_cols = [r["name"] for r in rows if r["pk"] > 0]
    print(f"Primary key columns: {pk_cols}")
    assert "exchange" in pk_cols, "exchange should be in primary key!"
    assert "merchant_id" in pk_cols, "merchant_id should be in primary key!"
    assert "chat_id" in pk_cols, "chat_id should be in primary key!"
    assert "message_ids_json" in pk_cols, "message_ids_json should be in primary key!"
    assert len(pk_cols) == 4, f"PK should have exactly 4 columns, got {len(pk_cols)}"
    print("Primary Key constraint verification passed.")

    # 3. Verify insertion of alert with both buy and sell legs
    buy_order = Order(
        id="buy_123",
        price=Decimal("44.50"),
        available_amount=Decimal("100"),
        min_limit=Decimal("1000"),
        max_limit=Decimal("5000"),
        merchant_id="merchant_buy_id_111",
        merchant_name="Merchant Buy",
        month_order_count=100,
        finish_rate_pct=99.0,
        exchange="Binance",
        risk_flag="OK"
    )

    sell_order = Order(
        id="sell_456",
        price=Decimal("45.00"),
        available_amount=Decimal("100"),
        min_limit=Decimal("1000"),
        max_limit=Decimal("5000"),
        merchant_id="merchant_sell_id_222",
        merchant_name="Merchant Sell",
        month_order_count=200,
        finish_rate_pct=98.0,
        exchange="Binance",
        risk_flag="OK"
    )

    alert = SpreadAlert(
        buy_order=buy_order,
        sell_order=sell_order,
        spread_pct=1.12,
        profit_uah=50.0,
        deal_amount_uah=4450.0,
        buy_bank="43",
        sell_bank="14",
        buy_banks_all=["43"],
        sell_banks_all=["14"],
        buy_banks_fit=["43"],
        sell_banks_fit=["14"],
        route_variants=["Mono -> Privat"],
        route_type="CROSS"
    )

    chat_id = 999999
    sent_ids = [12345, 12346]
    display = {"show_llm_summary": True}

    # Simulate notifier.py serialization and save
    from bot.notifier import TelegramNotifier
    notifier = TelegramNotifier()
    notifier.bind_db(db)
    
    alert_dict = notifier._serialize_alert(alert)
    
    # Save for buy merchant
    await db.save_sent_alert(
        alert.buy_order.exchange,
        alert.buy_order.merchant_id,
        chat_id,
        sent_ids,
        alert_dict,
        display,
        is_sniper=False
    )

    # Save for sell merchant
    await db.save_sent_alert(
        alert.sell_order.exchange,
        alert.sell_order.merchant_id,
        chat_id,
        sent_ids,
        alert_dict,
        display,
        is_sniper=False
    )

    # Now verify both rows exist
    async with db._db.execute("SELECT * FROM sent_alerts") as cur:
        all_sent = await cur.fetchall()
    
    print(f"Total sent alerts stored: {len(all_sent)}")
    assert len(all_sent) == 2, f"Should store exactly 2 rows for the alert, got {len(all_sent)}"
    
    mids_stored = {r["merchant_id"] for r in all_sent}
    assert "merchant_buy_id_111" in mids_stored, "Buy merchant ID missing!"
    assert "merchant_sell_id_222" in mids_stored, "Sell merchant ID missing!"
    print("Double merchant mapping insert verification passed.")

    # 4. Verify update_sent_alert_dict updates alert_json on both rows
    updated_alert_dict = dict(alert_dict)
    updated_alert_dict["spread_pct"] = 2.50
    updated_alert_dict["profit_uah"] = 120.0

    await db.update_sent_alert_dict(chat_id, sent_ids, updated_alert_dict)

    # Query again and check if updated in both rows
    async with db._db.execute("SELECT * FROM sent_alerts") as cur:
        all_sent_updated = await cur.fetchall()

    import json
    for r in all_sent_updated:
        data = json.loads(r["alert_json"])
        assert float(data["spread_pct"]) == 2.50, f"Expected spread_pct 2.50, got {data['spread_pct']}"
        assert float(data["profit_uah"]) == 120.0, f"Expected profit_uah 120.0, got {data['profit_uah']}"
    
    print("update_sent_alert_dict successfully updated all matching alert records.")
    print("Redraw mechanism test passed successfully!")

    # 5. Now verify card balance projection math with fallback buy_card_id
    print("\nStarting Card Balance Projection Test...")
    # Insert user card settings and card info
    await db._db.execute("INSERT OR REPLACE INTO user_card_settings (user_id, card_module_mode) VALUES (?, 'full')", (999999,))
    await db._db.execute("""
        INSERT INTO cards (id, owner_id, bank_name, last_four, is_own, balance, status, category)
        VALUES ('card_mono_123', 999999, 'monobank', '6251', 1, 11690.10, 'active', 'self')
    """)
    await db._db.commit()

    # Create new alert for Monobank -> Monobank
    buy_order2 = Order(
        id="buy_123_mono",
        price=Decimal("44.68"),
        available_amount=Decimal("500"),
        min_limit=Decimal("10000.00"),
        max_limit=Decimal("17700.00"),
        merchant_id="merchant_buy_mono",
        merchant_name="Buy Merchant Mono",
        month_order_count=100,
        finish_rate_pct=99.0,
        exchange="Bybit",
        risk_flag="OK"
    )

    sell_order2 = Order(
        id="sell_456_mono",
        price=Decimal("46.65"),
        available_amount=Decimal("20000"),
        min_limit=Decimal("13000"),
        max_limit=Decimal("30000"),
        merchant_id="merchant_sell_mono",
        merchant_name="Sell Merchant Mono",
        month_order_count=200,
        finish_rate_pct=98.0,
        exchange="Binance",
        risk_flag="OK"
    )

    alert2 = SpreadAlert(
        buy_order=buy_order2,
        sell_order=sell_order2,
        spread_pct=4.41,
        profit_uah=567.01,
        deal_amount_uah=12870.0,
        buy_bank="43",
        sell_bank="43",
        buy_banks_all=["43"],
        sell_banks_all=["43"],
        buy_banks_fit=["43"],
        sell_banks_fit=["43"],
        route_variants=["Mono -> Mono"],
        route_type="CROSS"
    )

    from bot.formatters import _bank_code_to_db

    buy_target = alert2.deal_amount_uah
    sell_target = alert2.deal_amount_uah + alert2.profit_uah

    # Match BUY card
    buy_card_text, buy_card_rows, buy_card_obj = await notifier.card_notifier.get_card_block(
        chat_id=999999,
        target_amount=buy_target,
        direction="buy",
        bank=_bank_code_to_db(alert2.buy_bank or ""),
        order_id="b_ad_123_mono"
    )

    # Ensure buy_card_obj is None because 11690.10 < 12870.0
    print(f"buy_card_obj: {buy_card_obj}")
    assert buy_card_obj is None, "Buy card should be None due to insufficient balance"

    # Verify fallback buy_card_id matches card_mono_123
    buy_card_id = None
    if buy_card_obj:
        buy_card_id = buy_card_obj.get("id")
    else:
        try:
            owner_uid = 999999
            buy_bank_db = _bank_code_to_db(alert2.buy_bank or "")
            buy_cards = await notifier._db.get_cards(owner_id=owner_uid, bank_name=buy_bank_db, status="active")
            if buy_cards:
                buy_card_id = buy_cards[0].get("id")
        except Exception as e:
            pass

    print(f"Fallback buy_card_id resolved to: {buy_card_id}")
    assert buy_card_id == "card_mono_123", f"Expected buy_card_id 'card_mono_123', got {buy_card_id}"

    # Match SELL card
    sell_card_text, sell_card_rows, sell_card_obj = await notifier.card_notifier.get_card_block(
        chat_id=999999,
        target_amount=sell_target,
        direction="sell",
        bank=_bank_code_to_db(alert2.sell_bank or ""),
        order_id="s_ad_456_mono",
        buy_card_spent_fiat=buy_target,
        buy_card_id=buy_card_id
    )

    # Print safely without emojis to avoid encoding errors
    safe_text = sell_card_text.encode('ascii', errors='replace').decode('ascii')
    print("sell_card_text (safely printed):")
    print(safe_text)
    
    # 11690.10 - 12870 + 13437.01 = 12257.11 UAH.
    assert "12,257.11" in sell_card_text, f"Expected expected balance 12,257.11 in text, got:\n{sell_card_text}"
    assert "25,127.21" not in sell_card_text, "Error: Double-counting occurred! 25,127.21 was printed."
    print("Card Balance Projection Test passed successfully!")

    # 6. Clean up
    await db.stop()
    await notifier.stop()
    
    # Cleanup DB files
    for suffix in ("", "-wal", "-shm"):
        p = Path(str(TEST_DB_PATH) + suffix)
        if p.exists():
            try:
                p.unlink()
            except Exception:
                pass
    print("Cleanup completed successfully.")
    print("ALL TESTS PASSED SUCCESSFULLY!")

if __name__ == "__main__":
    asyncio.run(run_tests())
