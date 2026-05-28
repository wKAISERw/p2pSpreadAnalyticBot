import asyncio
import sqlite3
import copy
import sys
import os
sys.path.append(os.getcwd())
from dataclasses import dataclass

# Mock the SpreadAlert dataclass
@dataclass
class SpreadAlert:
    buy_order: any
    sell_order: any
    spread_pct: float
    profit_uah: float
    deal_amount_uah: float
    buy_bank: str
    sell_bank: str
    buy_banks_all: list = None
    sell_banks_all: list = None
    buy_banks_fit: list = None
    sell_banks_fit: list = None
    route_variants: list = None
    route_type: str = "UNKNOWN"
    route_pairs: list = None
    buy_rec: str = "PENDING"
    sell_rec: str = "PENDING"
    buy_reason: str = ""
    sell_reason: str = ""
    buy_terms_summary: str = ""
    sell_terms_summary: str = ""
    buy_reviews_analysis: str = ""
    sell_reviews_analysis: str = ""

# Mock order
class MockOrder:
    def __init__(self, exchange, merchant_id, merchant_name, price, min_limit, max_limit, available_amount):
        self.exchange = exchange
        self.merchant_id = merchant_id
        self.merchant_name = merchant_name
        self.price = price
        self.min_limit = min_limit
        self.max_limit = max_limit
        self.available_amount = available_amount
        self.month_order_count = 100
        self.finish_rate_pct = 98.0
        self.bank_codes = ["43", "14", "64"]

async def run_validation():
    from core.engine.alert_dispatcher import AlertDispatcher
    from core.storage.merchant_db import MerchantDB

    db = MerchantDB()
    await db.start()

    # Kaiser user ID is 1115620363
    user_id = 1115620363

    class MockNotifier:
        def __init__(self):
            self.sent_alerts = []

        async def send_to_user(self, chat_id, alert, is_sniper_match=False):
            self.sent_alerts.append((chat_id, copy.copy(alert)))

    notifier = MockNotifier()
    dispatcher = AlertDispatcher(db, notifier)

    # Mock opportunity with multiple route pairs
    opp = {
        "buy_order": MockOrder("Binance", "buy_mid", "BuyMerchant", 44.10, 1000.0, 10000.0, 200.0),
        "sell_order": MockOrder("OKX", "sell_mid", "SellMerchant", 45.10, 1000.0, 10000.0, 200.0),
        "buy_bank": "43",
        "sell_bank": "64",
        "buy_banks_fit": ["monobank", "privatbank", "pumb"],
        "sell_banks_fit": ["monobank", "privatbank", "a-bank", "pumb"],
        "route_type": "CROSS",
        "net_spread_pct": 2.2,
        "net_profit": 220.0,
        "actual_entry_uah": 10000.0,
        "route_pairs": [["43", "64"], ["43", "14"], ["43", "43"]],  # Mono->Pumb, Mono->Privat, Mono->Mono
    }

    alert = SpreadAlert(
        buy_order=opp["buy_order"],
        sell_order=opp["sell_order"],
        spread_pct=opp["net_spread_pct"],
        profit_uah=opp["net_profit"],
        deal_amount_uah=opp["actual_entry_uah"],
        buy_bank=opp["buy_bank"],
        sell_bank=opp["sell_bank"],
        buy_banks_fit=opp["buy_banks_fit"],
        sell_banks_fit=opp["sell_banks_fit"],
        route_pairs=opp["route_pairs"],
    )

    print("--- Test Scenario 1: Fully Covered Card Pair (Mono->Mono) is available ---")
    await dispatcher.dispatch(alert, opp)

    kaiser_alerts = [a for cid, a in notifier.sent_alerts if cid == user_id]
    if kaiser_alerts:
        sent_alert = kaiser_alerts[0]
        print(f"Sent alert for Kaiser: buy_bank={sent_alert.buy_bank}, sell_bank={sent_alert.sell_bank}")
        if sent_alert.buy_bank == "43" and sent_alert.sell_bank == "43":
            print("SUCCESS: Selected Monobank -> Monobank (43 -> 43) because Kaiser has a Mono card!")
        else:
            print(f"FAILED: Expected 43 -> 43, got {sent_alert.buy_bank} -> {sent_alert.sell_bank}")
    else:
        print("FAILED: No alerts sent to Kaiser")

    print("\n--- Test Scenario 2: No Covered Card Pair is available (Fallback) ---")
    opp_fallback = copy.copy(opp)
    opp_fallback["sell_banks_fit"] = ["privatbank", "pumb"]  # Merchant does not support Monobank
    opp_fallback["route_pairs"] = [["43", "64"]]  # Mono->Pumb only
    
    alert_fallback = SpreadAlert(
        buy_order=opp_fallback["buy_order"],
        sell_order=opp_fallback["sell_order"],
        spread_pct=opp_fallback["net_spread_pct"],
        profit_uah=opp_fallback["net_profit"],
        deal_amount_uah=opp_fallback["actual_entry_uah"],
        buy_bank=opp_fallback["buy_bank"],
        sell_bank=opp_fallback["sell_bank"],
        buy_banks_fit=opp_fallback["buy_banks_fit"],
        sell_banks_fit=opp_fallback["sell_banks_fit"],
        route_pairs=opp_fallback["route_pairs"],
    )

    notifier.sent_alerts.clear()
    await dispatcher.dispatch(alert_fallback, opp_fallback)

    kaiser_alerts = [a for cid, a in notifier.sent_alerts if cid == user_id]
    if kaiser_alerts:
        sent_alert = kaiser_alerts[0]
        print(f"Sent alert for Kaiser: buy_bank={sent_alert.buy_bank}, sell_bank={sent_alert.sell_bank}")
        if sent_alert.buy_bank == "43" and sent_alert.sell_bank == "64":
            print("SUCCESS: Fell back to Monobank -> PUMB (43 -> 64)")
        else:
            print(f"FAILED: Expected fallback to 43 -> 64, got {sent_alert.buy_bank} -> {sent_alert.sell_bank}")
    else:
        print("FAILED: No alerts sent to Kaiser")

    await db.stop()

if __name__ == "__main__":
    asyncio.run(run_validation())
