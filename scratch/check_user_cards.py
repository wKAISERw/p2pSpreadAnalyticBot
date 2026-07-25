import sqlite3
import sys

sys.stdout.reconfigure(encoding='utf-8')
db_path = "c:/p2p_scanner/data/merchants.db"

def check_cards():
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()
    
    print("\n--- USER CARD SETTINGS ---")
    cursor.execute("SELECT * FROM user_card_settings WHERE user_id = 1115620363")
    row = cursor.fetchone()
    if row:
        print(dict(row))
    else:
        print("No card settings found for user 1115620363")
        
    print("\n--- USER CARDS ---")
    cursor.execute("SELECT * FROM cards WHERE owner_id = 1115620363")
    rows = cursor.fetchall()
    for row in rows:
        d = dict(row)
        # Convert any bytes to string or simplify
        print({k: str(v) for k, v in d.items()})
        
    conn.close()

if __name__ == "__main__":
    check_cards()
