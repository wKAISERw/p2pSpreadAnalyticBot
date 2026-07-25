import sys
sys.stdout.reconfigure(encoding='utf-8')
import sqlite3
import json
import re
from config.banks import BANK_NAMES, BankRegistry

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

conn = sqlite3.connect('data/merchants.db')
conn.row_factory = sqlite3.Row

# Get KAIŜER user
user_row = conn.execute("SELECT * FROM scanner_users WHERE user_id = 1115620363").fetchone()
user = dict(user_row)

general_banks = user["bank_codes"].split(",") if user["bank_codes"] else []
buy_codes_raw = user["buy_bank_codes"].strip()
sell_codes_raw = user["sell_bank_codes"].strip()
buy_banks = buy_codes_raw.split(",") if buy_codes_raw else general_banks
sell_banks = sell_codes_raw.split(",") if sell_codes_raw else general_banks

user_data = {
    "bank_codes": general_banks,
    "buy_bank_codes": buy_banks,
    "sell_bank_codes": sell_banks,
}

print("DB Row bank_codes:", repr(user["bank_codes"]))
print("DB Row buy_bank_codes:", repr(user["buy_bank_codes"]))
print("DB Row sell_bank_codes:", repr(user["sell_bank_codes"]))
print("Parsed user buy_bank_codes:", buy_banks)
print("Parsed user sell_bank_codes:", sell_banks)

user_buy_normalized = _clean_and_normalize_banks(user_data["buy_bank_codes"])
user_sell_normalized = _clean_and_normalize_banks(user_data["sell_bank_codes"])

print("user_buy_normalized:", user_buy_normalized)
print("user_sell_normalized:", user_sell_normalized)

# Get KAIŜER cards
user_cards = [dict(r) for r in conn.execute("SELECT * FROM cards WHERE owner_id = 1115620363 AND status = 'active'").fetchall()]
user_card_names = {str(c["bank_name"]).lower() for c in user_cards}
print("user_card_names:", user_card_names)

# Mock the ZeroFeePay vs oleks_bazza opportunity
opp = {
    "buy_bank": "43",
    "sell_bank": "64", # let's assume the highest profit was PUMB
    "buy_banks_fit": ["43"],
    "sell_banks_fit": ["43", "14", "48", "64"]
}

opp_buy_names = _clean_and_normalize_banks(opp["buy_banks_fit"])
opp_sell_names = _clean_and_normalize_banks(opp["sell_banks_fit"])

allowed_buy_names = opp_buy_names & user_buy_normalized
allowed_sell_names = opp_sell_names & user_sell_normalized

print("allowed_buy_names:", allowed_buy_names)
print("allowed_sell_names:", allowed_sell_names)

cards_buy_match = allowed_buy_names & user_card_names
cards_sell_match = allowed_sell_names & user_card_names

print("cards_buy_match:", cards_buy_match)
print("cards_sell_match:", cards_sell_match)

engine_buy_name = str(BankRegistry.get_name(opp["buy_bank"])).lower()
engine_sell_name = str(BankRegistry.get_name(opp["sell_bank"])).lower()

final_buy_name = list(cards_buy_match)[0] if cards_buy_match else (
    engine_buy_name if engine_buy_name in allowed_buy_names else (list(allowed_buy_names)[0] if allowed_buy_names else "monobank")
)
final_sell_name = list(cards_sell_match)[0] if cards_sell_match else (
    engine_sell_name if engine_sell_name in allowed_sell_names else (list(allowed_sell_names)[0] if allowed_sell_names else "monobank")
)

print("final_buy_name:", final_buy_name)
print("final_sell_name:", final_sell_name)

NAME_TO_CODE = {"monobank": "43", "privatbank": "14", "pumb": "64", "a-bank": "48", "izibank": "553", "sense": "328"}
chosen_buy = NAME_TO_CODE.get(final_buy_name, "43")
chosen_sell = NAME_TO_CODE.get(final_sell_name, "43")

print("chosen_buy:", chosen_buy)
print("chosen_sell:", chosen_sell)
