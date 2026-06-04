import sqlite3

db_path = "c:/p2p_scanner/data/merchants.db"

def check_cards():
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()
    
    print("--- ALL CARDS ---")
    cursor.execute("SELECT id, owner_id, bank_name, last_four, status, balance, is_own FROM cards")
    for row in cursor.fetchall():
        print(dict(row))
        
    print("\n--- USER CARD SETTINGS ---")
    cursor.execute("SELECT * FROM user_card_settings")
    for row in cursor.fetchall():
        print(dict(row))
        
    conn.close()

if __name__ == "__main__":
    check_cards()
