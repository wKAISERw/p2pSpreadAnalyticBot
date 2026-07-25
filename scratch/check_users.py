import sqlite3

db_path = "c:/p2p_scanner/data/merchants.db"

def check_users():
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()
    
    print("\n--- SCANNER USERS ---")
    cursor.execute("SELECT user_id, bank_codes, buy_bank_codes, sell_bank_codes FROM scanner_users")
    for row in cursor.fetchall():
        print(dict(row))
        
    conn.close()

if __name__ == "__main__":
    check_users()
