import os
import base64
from cryptography.fernet import Fernet
from config import settings

class CryptoUtils:
    @classmethod
    def _get_key(cls) -> bytes:
        # We need a 32-url-safe-base64-encoded bytes string.
        # We use telegram_bot_token as a seed since it's already in the environment
        secret = getattr(settings, "telegram_bot_token", "default_arbix_quantum_secret_key_1234567890")
        if not secret:
            secret = "default_arbix_quantum_secret_key_1234567890"
        
        # padding to 32 bytes
        secret = secret.ljust(32, '0')[:32]
        return base64.urlsafe_b64encode(secret.encode('utf-8'))

    @classmethod
    def encrypt(cls, plaintext: str) -> str:
        if not plaintext:
            return plaintext
        f = Fernet(cls._get_key())
        return f.encrypt(plaintext.encode('utf-8')).decode('utf-8')

    @classmethod
    def decrypt(cls, ciphertext: str) -> str:
        if not ciphertext:
            return ciphertext
        f = Fernet(cls._get_key())
        try:
            return f.decrypt(ciphertext.encode('utf-8')).decode('utf-8')
        except Exception:
            return ""
