# tests/core/test_cross_matcher.py
import pytest
from decimal import Decimal
from exchanges.base import Order
from core.engine.cross_matcher import CrossMatchingEngine


def test_symmetric_downscaling():
    # Setup matcher
    # max_capital_uah=10000, min_trade_uah=100, min_spread_pct=0.5
    matcher = CrossMatchingEngine(
        max_capital_uah=10000.0,
        min_trade_uah=100.0,
        min_spread_pct=0.5,
        safety_buffer_pct=0.0
    )

    # Buyer has capacity for 10,000 UAH (min 1,000 UAH)
    buy_order = Order(
        id="buy_1",
        price=Decimal("40.00"),
        available_amount=Decimal("250.0"), # 250 * 40 = 10,000 UAH
        min_limit=Decimal("1000.0"),
        max_limit=Decimal("10000.0"),
        merchant_id="merchant_buy",
        merchant_name="Buyer",
        month_order_count=100,
        finish_rate_pct=99.0,
        link="",
        exchange="Binance"
    )
    buy_order.bank_codes = ["43"]

    # Seller has max limit of 4,000 UAH (min 500 UAH)
    sell_order = Order(
        id="sell_1",
        price=Decimal("41.00"),
        available_amount=Decimal("100.0"), # 100 * 41 = 4,100 UAH, but limit is 4,000 UAH
        min_limit=Decimal("500.0"),
        max_limit=Decimal("4000.0"),
        merchant_id="merchant_sell",
        merchant_name="Seller",
        month_order_count=100,
        finish_rate_pct=99.0,
        link="",
        exchange="Bybit"
    )
    sell_order.bank_codes = ["43"]

    buy_grouped = {"43": [buy_order]}
    sell_grouped = {"43": [sell_order]}

    # 1. Standard symmetric mode (experimental_mode = False)
    opps_symmetric = matcher.match(buy_grouped, sell_grouped, experimental_mode=False)
    assert len(opps_symmetric) == 1
    opp = opps_symmetric[0]
    
    # We should have scaled down the buy leg to match the seller's capacity (approx 4,000 UAH worth of USDT * buy price)
    # Seller max is 4,000 UAH -> 4000 / 41 = 97.5609 USDT
    # 97.5609 USDT * 40 buy price = 3902.439 UAH buy fiat
    assert opp["is_asymmetric"] is False
    assert abs(opp["actual_entry_uah"] - 3902.44) < 1.0


def test_symmetric_limit_violation_skips():
    matcher = CrossMatchingEngine(
        max_capital_uah=10000.0,
        min_trade_uah=100.0,
        min_spread_pct=0.5,
    )

    # Buyer requires min 5,000 UAH
    buy_order = Order(
        id="buy_1",
        price=Decimal("40.00"),
        available_amount=Decimal("250.0"),
        min_limit=Decimal("5000.0"),
        max_limit=Decimal("10000.0"),
        merchant_id="merchant_buy",
        merchant_name="Buyer",
        month_order_count=100,
        finish_rate_pct=99.0,
        link="",
        exchange="Binance"
    )
    buy_order.bank_codes = ["43"]

    # Seller allows max 4,000 UAH (min 500 UAH)
    sell_order = Order(
        id="sell_1",
        price=Decimal("41.00"),
        available_amount=Decimal("100.0"),
        min_limit=Decimal("500.0"),
        max_limit=Decimal("4000.0"),
        merchant_id="merchant_sell",
        merchant_name="Seller",
        month_order_count=100,
        finish_rate_pct=99.0,
        link="",
        exchange="Bybit"
    )
    sell_order.bank_codes = ["43"]

    buy_grouped = {"43": [buy_order]}
    sell_grouped = {"43": [sell_order]}

    # In symmetric mode, scaling down to 4k violates the buyer's 5k min limit -> should be skipped!
    opps_symmetric = matcher.match(buy_grouped, sell_grouped, experimental_mode=False)
    assert len(opps_symmetric) == 0


def test_asymmetric_fallback():
    matcher = CrossMatchingEngine(
        max_capital_uah=10000.0,
        min_trade_uah=100.0,
        min_spread_pct=0.5,
    )

    # Buyer requires min 5,000 UAH
    buy_order = Order(
        id="buy_1",
        price=Decimal("40.00"),
        available_amount=Decimal("250.0"),
        min_limit=Decimal("5000.0"),
        max_limit=Decimal("10000.0"),
        merchant_id="merchant_buy",
        merchant_name="Buyer",
        month_order_count=100,
        finish_rate_pct=99.0,
        link="",
        exchange="Binance"
    )
    buy_order.bank_codes = ["43"]

    # Seller allows max 4,000 UAH (min 500 UAH)
    sell_order = Order(
        id="sell_1",
        price=Decimal("41.00"),
        available_amount=Decimal("100.0"),
        min_limit=Decimal("500.0"),
        max_limit=Decimal("4000.0"),
        merchant_id="merchant_sell",
        merchant_name="Seller",
        month_order_count=100,
        finish_rate_pct=99.0,
        link="",
        exchange="Bybit"
    )
    sell_order.bank_codes = ["43"]

    buy_grouped = {"43": [buy_order]}
    sell_grouped = {"43": [sell_order]}

    # In experimental (asymmetric) mode, we can't scale down to 4k symmetrically,
    # so we fallback to asymmetric mode: buy the buyer's minimum (5,000 UAH), sell 4,000 UAH, remainder to inventory
    opps_asymmetric = matcher.match(buy_grouped, sell_grouped, experimental_mode=True)
    assert len(opps_asymmetric) == 1
    opp = opps_asymmetric[0]
    assert opp["is_asymmetric"] is True
    assert opp["actual_entry_uah"] == 10000.0
    assert opp["asymmetric_details"]["inventory_usdt"] > 0
