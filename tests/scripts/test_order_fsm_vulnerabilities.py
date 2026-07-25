import asyncio
import logging
import sys
import os
import uuid
from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock

# Ensure we can import core modules
sys.path.append(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from config import settings
from state import state
from core.storage.merchant_db import MerchantDB
from core.engine.single_leg_executor import SingleLegExecutor
from core.engine.order_monitor import OrderMonitor

logging.basicConfig(level=logging.INFO, format="%(asctime)s | %(levelname)s | %(name)s | %(message)s")
logger = logging.getLogger("SimulateTradeFlow")

class MockNotifier:
    def __init__(self):
        self.sent_messages = []

    async def _send_with_retry(self, msg: str, keyboard=None, chat_id=None):
        logger.info(f"📤 [MOCK TELEGRAM NOTIFICATION] Chat={chat_id} | Msg={msg}")
        self.sent_messages.append({"chat_id": chat_id, "msg": msg})

async def run_simulation():
    # 🛑 Force DRY_RUN
    settings.dry_run_mode = True
    state.stats["internet_connected"] = True
    
    logger.info("🚀 Starting P2P Order Flow and FSM Vulnerability Simulation...")

    db = MerchantDB()
    await db.start()
    
    notifier = MockNotifier()
    executor = SingleLegExecutor(db, notifier=notifier)
    
    # Setup mock merchants for recommendation checks
    await db.save_verdict(
        exchange="Bybit", merchant_id="m_approve", merchant_name="Safe Merchant",
        trade_terms="Only clean cards", verdict="OK", risk_type="NONE",
        reason="Good stats", source="test", trade_recommendation="APPROVE",
        terms_summary="Only own cards", reviews_analysis="Clean reviews"
    )
    await db.save_verdict(
        exchange="Bybit", merchant_id="m_reject", merchant_name="Scam Merchant",
        trade_terms="Transfer to Telegram", verdict="BLOCK", risk_type="TRIANGLE",
        reason="Scam risk", source="test", trade_recommendation="REJECT",
        terms_summary="Scam terms", reviews_analysis="Bad reviews"
    )
    await db.save_verdict(
        exchange="Bybit", merchant_id="m_conditional", merchant_name="New Merchant",
        trade_terms="Terms", verdict="SUSPICIOUS", risk_type="LOW_STATS",
        reason="New account", source="test", trade_recommendation="CONDITIONAL",
        terms_summary="Sus terms", reviews_analysis="Few reviews"
    )

    # =========================================================================
    # CASE 1: LLM RECOMMENDATION FILTERS
    # =========================================================================
    logger.info("\n--- [CASE 1] Testing LLM Recommendation Filters ---")

    # 1.1 REJECT
    logger.info("1.1 Triggering Taker-Buy with REJECTED merchant...")
    res_rej = await executor.execute_single_buy(
        exchange="Bybit", ad_id="ad_123", price=41.50, amount_usdt=100.0,
        merchant_id="m_reject", owner_user_id=12345
    )
    logger.info(f"Result (Should be success=False): {res_rej}")
    assert not res_rej["success"]
    assert "REJECT" in res_rej["error"]

    # 1.2 CONDITIONAL (Should halve the amount from 100 USDT to 50 USDT)
    logger.info("1.2 Triggering Taker-Buy with CONDITIONAL merchant...")
    res_cond = await executor.execute_single_buy(
        exchange="Bybit", ad_id="ad_123", price=41.50, amount_usdt=100.0,
        merchant_id="m_conditional", owner_user_id=12345
    )
    logger.info(f"Result (Should halve amount, warnings present): {res_cond}")
    assert res_cond["success"]
    assert "CONDITIONAL" in res_cond["warning"]
    
    # Retrieve the trade from db to verify the amount
    trade_cond = await db.get_active_trade_by_id(res_cond["trade_id"])
    logger.info(f"Verifying DB record for CONDITIONAL trade: amount={trade_cond['amount']} USDT (original: 100 USDT)")
    assert float(trade_cond["amount"]) == 50.0

    # 1.3 APPROVE
    logger.info("1.3 Triggering Taker-Buy with APPROVED merchant...")
    res_app = await executor.execute_single_buy(
        exchange="Bybit", ad_id="ad_123", price=41.50, amount_usdt=100.0,
        merchant_id="m_approve", owner_user_id=12345
    )
    logger.info(f"Result (Should be full 100 USDT): {res_app}")
    assert res_app["success"]
    trade_app = await db.get_active_trade_by_id(res_app["trade_id"])
    assert float(trade_app["amount"]) == 100.0

    # =========================================================================
    # CASE 2: FSM ORDER TRANSITIONS & TELEGRAM ALERTS
    # =========================================================================
    logger.info("\n--- [CASE 2] Testing Order Monitor FSM Transitions & Alerts ---")
    
    # We will test order_monitor manually by mocking the fetch response and calling target callbacks
    # Fetch trade details
    trade_id = res_app["trade_id"]
    order_id = res_app["order_id"]
    
    # Trigger on_paid callback (Simulating counterparty marking paid)
    logger.info(f"2.1 Simulating counterparty PAID callback for trade #{trade_id}...")
    await executor._on_single_paid(trade_id, order_id)
    await asyncio.sleep(0.1)
    
    # Verify Telegram notification was triggered
    assert len(notifier.sent_messages) > 0
    last_msg = notifier.sent_messages[-1]
    logger.info(f"Alert verified: {last_msg['msg']}")
    assert "Контрагент оплатив ордер" in last_msg["msg"]
    assert str(trade_id) in last_msg["msg"]
    
    # Trigger on_filled callback (Completed status)
    logger.info(f"2.2 Simulating COMPLETED callback for trade #{trade_id}...")
    await executor._on_single_filled(trade_id, order_id, {})
    
    # Verify trade status updated in DB
    trade_final = await db.get_active_trade_by_id(trade_id)
    logger.info(f"DB status for completed trade: {trade_final['status']}")
    assert trade_final["status"] == "COMPLETED"

    # =========================================================================
    # CASE 3: APPEAL STATUS DETECTION
    # =========================================================================
    logger.info("\n--- [CASE 3] Testing APPEAL (60) Status Handling ---")
    
    # Create another trade
    res_appeal = await executor.execute_single_buy(
        exchange="Bybit", ad_id="ad_456", price=41.50, amount_usdt=100.0,
        merchant_id="m_approve", owner_user_id=12345
    )
    appeal_trade_id = res_appeal["trade_id"]
    appeal_order_id = res_appeal["order_id"]
    
    # Mock order status fetcher to return APPEAL
    monitor = OrderMonitor(db)
    # We will replace _fetch_order_status with a mock returning status 60 (APPEAL)
    monitor._fetch_order_status = AsyncMock(return_value={"orderStatus": "60", "status": "60"})
    
    # Run one loop iteration
    logger.info("Running OrderMonitor check (mocked returning status 60 - APPEAL)...")
    
    # Temporarily bind global notifier for test
    from bot.handlers import core
    core._notifier_impl = notifier
    
    # Run the loop using asyncio.wait_for to prevent infinite loop if break fails
    try:
        await asyncio.wait_for(
            monitor._monitor_loop(
                trade_id=appeal_trade_id, order_id=appeal_order_id, exchange="Bybit",
                credentials={}, fsm_status="PENDING_PAYMENT",
                on_filled=None, on_expired=None, on_paid=None
            ),
            timeout=2.0
        )
    except asyncio.TimeoutError:
        logger.error("OrderMonitor loop did not break after APPEAL state!")
        assert False, "OrderMonitor loop hang on APPEAL"
        
    # Verify DB status
    trade_appeal = await db.get_active_trade_by_id(appeal_trade_id)
    logger.info(f"Verified trade status in DB after appeal: {trade_appeal['status']}")
    assert trade_appeal["status"] == "APPEAL"
    
    # Verify TG alert
    await asyncio.sleep(0.1)
    assert len(notifier.sent_messages) > 0
    appeal_msg = notifier.sent_messages[-1]
    logger.info(f"Verified TG alert text: {appeal_msg['msg']}")
    assert "Апеляція по ордеру" in appeal_msg["msg"]
    assert str(appeal_trade_id) in appeal_msg["msg"]

    # =========================================================================
    # CASE 4: INTERNET OUTAGE RESILIENCE
    # =========================================================================
    logger.info("\n--- [CASE 4] Testing Internet Outage Resilience in OrderMonitor ---")
    
    # We will test order_monitor loop with internet connection down
    state.stats["internet_connected"] = False
    
    res_outage = await executor.execute_single_buy(
        exchange="Bybit", ad_id="ad_789", price=41.50, amount_usdt=100.0,
        merchant_id="m_approve", owner_user_id=12345
    )
    outage_trade_id = res_outage["trade_id"]
    outage_order_id = res_outage["order_id"]
    
    monitor_outage = OrderMonitor(db)
    # Mock to check if it gets called
    monitor_outage._fetch_order_status = AsyncMock(return_value={"orderStatus": "40"}) # COMPLETED
    
    # Start loop in background
    logger.info("Starting OrderMonitor loop in background while internet is offline...")
    task = asyncio.create_task(
        monitor_outage._monitor_loop(
            trade_id=outage_trade_id, order_id=outage_order_id, exchange="Bybit",
            credentials={}, fsm_status="PENDING_PAYMENT",
            on_filled=executor._on_single_filled, on_expired=None, on_paid=None
        )
    )
    
    # Sleep to verify it is paused and _fetch_order_status is NOT called
    await asyncio.sleep(1.0)
    logger.info(f"Checking if fetch was called while offline: {monitor_outage._fetch_order_status.call_count} times")
    assert monitor_outage._fetch_order_status.call_count == 0
    
    # Restore internet
    logger.info("Restoring internet connection...")
    state.stats["internet_connected"] = True
    
    # Wait for the monitor to run one cycle, fetch status, trigger completed, and exit
    logger.info("Waiting for OrderMonitor to complete order status retrieval...")
    await asyncio.wait_for(task, timeout=10.0)
    
    logger.info(f"Verified fetch was called after connection restored: {monitor_outage._fetch_order_status.call_count} times")
    assert monitor_outage._fetch_order_status.call_count > 0
    
    # Check status in DB
    trade_outage = await db.get_active_trade_by_id(outage_trade_id)
    logger.info(f"Verified trade status in DB after connection recovery: {trade_outage['status']}")
    assert trade_outage["status"] == "COMPLETED"

    # Cleanup
    await db.stop()
    logger.info("\n🎉 Simulation finished successfully! All checks passed.")

if __name__ == "__main__":
    asyncio.run(run_simulation())
