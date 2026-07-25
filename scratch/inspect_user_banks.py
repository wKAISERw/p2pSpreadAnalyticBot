import sqlite3

def main():
    conn = sqlite3.connect("data/merchants.db")
    conn.row_factory = sqlite3.Row
    cur = conn.cursor()
    cur.execute("SELECT user_id, telegram_chat_id, working_capital, bank_codes, buy_bank_codes, sell_bank_codes FROM scanner_users")
    rows = cur.fetchall()
    print("Found scanner_users:")
    for r in rows:
        print(dict(r))
    conn.close()

if __name__ == "__main__":
    main()
