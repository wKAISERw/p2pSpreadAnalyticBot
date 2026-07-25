import sqlite3
import json

db_path = "c:/p2p_scanner/data/merchants.db"

def check_users():
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()
    
    print("\n--- SCANNER USERS DETAIL ---")
    cursor.execute("SELECT * FROM scanner_users")
    for row in cursor.fetchall():
        d = dict(row)
        # remove some super long json fields just in case
        for k in list(d.keys()):
            if d[k] is None or d[k] == "":
                d.pop(k)
        print(d)
        
    conn.close()

if __name__ == "__main__":
    check_users()
