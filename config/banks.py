# config/banks.py
"""
Єдиний реєстр банків для всіх бірж.

Внутрішній код (INTERNAL_CODE) — уніфікований ідентифікатор банку
в системі. За основу взято коди Binance/Bybit як найпоширеніші.

Щоб додати новий банк:
  1. Додай рядок в BANKS з internal_code, name і кодами для кожної біржі
  2. Все — жоден інший файл чіпати не треба

Щоб додати нову біржу:
  1. Додай ключ в exchange_codes для кожного банку
  2. Використай BankRegistry.get_exchange_code(internal_code, "NewExchange")
"""
from __future__ import annotations

from typing import Optional

# ─────────────────────────────────────────────────────────────────────────────
# Модель банку
# ─────────────────────────────────────────────────────────────────────────────


class Bank:
    __slots__ = ("internal_code", "name", "exchange_codes")

    def __init__(self, internal_code: str, name: str, exchange_codes: dict):
        self.internal_code = internal_code
        self.name = name
        self.exchange_codes = exchange_codes

    def get_code(self, exchange: str) -> Optional[str]:
        """Повертає API-код банку для конкретної біржі або None."""
        return self.exchange_codes.get(exchange)


# ─────────────────────────────────────────────────────────────────────────────
# Реєстр банків — єдине джерело правди
# Коди взяті з реальних API відповідей бірж
# ─────────────────────────────────────────────────────────────────────────────

BANKS: list[Bank] = [
    Bank(
        internal_code="43",
        name="Monobank",
        exchange_codes={
            "Binance":   "Monobank",
            "Bybit":     "43",
            "OKX":       "Monobank",
            "Wallet":    "monobank",
            "MEXC":      "128",
            "CryptoBot": "choose-method-monobank",
        },
    ),
    Bank(
        internal_code="14",
        name="PrivatBank",
        exchange_codes={
            "Binance":   "PrivatBank",
            "Bybit":     "14",
            "OKX":       "PrivatBank",
            "Wallet":    "privatbank",
            "MEXC":      "131",
            "CryptoBot": "choose-method-privatbank",
        },
    ),
    Bank(
        internal_code="64",
        name="ПУМБ",
        exchange_codes={
            "Binance":   "PUMB",
            "Bybit":     "64",
            "OKX":       "PUMB",
            "Wallet":    "pumb",
            "MEXC":      "133",
            "CryptoBot": "choose-method-pumb",
        },
    ),
    Bank(
        internal_code="48",
        name="А-Банк",
        exchange_codes={
            "Binance":   "A-Bank",
            "Bybit":     "48",
            "OKX":       "A-Bank",
            "Wallet":    "abank",
            "MEXC":      "134",
        },
    ),
    Bank(
        internal_code="99",
        name="Ощадбанк",
        exchange_codes={
            "Binance":   "Oschadbank",
            "Bybit":     "99",
            "MEXC":      "130",
        },
    ),
    Bank(
        internal_code="380",
        name="Raiffeisen Bank",
        exchange_codes={
            "Binance":   "RaiffeisenBankUkraine",
            "Bybit":     "380",
            "MEXC":      "132",
            "CryptoBot": "choose-method-raiffeisenua",
        },
    ),
    Bank(
        internal_code="328",
        name="Sense Bank",
        exchange_codes={
            "Binance":   "SenseBank",
            "Bybit":     "328",
            "OKX":       "Sense SuperApp",
            "MEXC":      "135",
            "CryptoBot": "choose-method-alfabankua",
        },
    ),
    Bank(
        internal_code="319",
        name="OTP Bank",
        exchange_codes={
            "MEXC":      "140",
            "CryptoBot": "choose-method-otpbank",
        },
    ),
    Bank(
        internal_code="553",
        name="izibank",
        exchange_codes={
            "MEXC":      "142",
            "CryptoBot": "choose-method-izibank",
        },
    ),
]


# ─────────────────────────────────────────────────────────────────────────────
# BankRegistry — lookup таблиці (будуються один раз при імпорті)
# ─────────────────────────────────────────────────────────────────────────────

class BankRegistry:
    # internal_code → Bank
    _by_code: dict[str, Bank] = {b.internal_code: b for b in BANKS}

    # (exchange, api_code) → internal_code  — для парсингу відповідей API
    _reverse: dict[tuple[str, str], str] = {}

    def __init_subclass__(cls, **kwargs):
        pass

    @classmethod
    def _build_reverse(cls) -> None:
        for bank in BANKS:
            for exchange, api_code in bank.exchange_codes.items():
                cls._reverse[(exchange, str(api_code).lower())] = bank.internal_code

    @classmethod
    def get_exchange_code(cls, internal_code: str, exchange: str) -> Optional[str]:
        """internal_code + exchange → API код для запиту до біржі."""
        bank = cls._by_code.get(internal_code)
        return bank.get_code(exchange) if bank else None

    @classmethod
    def get_exchange_codes(cls, internal_codes: list[str], exchange: str) -> list[str]:
        """Список internal_codes → список API кодів для конкретної біржі."""
        result = []
        for code in internal_codes:
            api_code = cls.get_exchange_code(code, exchange)
            if api_code:
                result.append(api_code)
        return result

    @classmethod
    def from_api_code(cls, api_code: str, exchange: str) -> Optional[str]:
        """API код біржі → internal_code. Для парсингу відповідей."""
        if not cls._reverse:
            cls._build_reverse()
        return cls._reverse.get((exchange, str(api_code).lower()))

    @classmethod
    def get_name(cls, internal_code: str) -> str:
        """internal_code → людська назва для Telegram."""
        bank = cls._by_code.get(internal_code)
        return bank.name if bank else internal_code

    @classmethod
    def all_codes(cls) -> list[str]:
        """Всі внутрішні коди банків."""
        return list(cls._by_code.keys())

    @classmethod
    def supported_by(cls, exchange: str) -> list[str]:
        """Список internal_codes банків що підтримуються конкретною біржею."""
        return [
            b.internal_code for b in BANKS
            if exchange in b.exchange_codes
        ]


# Ініціалізуємо reverse lookup при імпорті
BankRegistry._build_reverse()


# ─────────────────────────────────────────────────────────────────────────────
# Зручні константи — для scanner.py і налаштувань
# ─────────────────────────────────────────────────────────────────────────────

# Банки що скануємо за замовчуванням (можна змінити через RuntimeConfig)
DEFAULT_BANK_CODES = ["43", "14", "64"]

# Повна назва для логів і алертів
BANK_NAMES = {b.internal_code: b.name for b in BANKS}