# scratch/test_taker_redraw.py
import asyncio
import os
import sys
from pathlib import Path
from decimal import Decimal
from dataclasses import dataclass

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from core.storage.merchant_db import MerchantDB
from exchanges.base import Order
from bot.notifier import TelegramNotifier
from bot.taker_builder import send_taker_single

TEST_DB_PATH = Path("data/test_taker_redraw.db")

class DummyBot:
    def __init__(self):
        self.sent_messages = []
        self.edited_messages = []
        
        class DummySession:
            async def close(self):
                pass
        self.session = DummySession()

    async def send_message(self, chat_id, text, reply_markup=None, disable_web_page_preview=True, disable_notification=False, reply_to_message_id=None):
        mid = len(self.sent_messages) + 1
        
        @dataclass
        class Msg:
            message_id: int
            chat_id: int
            text: str
            reply_markup: object
        
        msg = Msg(message_id=mid, chat_id=chat_id, text=text, reply_markup=reply_markup)
        self.sent_messages.append(msg)
        return msg

    async def edit_message_text(self, chat_id, message_id, text, reply_markup=None, disable_web_page_preview=True):
        self.edited_messages.append((message_id, text, reply_markup))
        return True

async def run_tests():
    print("Starting Taker Redraw tests...")
    
    # 1. Cleanup old test DB
    for suffix in ("", "-wal", "-shm"):
        p = Path(str(TEST_DB_PATH) + suffix)
        if p.exists():
            try:
                p.unlink()
            except Exception:
                pass

    # 2. Start DB
    db = MerchantDB(TEST_DB_PATH)
    await db.start()
    print("DB started.")

    # 3. Insert mock user card settings so card module is disabled (simplifies test)
    await db._db.execute("INSERT OR REPLACE INTO user_card_settings (user_id, card_module_mode) VALUES (?, 'off')", (999999,))
    await db._db.commit()

    # 4. Instantiate TelegramNotifier with DummyBot
    notifier = TelegramNotifier()
    notifier._bot = DummyBot()
    notifier.bind_db(db)

    # 5. Create a Taker order candidate
    order = Order(
        id="taker_123",
        price=Decimal("44.50"),
        available_amount=Decimal("1000"),
        min_limit=Decimal("500"),
        max_limit=Decimal("5000"),
        merchant_id="merchant_taker_111",
        merchant_name="SuperTaker",
        month_order_count=150,
        finish_rate_pct=98.5,
        exchange="Binance",
        risk_flag="OK"
    )

    # 6. Save LLM verdict in DB before sending
    await db.save_verdict(
        exchange="Binance",
        merchant_id="merchant_taker_111",
        merchant_name="SuperTaker",
        trade_terms="No terms",
        verdict="OK",
        trade_recommendation="CONDITIONAL",
        reason="Good history"
    )

    print("Sending taker alert...")
    # Send taker single
    sent_ids = await send_taker_single(
        notifier,
        order,
        mode="TAKER_SELL",
        chat_id=999999,
        display_settings={"show_bank_details": True, "show_llm_summary": True}
    )

    print(f"Sent message IDs: {sent_ids}")
    assert len(sent_ids) == 1, f"Expected 1 sent message, got {len(sent_ids)}"
    assert sent_ids[0] == 1, f"Expected message_id 1, got {sent_ids[0]}"

    # Verify that the sent alert is stored in DB
    async with db._db.execute("SELECT * FROM sent_alerts") as cur:
        all_sent = await cur.fetchall()

    print(f"Sent alerts count in DB: {len(all_sent)}")
    assert len(all_sent) == 1, "Expected 1 record in sent_alerts table"
    assert all_sent[0]["merchant_id"] == "merchant_taker_111", "Merchant ID does not match"
    import json
    alert_dict = json.loads(all_sent[0]["alert_json"])
    assert alert_dict.get("is_taker") is True, "Saved alert is not flagged as Taker alert"
    assert alert_dict["taker_mode"] == "TAKER_SELL", "Saved taker mode is incorrect"
    print("Alert stored in sent_alerts correctly.")

    # 7. Modify LLM verdict/recommendation in DB to check redraw updates
    await db.save_verdict(
        exchange="Binance",
        merchant_id="merchant_taker_111",
        merchant_name="SuperTaker",
        trade_terms="No terms",
        verdict="OK",
        trade_recommendation="APPROVE",  # Changed from CONDITIONAL to APPROVE
        reason="Excellent history updated"
    )

    # Clear edited messages list in bot mock
    notifier._bot.edited_messages.clear()

    print("Triggering redraw...")
    # Call redraw
    await notifier.redraw_alerts_for_merchant(exchange="Binance", merchant_id="merchant_taker_111")

    print(f"Edited messages count: {len(notifier._bot.edited_messages)}")
    assert len(notifier._bot.edited_messages) == 1, f"Expected 1 edit call, got {len(notifier._bot.edited_messages)}"
    
    mid, edited_text, _ = notifier._bot.edited_messages[0]
    assert mid == 1, f"Expected to edit message ID 1, got {mid}"
    
    # Check if the edited text contains updated recommendation
    print("Edited text snippet:")
    
    # "APPROVE" is represented as БЕЗПЕЧНО in rec_badge for OK/APPROVE
    # Let's verify the text has "БЕЗПЕЧНО" or contains the updated reason
    assert "Excellent history updated" in edited_text, "Edited text does not contain updated recommendation!"
    print("Redraw successfully edited the message text in Telegram.")

    # Verify that the sent_alerts table alert_json is updated in DB
    async with db._db.execute("SELECT * FROM sent_alerts") as cur:
        all_sent_post = await cur.fetchall()
    
    alert_dict_post = json.loads(all_sent_post[0]["alert_json"])
    assert alert_dict_post["order"]["merchant_id"] == "merchant_taker_111", "Saved order merchant ID mismatch"
    
    # 8. Cleanup
    await db.stop()
    await notifier.stop()
    
    for suffix in ("", "-wal", "-shm"):
        p = Path(str(TEST_DB_PATH) + suffix)
        if p.exists():
            try:
                p.unlink()
            except Exception:
                pass
    print("Cleanup completed.")
    print("ALL TAKER REDRAW TESTS PASSED SUCCESSFULLY!")

if __name__ == "__main__":
    asyncio.run(run_tests())
