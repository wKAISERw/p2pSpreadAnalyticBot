import sqlite3
import re

def clean_bank_codes(raw_str, valid_codes):
    if not raw_str:
        return ""
    # Extract all digit sequences from the raw string
    digits = re.findall(r'\d+', raw_str)
    # Keep only those that are in valid_codes and de-duplicate
    seen = set()
    cleaned = []
    for d in digits:
        if d in valid_codes and d not in seen:
            cleaned.append(d)
            seen.add(d)
    return ",".join(cleaned)

def main():
    valid_codes = {"43", "14", "64", "48", "99", "380", "328", "319", "553"}
    conn = sqlite3.connect("data/merchants.db")
    conn.row_factory = sqlite3.Row
    cur = conn.cursor()
    
    cur.execute("SELECT user_id, bank_codes, buy_bank_codes, sell_bank_codes FROM scanner_users")
    rows = cur.fetchall()
    
    print("Starting bank codes cleanup in merchants.db...")
    for row in rows:
        user_id = row["user_id"]
        b_codes = row["bank_codes"]
        buy_codes = row["buy_bank_codes"]
        sell_codes = row["sell_bank_codes"]
        
        clean_b = clean_bank_codes(b_codes, valid_codes)
        clean_buy = clean_bank_codes(buy_codes, valid_codes)
        clean_sell = clean_bank_codes(sell_codes, valid_codes)
        
        if clean_b != b_codes or clean_buy != buy_codes or clean_sell != sell_codes:
            print(f"User {user_id}:")
            print(f"  bank_codes:     '{b_codes}' -> '{clean_b}'")
            print(f"  buy_bank_codes:  '{buy_codes}' -> '{clean_buy}'")
            print(f"  sell_bank_codes: '{sell_codes}' -> '{clean_sell}'")
            
            cur.execute(
                "UPDATE scanner_users SET bank_codes = ?, buy_bank_codes = ?, sell_bank_codes = ? WHERE user_id = ?",
                (clean_b, clean_buy, clean_sell, user_id)
            )
            
    conn.commit()
    conn.close()
    print("Cleanup complete!")

if __name__ == "__main__":
    main()
