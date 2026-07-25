import os
import base64
import dotenv
from cryptography.fernet import Fernet

dotenv.load_dotenv()

# Old key generation logic using bot token
def get_old_key(bot_token) -> bytes:
    secret = bot_token.ljust(32, '0')[:32]
    return base64.urlsafe_b64encode(secret.encode('utf-8'))

def test():
    # Candidate token
    token_enc = "gAAAAABoMDG0WJ_Y4i6R1b92Lh20y00vD-mXj2w0Vb98G-1XyGgW35R3S3_8aZ-=="
    
    keys = [
        os.environ.get("TELEGRAM_BOT_TOKEN", ""),
        "default_arbix_quantum_secret_key_1234567890"
    ]
    
    for key in keys:
        if not key:
            continue
        print(f"Trying key seed: {repr(key)}")
        try:
            f_old = Fernet(get_old_key(key))
            decrypted = f_old.decrypt(token_enc.encode('utf-8')).decode('utf-8')
            print(f"Decrypted successfully! Value: {decrypted}")
            return
        except Exception as e:
            print(f"Decryption failed: {type(e).__name__} {e}")

if __name__ == "__main__":
    test()
