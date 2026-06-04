import os
import sys
import time
import asyncio
import tempfile
import sqlite3
from pathlib import Path

# Add project root to python path
sys.path.insert(0, str(Path(__file__).parent.parent.resolve()))

from core.storage.merchant_db import MerchantDB
from core.analytics.stats_engine import StatsEngine


async def check_column_exists(db_path: str, table_name: str, column_name: str) -> bool:
    conn = sqlite3.connect(db_path)
    cursor = conn.cursor()
    cursor.execute(f"PRAGMA table_info({table_name})")
    columns = [row[1] for row in cursor.fetchall()]
    conn.close()
    return column_name in columns


async def main():
    print("--- Arbix Quantum Stats Verification Script ---")

    # 1. Create a temporary database file
    temp_dir = tempfile.TemporaryDirectory()
    db_path = os.path.join(temp_dir.name, "test_merchants.db")
    print(f"Temporary database path: {db_path}")

    # 2. Instantiate and start MerchantDB (triggers migrations)
    db = MerchantDB(db_path=Path(db_path))
    await db.start()
    print("MerchantDB started, schemas initialized and migrations run.")

    # Verify that column user_id exists in scanner_proposals
    has_user_id = await check_column_exists(db_path, "scanner_proposals", "user_id")
    print(f"Verification: column 'user_id' in table 'scanner_proposals': {'[OK] EXISTS' if has_user_id else '[FAIL] MISSING'}")
    assert has_user_id, "user_id column was not successfully added to scanner_proposals"

    # 3. Populate Mock Data
    now = time.time()
    one_day = 86400

    # Mock user IDs
    USER_A = 12345
    USER_B = 67890

    # User details
    await db._db.execute("INSERT OR IGNORE INTO scanner_users (telegram_chat_id, working_capital, is_active) VALUES (?, ?, 1)", (USER_A, 10000.0))
    await db._db.execute("INSERT OR IGNORE INTO scanner_users (telegram_chat_id, working_capital, is_active) VALUES (?, ?, 1)", (USER_B, 20000.0))
    await db._db.commit()

    # Create mockup proposal helper
    async def insert_proposal(user_id, age_days, spread, profit, amount, mode, buy_ex, sell_ex, buy_bank, sell_bank, was_sent=1):
        created_at = now - age_days * one_day
        await db._db.execute(
            """INSERT INTO scanner_proposals 
               (buy_exchange, sell_exchange, buy_merchant, sell_merchant, spread_pct, profit_uah, deal_amount, route_type, buy_bank, sell_bank, was_sent, user_id, created_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (buy_ex, sell_ex, "MerchBuy", "MerchSell", spread, profit, amount, mode, buy_bank, sell_bank, was_sent, user_id, created_at)
        )

    # Insert Proposals for USER A
    # Today
    await insert_proposal(USER_A, 0.1, 1.5, 150.0, 10000.0, "SPREAD", "Binance", "Bybit", "monobank", "privatbank")
    await insert_proposal(USER_A, 0.2, 2.2, 440.0, 20000.0, "SPREAD", "Binance", "OKX", "monobank", "pumb")
    await insert_proposal(USER_A, 0.3, 0.8, 40.0, 5000.0, "TAKER_BUY", "Bybit", "Binance", "privatbank", "monobank")
    # 5 days ago
    await insert_proposal(USER_A, 5.0, 1.2, 120.0, 10000.0, "SPREAD", "OKX", "Bybit", "pumb", "monobank")
    # 10 days ago
    await insert_proposal(USER_A, 10.0, 3.5, 1050.0, 30000.0, "MAKER_SELL", "Binance", "Wallet", "monobank", "monobank")
    # 25 days ago
    await insert_proposal(USER_A, 25.0, 1.8, 180.0, 10000.0, "SPREAD", "Bybit", "Binance", "privatbank", "privatbank")

    # Insert Proposals for USER B
    await insert_proposal(USER_B, 0.1, 3.0, 300.0, 10000.0, "SPREAD", "Binance", "Bybit", "monobank", "privatbank")
    await insert_proposal(USER_B, 2.0, 1.1, 110.0, 10000.0, "TAKER_SELL", "OKX", "Binance", "pumb", "monobank")

    # Global / system proposals (user_id = 0 or unassigned)
    await insert_proposal(0, 0.5, 0.5, 25.0, 5000.0, "SPREAD", "Binance", "Bybit", "monobank", "privatbank")

    # Insert completed trade sessions & active trades (for user A and user B)
    # Trade Session helper
    async def insert_trade(user_id, age_days, profit, mode, exchange, bank, status="COMPLETED"):
        # Format date for sqlite (DATETIME format)
        t_struct = time.localtime(now - age_days * one_day)
        date_str = time.strftime("%Y-%m-%d %H:%M:%S", t_struct)
        
        # 1. Insert session
        cursor = await db._db.execute(
            """INSERT INTO trade_sessions (strategy, route_type, buy_exchange, session_status, gross_profit, created_at, completed_at)
               VALUES (?, ?, ?, ?, ?, ?, ?)""",
            ("STG", mode, exchange, status, profit, date_str, date_str)
        )
        session_id = cursor.lastrowid
        
        # 2. Insert active trade leg 1 (Buy)
        await db._db.execute(
            """INSERT INTO active_trades (session_id, strategy, leg, route_type, owner_user_id, exchange, status, payment_method, fiat_amount, created_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (session_id, "STG", "BUY", mode, user_id, exchange, status, bank, 10000.0, date_str)
        )
        
        # 3. Insert active trade leg 2 (Sell)
        await db._db.execute(
            """INSERT INTO active_trades (session_id, strategy, leg, route_type, owner_user_id, exchange, status, payment_method, fiat_amount, created_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (session_id, "STG", "SELL", mode, user_id, exchange, status, bank, 10000.0 + profit, date_str)
        )

    # Trades for USER A
    await insert_trade(USER_A, 0.1, 150.0, "SPREAD", "Binance", "Monobank")
    await insert_trade(USER_A, 0.2, 220.0, "SPREAD", "Bybit", "PrivatBank")
    await insert_trade(USER_A, 4.0, 180.0, "TAKER_BUY", "Binance", "Monobank")
    await insert_trade(USER_A, 12.0, 300.0, "MAKER_SELL", "OKX", "ПУМБ")
    await insert_trade(USER_A, 28.0, 90.0, "SPREAD", "Wallet", "Monobank")

    # Trades for USER B
    await insert_trade(USER_B, 0.5, 400.0, "SPREAD", "Binance", "PrivatBank")
    
    await db._db.commit()
    print("Mock data successfully populated.")

    # 4. Instantiate StatsEngine
    engine = StatsEngine(db)

    # ── Test 1: Proposals Summary ──
    print("\n--- Test 1: Proposals Summary ---")
    
    # Check that USER A query is isolated and respects period
    summary_a_7d = await engine.get_proposals_summary(period_days=7, user_id=USER_A, mode="ALL")
    print(f"USER A (7d, ALL): total={summary_a_7d.get('total')}, avg_spread={summary_a_7d.get('avg_spread')}%, sent={summary_a_7d.get('sent')}")
    assert summary_a_7d.get("total") == 4, f"Expected 4 proposals for USER A in 7d, got {summary_a_7d.get('total')}"

    summary_a_14d_spread = await engine.get_proposals_summary(period_days=14, user_id=USER_A, mode="SPREAD")
    print(f"USER A (14d, SPREAD): total={summary_a_14d_spread.get('total')}")
    assert summary_a_14d_spread.get("total") == 3, f"Expected 3 SPREAD proposals for USER A in 14d, got {summary_a_14d_spread.get('total')}"

    summary_b_7d = await engine.get_proposals_summary(period_days=7, user_id=USER_B, mode="ALL")
    print(f"USER B (7d, ALL): total={summary_b_7d.get('total')}")
    assert summary_b_7d.get("total") == 2, f"Expected 2 proposals for USER B in 7d, got {summary_b_7d.get('total')}"

    # ── Test 2: Heatmap Activity Queries ──
    print("\n--- Test 2: Heatmap Activity Queries ---")
    
    # Market proposals activity heatmap
    heatmap_proposals = await engine.get_proposals_hourly_heatmap(period_days=14, user_id=USER_A, mode="ALL")
    total_heatmap_proposals = sum(heatmap_proposals[d][h] for d in heatmap_proposals for h in heatmap_proposals[d])
    print(f"Heatmap proposals total count (14d, USER A): {total_heatmap_proposals}")
    assert total_heatmap_proposals == 5, f"Expected 5 proposals in heatmap for USER A (14d), got {total_heatmap_proposals}"

    # Personal trades activity heatmap
    heatmap_trades = await engine.get_my_hourly_heatmap(period_days=30, owner_user_id=USER_A, mode="ALL")
    total_heatmap_trades = sum(heatmap_trades[d][h] for d in heatmap_trades for h in heatmap_trades[d])
    print(f"Heatmap personal trades total count (30d, USER A): {total_heatmap_trades}")
    assert total_heatmap_trades == 5, f"Expected 5 completed trade sessions for USER A (30d), got {total_heatmap_trades}"

    # ── Test 3: volume-based "Spread Cloud" aggregation ──
    print("\n--- Test 3: Spread Cloud Volume Aggregation ---")
    
    vol_data = await engine.get_proposals_by_volume(period_days=30, user_id=USER_A, mode="ALL")
    print("Spread distribution by volume:")
    for v in vol_data:
        print(f"  {v['range_name']}: count={v['count']}, avg_spread={v['avg_spread']}%, max_spread={v['max_spread']}%")
    
    assert len(vol_data) > 0, "Volume distribution data should not be empty"

    # ── Test 4: Day detail hourly breakdown ──
    print("\n--- Test 4: Day Detail Report ---")
    
    t_struct = time.localtime(now)
    today_str = time.strftime("%Y-%m-%d", t_struct)
    
    # Scanner proposals detail
    scanner_detail = await engine.format_day_detail_report(today_str, user_id=USER_A, mode="ALL", source="scanner")
    print("Scanner Detail Output Preview:")
    try:
        print(scanner_detail)
    except UnicodeEncodeError:
        print(scanner_detail.encode('ascii', errors='replace').decode('ascii'))
        
    assert "Деталі за" in scanner_detail or "Detail for" in scanner_detail
    assert "спредів" in scanner_detail or "spreads" in scanner_detail

    # Personal trade detail
    my_detail = await engine.format_day_detail_report(today_str, user_id=USER_A, mode="ALL", source="my")
    print("Personal Detail Output Preview:")
    try:
        print(my_detail)
    except UnicodeEncodeError:
        print(my_detail.encode('ascii', errors='replace').decode('ascii'))
        
    assert "угоди" in my_detail or "trades" in my_detail

    # ── Test 5: Standard Formatting Methods ──
    print("\n--- Test 5: Formatting Reports (UAH Brackets, heatmaps, exchanges) ---")
    
    # Daily Report
    daily_report = await engine.format_daily_report(period_days=30, owner_user_id=USER_A, mode="ALL")
    assert "Прибуток по днях" in daily_report
    print("Daily report formatted successfully.")

    # Proposals Report (includes the new volume-based Spread Cloud table)
    proposals_report = await engine.format_proposals_report(period_days=30, user_id=USER_A, mode="ALL")
    assert "об'ємом" in proposals_report
    print("Proposals report formatted successfully.")
    print("Proposals Report Preview:")
    try:
        print(proposals_report)
    except UnicodeEncodeError:
        print(proposals_report.encode('ascii', errors='replace').decode('ascii'))

    # Exchanges Report
    exchanges_report = await engine.format_exchanges_report(period_days=30, owner_user_id=USER_A, mode="ALL")
    assert "біржах" in exchanges_report
    print("Exchanges report formatted successfully.")

    # Banks Report
    banks_report = await engine.format_banks_report(period_days=30, owner_user_id=USER_A, mode="ALL")
    assert "банках" in banks_report
    print("Banks report formatted successfully.")

    # Clean up
    await db.stop()
    temp_dir.cleanup()
    print("\n--- ALL TESTS COMPLETED SUCCESSFULLY! ---")


if __name__ == "__main__":
    asyncio.run(main())
