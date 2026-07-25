import sqlite3
import re
import sys

sys.stdout.reconfigure(encoding='utf-8')

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

def _select_best_bank_from_owned(allowed_banks: set[str], owned_cards: list[dict]) -> str | None:
    if not allowed_banks or not owned_cards:
        return None
    candidates = {}
    status_scores = {
        "active": 10,
        "cooldown": 6,
        "inactive": 5,
        "frozen_funds": 2,
        "frozen": 1,
        "blocked": 0
    }
    for card in owned_cards:
        b_name = str(card.get("bank_name", "")).lower()
        if b_name in allowed_banks:
            status = str(card.get("status", "")).lower()
            score = status_scores.get(status, 1)
            candidates[b_name] = max(candidates.get(b_name, -1), score)
    if not candidates:
        return None
    # Sort candidates by score descending, secondary key alphabetically for stability
    sorted_candidates = sorted(candidates.items(), key=lambda x: (-x[1], x[0]))
    return sorted_candidates[0][0]

async def run_test():
    # Simulate user setting: they allow Monobank, PrivatBank, Pumb
    user_buy_names = {"monobank", "privatbank", "pumb"}
    user_sell_names = {"monobank", "privatbank", "pumb"}

    # Simulate cards in wallet: Monobank (inactive) and PrivatBank (frozen_funds)
    user_all_cards = [
        {"bank_name": "monobank", "status": "inactive"},
        {"bank_name": "privatbank", "status": "frozen_funds"}
    ]
    # Active cards would be empty
    user_card_names = set()

    # Simulate opportunity: PUMB -> PrivatBank
    opp = {
        "buy_bank": "64", # PUMB
        "sell_bank": "14", # PrivatBank
        "buy_banks_fit": ["monobank", "privatbank", "pumb"],
        "sell_banks_fit": ["monobank", "privatbank", "pumb"]
    }

    opp_buy_names = _clean_and_normalize_banks(opp.get("buy_banks_fit"))
    opp_sell_names = _clean_and_normalize_banks(opp.get("sell_banks_fit"))

    allowed_buy_names = opp_buy_names & user_buy_names
    allowed_sell_names = opp_sell_names & user_sell_names

    cards_buy_match = allowed_buy_names & user_card_names
    cards_sell_match = allowed_sell_names & user_card_names

    engine_buy_name = "pumb"
    engine_sell_name = "privatbank"

    # --- BEFORE FIX ---
    old_buy_name = list(cards_buy_match)[0] if cards_buy_match else (
        engine_buy_name if engine_buy_name in allowed_buy_names else (list(allowed_buy_names)[0] if allowed_buy_names else "monobank")
    )
    old_sell_name = list(cards_sell_match)[0] if cards_sell_match else (
        engine_sell_name if engine_sell_name in allowed_sell_names else (list(allowed_sell_names)[0] if allowed_sell_names else "monobank")
    )

    # --- AFTER FIX ---
    new_buy_name = list(cards_buy_match)[0] if cards_buy_match else (
        _select_best_bank_from_owned(allowed_buy_names, user_all_cards) or
        (engine_buy_name if engine_buy_name in allowed_buy_names else (list(allowed_buy_names)[0] if allowed_buy_names else "monobank"))
    )
    new_sell_name = list(cards_sell_match)[0] if cards_sell_match else (
        _select_best_bank_from_owned(allowed_sell_names, user_all_cards) or
        (engine_sell_name if engine_sell_name in allowed_sell_names else (list(allowed_sell_names)[0] if allowed_sell_names else "monobank"))
    )

    print("--- BEFORE FIX ---")
    print(f"Chosen Buy: {old_buy_name} | Chosen Sell: {old_sell_name}")

    print("--- AFTER FIX ---")
    print(f"Chosen Buy: {new_buy_name} | Chosen Sell: {new_sell_name}")

    assert old_buy_name == "pumb"
    assert old_sell_name == "privatbank"
    
    assert new_buy_name == "monobank"
    assert new_sell_name == "monobank"
    print("SUCCESS: Tests passed!")

import asyncio
asyncio.run(run_test())
