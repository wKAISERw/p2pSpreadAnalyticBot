# core/utils/crypto.py
"""
Шифрування API ключів через Fernet (AES-128-CBC + HMAC-SHA256).
Master key береться з .env → ENCRYPTION_KEY.

Якщо ENCRYPTION_KEY не встановлений — генерує і логує один раз.
Зберігай згенерований ключ в .env, інакше після перезапуску
всі збережені credentials стануть нечитабельними.

Використання:
    from core.utils.crypto import encrypt, decrypt
    encrypted = encrypt("my_secret_api_key")
    original  = decrypt(encrypted)
"""
from __future__ import annotations

import base64
import logging
import os

logger = logging.getLogger("Crypto")

_fernet = None


def _get_fernet():
    global _fernet
    if _fernet is not None:
        return _fernet

    try:
        from cryptography.fernet import Fernet
    except ImportError:
        raise RuntimeError(
            "Потрібен пакет cryptography: pip install cryptography"
        )

    try:
        import dotenv
        dotenv.load_dotenv()
    except ImportError:
        pass

    key_str = os.environ.get("ENCRYPTION_KEY", "")

    if not key_str:
        # Генеруємо новий ключ і логуємо — користувач має зберегти його в .env
        new_key = Fernet.generate_key().decode()
        logger.warning(
            "⚠️  ENCRYPTION_KEY не знайдено в .env!\n"
            "    Додай рядок у .env файл:\n"
            "    ENCRYPTION_KEY=%s\n"
            "    Без цього credentials будуть втрачені після перезапуску!",
            new_key,
        )
        key_str = new_key

    # Fernet вимагає 32-байтовий URL-safe base64 ключ
    try:
        _fernet = Fernet(key_str.encode())
    except Exception:
        # Можливо ключ у старому форматі — конвертуємо
        padded = key_str + "=" * (4 - len(key_str) % 4)
        raw = base64.urlsafe_b64decode(padded)[:32]
        if len(raw) < 32:
            raw = raw + b"\x00" * (32 - len(raw))
        _fernet = Fernet(base64.urlsafe_b64encode(raw))

    return _fernet


def encrypt(plaintext: str) -> str:
    """Шифрує рядок. Повертає base64-encoded зашифрований рядок."""
    if not plaintext:
        return ""
    f = _get_fernet()
    return f.encrypt(plaintext.encode("utf-8")).decode("utf-8")


def decrypt(ciphertext: str) -> str:
    """Розшифровує рядок. Повертає оригінальний plaintext."""
    if not ciphertext:
        return ""
    try:
        f = _get_fernet()
        return f.decrypt(ciphertext.encode("utf-8")).decode("utf-8")
    except Exception as e:
        logger.error("Crypto decrypt failed: %s", e)
        return ""


def is_encrypted(value: str) -> bool:
    """Перевіряє чи виглядає рядок як Fernet-зашифрований."""
    if not value or len(value) < 50:
        return False
    try:
        decoded = base64.urlsafe_b64decode(value + "==")
        return decoded[:1] == b"\x80"  # Fernet magic byte
    except Exception:
        return False