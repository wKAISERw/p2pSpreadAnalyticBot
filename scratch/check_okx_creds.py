import sqlite3
from core.security.crypto_utils import CryptoUtils

# Let's import decrypt from user_repo
from core.storage.user_repo import decrypt

def main():
    conn = sqlite3.connect('data/merchants.db')
    conn.row_factory = sqlite3.Row
    cur = conn.execute("SELECT user_id, exchange, api_key, api_secret, passphrase, label FROM user_credentials WHERE exchange='OKX'")
    for r in cur.fetchall():
        try:
            key_dec = decrypt(r['api_key'])
            sec_dec = decrypt(r['api_secret'])
            pass_dec = decrypt(r['passphrase']) if r['passphrase'] else ""
            print(f"User: {r['user_id']}, Label: {r['label']}")
            print(f"  Key length: {len(key_dec)}, Starts with: {key_dec[:5]}")
            print(f"  Secret length: {len(sec_dec)}")
            print(f"  Passphrase length: {len(pass_dec)}, Starts with: {pass_dec[:2]}")
        except Exception as e:
            print(f"Error decrypting for User {r['user_id']}: {e}")

if __name__ == '__main__':
    main()
