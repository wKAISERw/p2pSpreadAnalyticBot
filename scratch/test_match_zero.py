import sys
sys.stdout.reconfigure(encoding='utf-8')
from decimal import Decimal
from exchanges.base import Order
from core.engine.cross_matcher import CrossMatchingEngine
from config.banks import BANK_NAMES

buy_o = Order(
    id="bn_13878449675387191296",
    price=Decimal("44.43"),
    available_amount=Decimal("37254.55"),
    min_limit=Decimal("4750"),
    max_limit=Decimal("4750"),
    merchant_id="s0cbfe9b8b1d933f98c2b518cd50177f0",
    merchant_name="ZeroFeePay",
    month_order_count=21124,
    finish_rate_pct=100.0,
    exchange="Binance",
    link="https://c2c.binance.com/uk-UA/advertiserDetail?advertiserNo=s0cbfe9b8b1d933f98c2b518cd50177f0",
    bank_codes=["43"],
    trade_terms="",
    is_verified=True
)

sell_o = Order(
    id="260527043408180",
    price=Decimal("45.47"),
    available_amount=Decimal("380.00"),
    min_limit=Decimal("3000.00"),
    max_limit=Decimal("17278.60"),
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
