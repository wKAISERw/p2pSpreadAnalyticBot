import sqlite3
import re
import sys

sys.stdout.reconfigure(encoding='utf-8')

# Mock structures and functions from AlertDispatcher
def _clean_and_normalize_banks(banks_input) -> set[str]:
    if not banks_input:
        return set()
    input_str = str(banks_input)
    raw_strings = re.findall(r'[a-zA-Z0-9а-яА-ЯіІёЁєЄїЇґҐ]+', input_str)

    normalized = set()
    name_map = {
        "43": "monobank", "mono": "monobank", "monobank": "monobank", "моно": "monobank", "монобанк": "monobank",
        "14": "privatbank", "privat": "privatbank", "privatbank": "privatbank", "приват": "privatbank", "приватбанк": "privatbank",
        "64": "pumb", "pumb": "pumb", "пумб": "pumb",
        "48": "a-bank", "abank": "a-bank", "a-bank": "a-bank", "абанк": "a-bank", "а-банк": "a-bank",
        "553": "izibank", "izi": "izibank", "izibank": "izibank", "ізі": "izibank", "ізібанк": "izibank",
        "328": "sense", "sense": "sense", "sensebank": "sense", "сенс": "sense", "сенсбанк": "sense"
    }
    for s in raw_strings:
        s_low = s.lower()
        if s_low in name_map:
            normalized.add(name_map[s_low])
        else:
            normalized.add(s_low)
    return normalized

async def run_test():
    conn = sqlite3.connect("data/merchants.db")
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()

    user_id = 1115620363

    # Load user settings
    cursor.execute("SELECT * FROM scanner_users WHERE user_id = ?", (user_id,))
    user = dict(cursor.fetchone())

    # Load active cards
    cursor.execute("SELECT * FROM cards WHERE owner_id = ? AND status='active'", (user_id,))
    user_cards = [dict(r) for r in cursor.fetchall()]
    user_card_names = {str(c["bank_name"]).lower() for c in user_cards}

    # Simulate an opportunity where:
    # - Sell merchant supports Monobank, PrivatBank, A-bank, Pumb
    # - Original matched route had sell_bank = "64" (Pumb)
    opp = {
        "buy_bank": "43",
        "sell_bank": "64",
        "buy_banks_fit": ["43", "14", "64"],
        "sell_banks_fit": ["43", "14", "48", "64"],
    }

    print("user_card_names:", user_card_names)
    
    user_buy_names = _clean_and_normalize_banks(user.get("buy_bank_codes") or user.get("bank_codes"))
    user_sell_names = _clean_and_normalize_banks(user.get("sell_bank_codes") or user.get("bank_codes"))
    opp_buy_names = _clean_and_normalize_banks(opp.get("buy_banks_fit"))
    opp_sell_names = _clean_and_normalize_banks(opp.get("sell_banks_fit"))

    print("user_sell_names:", user_sell_names)
    print("opp_sell_names:", opp_sell_names)

    allowed_buy_names = opp_buy_names & user_buy_names
    allowed_sell_names = opp_sell_names & user_sell_names

    print("allowed_sell_names:", allowed_sell_names)

    cards_buy_match = allowed_buy_names & user_card_names
    cards_sell_match = allowed_sell_names & user_card_names

    print("cards_sell_match:", cards_sell_match)

    engine_buy_name = "monobank"
    engine_sell_name = "pumb"

    final_buy_name = list(cards_buy_match)[0] if cards_buy_match else (
        engine_buy_name if engine_buy_name in allowed_buy_names else (list(allowed_buy_names)[0] if allowed_buy_names else "monobank")
    )
    final_sell_name = list(cards_sell_match)[0] if cards_sell_match else (
        engine_sell_name if engine_sell_name in allowed_sell_names else (list(allowed_sell_names)[0] if allowed_sell_names else "monobank")
    )

    NAME_TO_CODE = {"monobank": "43", "privatbank": "14", "pumb": "64", "a-bank": "48", "izibank": "553", "sense": "328"}
    chosen_buy = NAME_TO_CODE.get(final_buy_name, "43")
    chosen_sell = NAME_TO_CODE.get(final_sell_name, "43")

    print("Chosen buy:", chosen_buy, "Chosen sell:", chosen_sell)

    conn.close()

import asyncio
asyncio.run(run_test())
