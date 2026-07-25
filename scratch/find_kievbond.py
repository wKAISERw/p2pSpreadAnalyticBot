import sqlite3
import json

def main():
    conn = sqlite3.connect('data/merchants.db')
    conn.row_factory = sqlite3.Row
    
    print("--- Searching for 'kievbond' or 'sa90446076d' in merchant_reviews ---")
    cur = conn.execute("SELECT * FROM merchant_reviews WHERE merchant_id LIKE '%sa90446076d%'")
    rows = cur.fetchall()
    for row in rows:
        print(dict(row))
        
    print("\n--- Searching for 'kievbond' in any table/column if possible (recent scans) ---")
    # Let's see what tables we have
    cur = conn.execute("SELECT name FROM sqlite_master WHERE type='table'")
    tables = [r[0] for r in cur.fetchall()]
    print(f"Tables: {tables}")
    
    for t in tables:
        try:
            cur = conn.execute(f"SELECT * FROM {t} LIMIT 1")
            row = cur.fetchone()
            if row:
                cols = row.keys()
                # find any string columns
                for col in cols:
                    cur2 = conn.execute(f"SELECT * FROM {t} WHERE {col} LIKE '%kievbond%' OR {col} LIKE '%sa90446076d%'")
                    matches = cur2.fetchall()
                    if matches:
                        print(f"Match in table {t}, column {col}:")
                        for m in matches:
                            print(dict(m))
        except Exception as e:
            print(f"Error checking table {t}: {e}")

if __name__ == '__main__':
    main()
