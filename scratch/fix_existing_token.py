import asyncio
import os
import base64
import dotenv
from pathlib import Path
from cryptography.fernet import Fernet
from core.storage.merchant_db import MerchantDB
from core.utils.crypto import encrypt as standard_encrypt

dotenv.load_dotenv()

def get_old_key() -> bytes:
    bot_token = os.environ.get("TELEGRAM_BOT_TOKEN", "default_arbix_quantum_secret_key_1234567890")
    secret = bot_token.ljust(32, '0')[:32]
    return base64.urlsafe_b64encode(secret.encode('utf-8'))

async def migrate_token():
    db = MerchantDB(Path("data/merchants.db"))
    await db.start()
    
    conn = db._db
    async with conn.execute("SELECT id, bank_name, last_four, mono_x_token_encrypted FROM cards WHERE bank_name='monobank'") as cur:
        rows = await cur.fetchall()
        
    for r in rows:
        card_id = r["id"]
        last_four = r["last_four"]
        token_enc = r["mono_x_token_encrypted"]
        if not token_enc:
            continue
            
        print(f"Processing card *{last_four}...")
        
        # Let's split it into candidates
        candidates = [token_enc]
        # split by '-'
        if '-' in token_enc:
            parts = token_enc.split('-')
            candidates.append(parts[0])
            candidates.append(parts[0] + "==")
            if len(parts) > 1:
                candidates.append(parts[1])
                candidates.append(parts[1] + "==")
                candidates.append("gAAAAABoMDG0WJ_Y4i6R1b92Lh20y00vD-mXj2w0Vb98G-1XyGgW35R3S3_8aZ-" + parts[1])
                candidates.append("gAAAAABo" + parts[1])
                
        f_old = Fernet(get_old_key())
        
        success = False
        for cand in candidates:
            try:
                # pad cand properly
                padded_cand = cand
                if not padded_cand.endswith("==") and len(padded_cand) % 4 != 0:
                    padded_cand += "=" * (4 - len(padded_cand) % 4)
                print(f"Trying candidate: {repr(padded_cand)} (len: {len(padded_cand)})")
                token = f_old.decrypt(padded_cand.encode('utf-8')).decode('utf-8')
                print(f"SUCCESS! Decrypted: {token}")
                
                # Re-encrypt with standard
                new_enc = standard_encrypt(token)
                await conn.execute("UPDATE cards SET mono_x_token_encrypted=? WHERE id=?", (new_enc, card_id))
                await conn.commit()
                print("Migrated successfully!")
                success = True
                break
            except Exception as e:
                pass
                
        if not success:
            print("All candidates failed to decrypt.")
            
    await db.disconnect()

if __name__ == "__main__":
    asyncio.run(migrate_token())
