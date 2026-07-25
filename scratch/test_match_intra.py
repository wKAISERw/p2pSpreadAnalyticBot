import sys
sys.stdout.reconfigure(encoding='utf-8')
from decimal import Decimal
from exchanges.base import Order
from core.engine.cross_matcher import CrossMatchingEngine
from config.banks import BANK_NAMES

buy_o = Order(
    id="260527042755984",
    price=Decimal("44.84"),
    available_amount=Decimal("269.52"),
    min_limit=Decimal("3000.00"),
    max_limit=Decimal("12085.27"),
    merchant_id="c9b13c9063",
    merchant_name="Ilya_ZAV",
    month_order_count=162,
    finish_rate_pct=98.78,
    exchange="OKX",
    link="https://www.okx.com/p2p/ads-merchant?publicUserId=c9b13c9063",
    bank_codes=["64", "43", "48", "328"],
    trade_terms="",
    is_verified=False
)

sell_o = Order(
    id="260527045745190",
    price=Decimal("45.52"),
    available_amount=Decimal("380.00"),
    min_limit=Decimal("3000.00"),
    max_limit=Decimal("17297.60"),
    merchant_id="93e50b50bb",
    merchant_name="oleks_bazza",
    month_order_count=114,
    finish_rate_pct=97.43,
    exchange="OKX",
    link="https://www.okx.com/p2p/ads-merchant?publicUserId=93e50b50bb",
    bank_codes=["64", "43", "48", "14"],
    trade_terms="",
    is_verified=False
)

# Simulate target_banks = {"43", "14", "64"}
buy_grouped = {}
sell_grouped = {}

for b in ["43", "14", "64"]:
    buy_grouped[b] = []
    sell_grouped[b] = []
    
    if b in buy_o.bank_codes:
        buy_grouped[b].append(buy_o)
    if b in sell_o.bank_codes:
        sell_grouped[b].append(sell_o)

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
