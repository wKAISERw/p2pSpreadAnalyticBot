# core/security/crypto_utils.py
from core.utils.crypto import encrypt as _encrypt, decrypt as _decrypt

class CryptoUtils:
    @classmethod
    def encrypt(cls, plaintext: str) -> str:
        if not plaintext:
            return plaintext
        return _encrypt(plaintext)

    @classmethod
    def decrypt(cls, ciphertext: str) -> str:
        if not ciphertext:
            return ciphertext
        return _decrypt(ciphertext)
