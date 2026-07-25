import asyncio
from pathlib import Path
from core.storage.merchant_db import MerchantDB
from core.engine.alert_dispatcher import AlertDispatcher
from bot.alert_builder import SpreadAlert
from exchanges.base import Order

async def test():
    db = MerchantDB(Path("data/merchants.db"))
    await db.start()
    
    # Let's get the active user
    users = await db.get_active_users()
    user = users[0]
    uid = user["user_id"]
    
    # Mock an opportunity matching the one in the user's message:
    # 🔄 Маршрут: 🔀 CROSS | 🔵MEXC → 🟢OKX
    # 🏦 Варіанти зв'язки: PrivatBank → ПУМБ
    # 🛒 Buy банки: PrivatBank
    # ✅ Buy фільтр: PrivatBank
    # 💸 Sell банки: Monobank, PrivatBank, 328, А-Банк, ПУМБ
    # ✅ Sell фільтр: Monobank, PrivatBank, 328, А-Банк, ПУМБ
    
    # Let's mock Order objects
    buy_order = Order(
        exchange="MEXC",
        merchant_id="a36f1ed8b2b843488231c0f7507e151f",
        merchant_name="Armageddon",
        price=43.0,
        available_amount=500.0,
        min_limit=4300.0,
        max_limit=4300.0,
        bank_codes=["14"],
    )
    sell_order = Order(
        exchange="OKX",
        merchant_id="39d0f0418c",
        merchant_name="lerchik_o",
        price=44.26,
        available_amount=310.0,
        min_limit=1000.00,
        max_limit=13720.60,
        bank_codes=["43", "14", "328", "48", "64"],
    )
    
    opp = {
        "buy_order": buy_order,
        "sell_order": sell_order,
        "buy_bank": "14",
        "sell_bank": "64",
        "buy_banks_fit": ["14"],
        "sell_banks_fit": ["43", "14", "328", "48", "64"],
        "net_spread_pct": 2.42,
        "actual_entry_uah": 4300.0,
        "net_profit": 104.07,
    }
    
    alert = SpreadAlert(
        buy_order=buy_order,
        sell_order=sell_order,
        spread_pct=2.42,
        profit_uah=104.07,
        deal_amount_uah=4300.0,
        buy_bank="14",
        sell_bank="64",
        buy_banks_fit=["14"],
        sell_banks_fit=["43", "14", "328", "48", "64"],
    )
    
    # Let's run the dispatcher matching logic
    # 💳 Витягуємо назви брендів твоїх АКТИВНИХ пластикових карт
    user_cards = await db.get_cards(owner_id=uid, status="active")
    user_card_names = {str(c["bank_name"]).lower() for c in user_cards}
    print(f"User Active Cards: {user_card_names}")
    
    # Clean and normalize
    dispatcher = AlertDispatcher(db, None)
    user_buy_names = dispatcher._clean_and_normalize_banks(user.get("buy_bank_codes") or user.get("bank_codes"))
    user_sell_names = dispatcher._clean_and_normalize_banks(user.get("sell_bank_codes") or user.get("bank_codes"))
    opp_buy_names = dispatcher._clean_and_normalize_banks(opp.get("buy_banks_fit"))
    opp_sell_names = dispatcher._clean_and_normalize_banks(opp.get("sell_banks_fit"))
    
    print(f"user_buy_names: {user_buy_names}")
    print(f"user_sell_names: {user_sell_names}")
    print(f"opp_buy_names: {opp_buy_names}")
    print(f"opp_sell_names: {opp_sell_names}")
    
    allowed_buy_names = opp_buy_names & user_buy_names
    allowed_sell_names = opp_sell_names & user_sell_names
    print(f"allowed_buy_names: {allowed_buy_names}")
    print(f"allowed_sell_names: {allowed_sell_names}")
    
    cards_buy_match = allowed_buy_names & user_card_names
    cards_sell_match = allowed_sell_names & user_card_names
    print(f"cards_buy_match: {cards_buy_match}")
    print(f"cards_sell_match: {cards_sell_match}")
    
    from config.banks import BankRegistry
    engine_buy_name = str(BankRegistry.get_name(opp.get("buy_bank", "43"))).lower()
    engine_sell_name = str(BankRegistry.get_name(opp.get("sell_bank", "43"))).lower()
    print(f"engine_buy_name: {engine_buy_name}")
    print(f"engine_sell_name: {engine_sell_name}")
    
    final_buy_name = list(cards_buy_match)[0] if cards_buy_match else (
        engine_buy_name if engine_buy_name in allowed_buy_names else (list(allowed_buy_names)[0] if allowed_buy_names else "monobank")
    )
    final_sell_name = list(cards_sell_match)[0] if cards_sell_match else (
        engine_sell_name if engine_sell_name in allowed_sell_names else (list(allowed_sell_names)[0] if allowed_sell_names else "monobank")
    )
    print(f"final_buy_name: {final_buy_name}")
    print(f"final_sell_name: {final_sell_name}")
    
    NAME_TO_CODE = {"monobank": "43", "privatbank": "14", "pumb": "64", "a-bank": "48", "izibank": "553", "sense": "328"}
    chosen_buy = NAME_TO_CODE.get(final_buy_name, "43")
    chosen_sell = NAME_TO_CODE.get(final_sell_name, "43")
    print(f"chosen_buy: {chosen_buy}")
    print(f"chosen_sell: {chosen_sell}")
    
    await db.disconnect()

if __name__ == "__main__":
    asyncio.run(test())
