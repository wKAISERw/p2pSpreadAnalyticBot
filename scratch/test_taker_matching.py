import asyncio
import sqlite3
import sys
from pathlib import Path
from decimal import Decimal

sys.stdout.reconfigure(encoding='utf-8')

# Import our scanner & DB classes
sys.path.append("c:/p2p_scanner")
from core.storage.merchant_db import MerchantDB
from core.engine.taker_scanner import TakerScanner
from exchanges.base import Order

async def run_simulation():
    db_path = Path("c:/p2p_scanner/data/merchants.db")
    db = MerchantDB(db_path)
    await db.start()
    
    users = await db.get_active_users()
    user = next((u for u in users if u["user_id"] == 1115620363), None)
    
    if not user:
        print("User 1115620363 not found!")
        return
        
    print("User configuration loaded:")
    for k, v in user.items():
        if "taker_sell" in k or k in ("bank_codes", "buy_bank_codes", "sell_bank_codes", "scanner_mode", "capital", "min_amount"):
            print(f"  {k}: {v}")
            
    # Mock order for lord_tt
    lord_tt_order = Order(
        id="bn_test_lord_tt",
        price=Decimal("45.62"),
        available_amount=Decimal("20387.49"),
        min_limit=Decimal("4000"),
        max_limit=Decimal("50000"),
        merchant_id="sc4a0858df01a3e4f96ce2bbc44ffa10e",
        merchant_name="lord_tt",
        month_order_count=176,
        finish_rate_pct=90.8,
        positive_rate=1.0, # or 100.0
        exchange="Binance",
        link="https://c2c.binance.com/uk-UA/advertiserDetail?advertiserNo=sc4a0858df01a3e4f96ce2bbc44ffa10e",
        bank_codes=["43"],
        trade_terms="",
        is_verified=True,
        last_online_mins=1
    )
    
    scanner = TakerScanner(db)
    
    print("\nStarting TakerScanner.find_orders_for_user simulation...")
    # Side 0 is sell_grouped (merchants are buying, user is selling)
    sell_grouped = {"43": [lord_tt_order]}
    buy_grouped = {}
    
    # Run the filter trace manually first to log each branch
    print("\nManual Trace of TakerScanner filtering:")
    mode = user.get("scanner_mode")
    print(f"Mode: {mode}")
    
    user_banks = set(user.get("sell_bank_codes") or user.get("bank_codes", []))
    print(f"User banks: {user_banks}")
    
    capital = float(user.get("capital", 0))
    min_amount = float(user.get("min_amount", 0))
    mf = user.get("merchant_filters") or {}
    emf = user.get("exchange_merchant_filters") or {}
    print(f"Capital: {capital}, Min amount: {min_amount}")
    print(f"Merchant filters: {mf}")
    print(f"Exchange merchant filters: {emf}")
    
    # Card matching setup
    card_matching_active = False
    user_cards_by_bank = {}
    try:
        card_settings = await db.get_user_card_settings(user["user_id"])
        print(f"Card settings: {card_settings}")
        if card_settings and card_settings.get("card_module_mode") != "off" and card_settings.get("enable_in_single_modes"):
            card_matching_active = True
            raw_cards = await db.get_cards(user["user_id"], status="active")
            import time
            now_epoch = time.time()
            for c in raw_cards:
                if float(c.get("cooldown_until", 0.0)) > now_epoch:
                    continue
                b_name = str(c.get("bank_name", "")).lower()
                if b_name not in user_cards_by_bank:
                    user_cards_by_bank[b_name] = []
                user_cards_by_bank[b_name].append(c)
    except Exception as e:
        print(f"Error checking card settings: {e}")
        
    print(f"card_matching_active: {card_matching_active}")
    print(f"user_cards_by_bank: {list(user_cards_by_bank.keys())}")
    
    for bank_code, orders in sell_grouped.items():
        print(f"\nEvaluating bank_code: {bank_code}")
        if bank_code not in user_banks:
            print("  FAIL: bank_code not in user_banks")
            continue
        for order in orders:
            print(f"  Evaluating order: {order.merchant_name}")
            if not (set(order.bank_codes) & user_banks):
                print("  FAIL: order.bank_codes & user_banks is empty")
                continue
            order_max = float(order.max_limit)
            order_min = float(order.min_limit)
            if capital > 0 and order_min > capital:
                print(f"  FAIL: order_min ({order_min}) > capital ({capital})")
                continue
            if min_amount > 0 and order_max < min_amount:
                print(f"  FAIL: order_max ({order_max}) < min_amount ({min_amount})")
                continue
                
            order_price = float(order.price)
            
            if card_matching_active:
                from bot.formatters import _bank_code_to_db
                card_bank_db = _bank_code_to_db(bank_code)
                print(f"  Card bank database key: {card_bank_db}")
                if not card_bank_db or card_bank_db not in user_cards_by_bank:
                    print(f"  FAIL: card_bank_db '{card_bank_db}' not in user_cards_by_bank keys {list(user_cards_by_bank.keys())}")
                    continue
            
            # TAKER_SELL price logic
            strategy = user.get("taker_sell_price_strategy", "roi")
            min_sell_price = float(user.get("taker_sell_min_price", 0))
            price_to = float(user.get("taker_sell_price_to", 0))
            t_amount = float(user.get("taker_sell_amount", 0))
            t_speed = user.get("taker_sell_speed", "ANY")
            
            print(f"  Taker Sell Price Strategy: {strategy}, min_price={min_sell_price}, price_to={price_to}, amount={t_amount}, speed={t_speed}")
            if strategy in ("roi", "min") and min_sell_price > 0:
                if order_price < min_sell_price:
                    print(f"    FAIL: order_price ({order_price}) < min_sell_price ({min_sell_price})")
                    continue
                    
            # Volume check
            if t_amount > 0:
                fiat_val = t_amount * order_price
                print(f"    Target fiat value: {fiat_val:.2f} UAH")
                if t_speed == "FAST":
                    if order_max < fiat_val or order_min > fiat_val:
                        print(f"    FAIL: speed is FAST, but fiat_val not in range [{order_min}, {order_max}]")
                        continue
                else:
                    if order_min > fiat_val:
                        print(f"    FAIL: order_min ({order_min}) > fiat_val ({fiat_val:.2f})")
                        continue
                        
            # Merchant check
            merchant_ok = TakerScanner._merchant_ok(order, mf, emf)
            print(f"    Merchant filters check: {merchant_ok}")
            if not merchant_ok:
                print("    FAIL: merchant filters check failed")
                continue
                
            # Card matching check
            if card_matching_active:
                from bot.formatters import _bank_code_to_db
                card_bank_db = _bank_code_to_db(bank_code)
                target_uah = t_amount * order_price
                card_direction = "sell"
                
                print(f"    Running CardMatchingEngine.run for user={user['user_id']}, bank={card_bank_db}, amount={target_uah}, direction={card_direction}")
                match_res = await scanner.card_engine.run(
                    user_id=user["user_id"],
                    bank=card_bank_db,
                    amount=target_uah,
                    direction=card_direction,
                    crypto_available=True
                )
                print(f"    Card matching result: {match_res.status}, best_card={match_res.best_card}, report={match_res.rejection_report}")
                if match_res.status not in ("success", "needs_split"):
                    print("    FAIL: CardMatchingEngine status not success/needs_split")
                    continue
            
            print("  SUCCESS: Order passes all filters!")
            
    print("\nCalling finder method directly:")
    orders = await scanner.find_orders_for_user(user, buy_grouped, sell_grouped)
    print(f"Found orders count: {len(orders)}")
    for o in orders:
        print(f"  Order ID: {o.id}, Price: {o.price}, Merchant: {o.merchant_name}")

if __name__ == "__main__":
    asyncio.run(run_simulation())
