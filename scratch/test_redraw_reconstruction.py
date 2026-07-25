# scratch/test_redraw_reconstruction.py
import sys
import os
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from decimal import Decimal
from exchanges.base import Order
from bot.notifier import SpreadAlert
import inspect

def test_reconstruction():
    print("Testing Order reconstruction with dynamic fields...")
    
    # 1. Mock dictionary representing the serialized alert buy_order from DB
    buy_dict = {
        "id": "12345",
        "price": 44.11,
        "available_amount": 1000.0,
        "min_limit": 500.0,
        "max_limit": 5000.0,
        "merchant_id": "m123",
        "merchant_name": "Test Merchant",
        "month_order_count": 150,
        "finish_rate_pct": 98.5,
        "exchange": "Binance",
        "link": "https://binance.com",
        "bank_codes": ["43", "14"],
        "trade_terms": "Only Monobank",
        "risk_flag": "LLM_PENDING",
        "is_verified": True,
        "account_age_days": 10,
        "composite_score": 15,
        "review_score": 5,
        "review_neg_pct": 0.0,
        "review_fetched": True,
        "positive_rate": 0.99,
        "side": "buy",
        # Dynamic/non-init attributes:
        "ad_id": "ad_9999",
        "order_id": "order_7777"
    }

    # 2. Emulate the type conversions in redraw_alerts_for_merchant
    for field in ("price", "available_amount", "min_limit", "max_limit"):
        if field in buy_dict:
            buy_dict[field] = Decimal(str(buy_dict[field]))

    buy_dict["month_order_count"] = int(float(buy_dict.get("month_order_count", 0)))
    buy_dict["finish_rate_pct"] = float(buy_dict.get("finish_rate_pct", 100.0))
    buy_dict["is_verified"] = str(buy_dict.get("is_verified", "False")) in ("True", "1", "true")
    buy_dict["account_age_days"] = int(float(buy_dict.get("account_age_days", 0)))
    buy_dict["composite_score"] = int(float(buy_dict.get("composite_score", 0)))
    buy_dict["review_score"] = int(float(buy_dict.get("review_score", 0)))
    buy_dict["review_neg_pct"] = float(buy_dict.get("review_neg_pct", 0.0))
    buy_dict["review_fetched"] = str(buy_dict.get("review_fetched", "False")) in ("True", "1", "true")
    buy_dict["positive_rate"] = float(buy_dict.get("positive_rate", 0.0))

    # Reconstruct order objects from raw dict
    valid_fields = set(inspect.signature(Order).parameters.keys())
    buy_order = Order(**{k: v for k, v in buy_dict.items() if k in valid_fields})
    for k, v in buy_dict.items():
        if k not in valid_fields:
            setattr(buy_order, k, v)

    # 3. Assertions
    assert hasattr(buy_order, "ad_id"), "Error: ad_id was not restored on buy_order!"
    assert buy_order.ad_id == "ad_9999", f"Error: ad_id is {buy_order.ad_id} instead of ad_9999"
    assert hasattr(buy_order, "order_id"), "Error: order_id was not restored on buy_order!"
    assert buy_order.order_id == "order_7777", f"Error: order_id is {buy_order.order_id} instead of order_7777"
    
    print("SUCCESS: Reconstruction test passed! ad_id and order_id are preserved.")

if __name__ == "__main__":
    test_reconstruction()
