import sys
sys.stdout.reconfigure(encoding='utf-8')
from decimal import Decimal
from exchanges.base import Order
from core.engine.cross_matcher import CrossMatchingEngine
from config.banks import BANK_NAMES

# Mock orders matching the user's alert
buy_o = Order(
    id="buy_1",
    price=Decimal("44.10"),
    available_amount=Decimal("200.0"),
    min_limit=Decimal("8599.0"),
    max_limit=Decimal("8600.0"),
    merchant_id="buy_merch",
    merchant_name="User-2faf8",
    month_order_count=229,
    finish_rate_pct=100.0,
    exchange="Binance",
    link="https://c2c.binance.com/uk-UA/advertiserDetail?advertiserNo=s793c0a23291b3139910234d65d2ad96d",
    bank_codes=["43"],  # Monobank
    trade_terms="",
    is_verified=False
)

sell_o = Order(
    id="sell_1",
    price=Decimal("45.45"),
    available_amount=Decimal("350.0"),
    min_limit=Decimal("3000.0"),
    max_limit=Decimal("15907.50"),
    merchant_id="sell_merch",
    merchant_name="oleks_bazza",
    month_order_count=97,
    finish_rate_pct=97.0,
    exchange="OKX",
    link="https://www.okx.com/p2p/ads-merchant?publicUserId=93e50b50bb",
    bank_codes=["43", "14", "48", "64"],  # Monobank, PrivatBank, А-Банк, ПУМБ
    trade_terms="",
    is_verified=False
)

buy_grouped = {"43": [buy_o]}
sell_grouped = {
    "43": [sell_o],
    "14": [sell_o],
    "48": [sell_o],
    "64": [sell_o]
}

engine = CrossMatchingEngine(max_capital_uah=11000.0, min_trade_uah=1000.0, min_spread_pct=0.5)
raw_opps = engine.match(buy_grouped, sell_grouped, experimental_mode=True)
print(f"RAW OPPS COUNT: {len(raw_opps)}")
for i, opp in enumerate(raw_opps):
    print(f"{i}: {opp['buy_bank']} -> {opp['sell_bank']} | net_profit: {opp['net_profit']} | net_spread_pct: {opp['net_spread_pct']:.2f}%")

grouped_opps = engine.group(raw_opps, BANK_NAMES)
print(f"\nGROUPED OPPS COUNT: {len(grouped_opps)}")
for opp in grouped_opps:
    print(f"Buy: {opp['buy_bank']}, Sell: {opp['sell_bank']}")
    print(f"Route Variants: {opp['route_variants']}")
    print(f"Route Pairs: {opp.get('route_pairs')}")
