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

from dataclasses import dataclass
from typing import Optional

# Ліміт «без обмежень». Та сама домовленість, що вже діє в БД лімітів карток:
# -1 означає «не обмежувати», а не «нуль».
UNLIMITED = -1.0

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

    @property
    def profile(self) -> "BankProfile":
        """Операційний профіль банку — ліміти, комісії, режим роботи."""
        return get_bank_profile(self.internal_code)


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
            "BingX":     "113",
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
            "BingX":     "164",
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
            "BingX":     "114",
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
            "BingX":     "115",
        },
    ),
    Bank(
        internal_code="99",
        name="Ощадбанк",
        exchange_codes={
            "Binance":   "Oschadbank",
            "Bybit":     "99",
            "OKX":       "Oschad Bank",
            "MEXC":      "130",
        },
    ),
    Bank(
        internal_code="380",
        name="Raiffeisen Bank",
        exchange_codes={
            "Binance":   "RaiffeisenBankUkraine",
            "Bybit":     "380",
            # OKX пише назву з помилкою — «Raiffaisen» замість «Raiffeisen».
            # Виправляти нема що: у їхньому API це буквальне значення поля,
            # і зіставляти треба саме з ним.
            "OKX":       "Raiffaisen Bank",
            "MEXC":      "132",
            "CryptoBot": "choose-method-raiffeisenua",
            "BingX":     "235",
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
            "BingX":     "170",
        },
    ),
    Bank(
        internal_code="319",
        name="OTP Bank",
        exchange_codes={
            "OKX":       "OTP Bank",
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
    # БВР довго жив лише в довіднику лімітів: вважалось, що жодна біржа
    # його не віддає. OKX віддає — назвою «Bank Vlasnyi Rakhunok», і 1 495
    # ордерів осіли в невідомих кодах, хоча профіль із лімітами, комісією
    # і спільною з Востоком ліцензією для нього вже був.
    Bank(
        internal_code="bvr",
        name="БВР",
        exchange_codes={
            "OKX": "Bank Vlasnyi Rakhunok",
        },
    ),
]


# Другі написання тих самих банків у відповідях бірж.
#
# `exchange_codes` тримає по одному коду на біржу, а біржі того самого
# банку називають по-різному: Binance віддає і «A-Bank», і «ABank», і
# «PUMBBank» замість «PUMB». Кожне таке написання випадало в невідомі —
# у діагностиці бота вони й накопичились: ABank 1826 разів, PUMBBank 1481,
# Izibank на OKX 1725. Це не нові банки, і заводити їх як нові не треба.
_EXCHANGE_ALIASES: dict[tuple[str, str], str] = {
    ("Binance", "abank"): "48",        # А-Банк
    ("Binance", "a-bank (card)"): "48",
    ("Binance", "pumbbank"): "64",     # ПУМБ
    ("Binance", "pumb (card)"): "64",
    ("OKX", "izibank"): "553",
}


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
        for (exchange, api_code), internal in _EXCHANGE_ALIASES.items():
            cls._reverse[(exchange, api_code.lower())] = internal

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


# ─────────────────────────────────────────────────────────────────────────────
# Нормалізація назв банків
# ─────────────────────────────────────────────────────────────────────────────
#
# Одна й та сама мапа («43» → monobank, «моно» → monobank, «pb» → privatbank…)
# лежала скопійованою в чотирьох файлах: alert_dispatcher, taker_scanner,
# card_repo і formatters. Кожна копія жила своїм життям, і додати банк
# означало не забути про решту трьох — інакше движок і картки почали б
# розуміти під тим самим словом різні речі.
#
# Тепер джерело одне. Розширювати треба тут.

# Синоніми поверх канонічних назв із BANKS. Ключі — у нижньому регістрі.
_BANK_ALIASES: dict[str, str] = {
    "mono": "monobank", "моно": "monobank", "монобанк": "monobank",
    "pb": "privatbank", "privat": "privatbank",
    "приват": "privatbank", "приватбанк": "privatbank",
    "пумб": "pumb",
    "abank": "a-bank", "абанк": "a-bank", "а-банк": "a-bank",
    "izi": "izibank", "ізі": "izibank", "ізібанк": "izibank",
    "sensebank": "sense", "сенс": "sense", "сенсбанк": "sense",
    "sense bank": "sense",
    "ощадбанк": "oschadbank", "oschad": "oschadbank",
    "raiffeisen bank": "raiffeisen", "райф": "raiffeisen",
    "otp bank": "otp", "отп": "otp",
    # Банки, які є в довіднику лімітів (BANK_PROFILES), але не в BANKS:
    # жодна з підключених бірж їх не віддає, а картки в них користувач має.
    "таскомбанк": "taskombank", "tascombank": "taskombank", "таском": "taskombank",
    # OKX пише його повною назвою — «Банк Власний Рахунок». Без цього рядка
    # він проходив нормалізацію наскрізь і осідав у невідомих кодах, хоча
    # профіль із лімітами й спільною ліцензією для нього давно є.
    "бвр": "bvr", "bvr bank": "bvr",
    "bank vlasnyi rakhunok": "bvr", "банк власний рахунок": "bvr",
    "власний рахунок": "bvr",
    "банк восток": "vostok", "восток": "vostok", "bank vostok": "vostok",
    "глобус": "globus", "globus bank": "globus",
    "кредобанк": "kredobank", "kredo": "kredobank",
    "кредит дніпро": "credit-dnipro", "credit dnipro": "credit-dnipro",
    "creditdnipro": "credit-dnipro",
    "грант": "grant", "grant bank": "grant",
    "банк львів": "lviv", "львів": "lviv", "bank lviv": "lviv",
    "прокредит": "procredit", "procredit bank": "procredit",
    "радабанк": "rada", "рада": "rada",
    "комінбанк": "cominbank", "комінвестбанк": "cominbank",
    "укрсиббанк": "ukrsibbank", "укрсиб": "ukrsibbank", "ukrsib": "ukrsibbank",
    "юнекс": "unex", "unex bank": "unex",
    "південний": "pivdenny", "pivdennyi": "pivdenny",
    "креді агріколь": "credit-agricole", "credit agricole": "credit-agricole",
    "creditagricole": "credit-agricole",
}

# Внутрішній код («43») → канонічна коротка назва («monobank»).
#
# Мапа явна, а не похідна від Bank.name: назви в реєстрі місцями українські
# («ПУМБ») або довші за слаг («Sense Bank»), і автоматичне перетворення дало
# б «пумб» і «sense-bank» — тобто рядки, яких решта коду не знає. Слаги тут
# рівно ті, що вже використовувались у копіях мапи по движку.
_CODE_TO_SLUG: dict[str, str] = {
    "43": "monobank",
    "14": "privatbank",
    "64": "pumb",
    "48": "a-bank",
    "99": "oschadbank",
    "380": "raiffeisen",
    "328": "sense",
    "319": "otp",
    "553": "izibank",
    # internal_code БВР текстовий, бо числового коду біржі в нього немає —
    # OKX ідентифікує його назвою.
    "bvr": "bvr",
    # Альтернативні коди, під якими ті самі банки приходять від бірж.
    # Були відомі лише bot/formatters.py — тепер їх розуміє вся система.
    "61": "a-bank",
    "80": "pumb",
    "1": "monobank",
}


def normalize_bank(value: str) -> str:
    """
    Зводить будь-яке написання банку до канонічного: код, англійська чи
    українська назва, скорочення. Невідоме значення повертається як є, у
    нижньому регістрі — щоб порівняння лишалось передбачуваним.
    """
    if not value:
        return ""

    key = str(value).strip().lower()
    if key in _CODE_TO_SLUG:
        return _CODE_TO_SLUG[key]
    if key in _BANK_ALIASES:
        return _BANK_ALIASES[key]
    return key


def normalize_banks(values) -> set[str]:
    """Нормалізує список/рядок банків у множину канонічних назв."""
    if not values:
        return set()
    if isinstance(values, str):
        values = [chunk for chunk in values.replace(";", ",").split(",")]
    return {normalize_bank(v) for v in values if str(v).strip()}


# ─────────────────────────────────────────────────────────────────────────────
# Операційний профіль банку
# ─────────────────────────────────────────────────────────────────────────────
#
# До цього ліміти були спільні для всіх банків: 150к/добу, 400к/місяць,
# 29 999 за переказ, 15 транзакцій. Тобто бот спокійно розписував 15 переказів
# на Izibank, де безпечно 2–3, і вважав доступними 400к там, де верхня межа
# 60–100к. Це не похибка округлення — це маршрут, який веде до блокування
# картки.
#
# Правило заповнення: None означає «даних немає». Порожнє поле падає на
# глобальний дефолт (config/card_limits.py), і це чесніше за вигадану цифру.
# Джерело — операційна зведенка з профільного каналу (08.04.2026) плюс дані
# користувача; див. PLAN_CARD_MATCHING.md, розділ 7.


@dataclass(frozen=True)
class P2PFee:
    """
    Комісія банку за вихідний P2P-переказ.

    Майже всі банки тарифікують порогом: безкоштовно до якоїсь суми або
    кількості переказів на місяць, далі відсоток (іноді плюс фікс).
    """
    pct: float = 0.0
    fixed_uah: float = 0.0
    free_until_uah: Optional[float] = None      # безкоштовно, поки оборот нижчий
    free_tx_per_month: Optional[int] = None     # …або поки переказів менше
    cross_bank_only: bool = False               # комісія лише в інший банк
    label: str = ""


@dataclass(frozen=True)
class NightWindow:
    """Нічне вікно, коли банк обмежує перекази. max_uah=None — заборонено зовсім."""
    from_hour: int
    to_hour: int
    max_uah: Optional[float] = None


@dataclass(frozen=True)
class BankProfile:
    tier: int = 3                                # вага у скорингу картки
    safe_monthly_uah: Optional[float] = None     # рекомендована місячна межа
    max_monthly_uah: Optional[float] = None      # вище — ризик блоку різко зростає
    safe_tx_per_day: Optional[int] = None        # скільки переказів не привертає уваги
    single_tx_limit_uah: Optional[float] = None  # стеля одного переказу; UNLIMITED = без неї
    p2p_fee: Optional[P2PFee] = None
    business_days_only: bool = False             # IBAN не піде у вихідні
    night_window: Optional[NightWindow] = None
    license_group: str = ""                      # спільна ліцензія + спільний фінмон
    termination_fee_pct: float = 0.0             # комісія при розриві контракту
    third_party_friendly: Optional[bool] = None
    note: str = ""


# Ключ — канонічний слаг із normalize_bank(), а не internal_code: у BANKS
# живуть лише банки, які підтримує хоч одна біржа, а картку користувач може
# завести в будь-якому. Таскомбанк і БВР бірж не цікавлять, але їхні ліміти
# й спільна ліцензія цікавлять матчинг.
BANK_PROFILES: dict[str, BankProfile] = {
    # ── Tier 1 ────────────────────────────────────────────────────────────
    "monobank": BankProfile(
        tier=1,
        safe_monthly_uah=100_000, max_monthly_uah=150_000,
        safe_tx_per_day=15,
        single_tx_limit_uah=UNLIMITED,
        p2p_fee=P2PFee(pct=0.0, label="Monobank (0% по Україні)"),
        note="часті фінмони; 2+ чарджбеки → перевірка → розрив",
    ),
    "privatbank": BankProfile(
        tier=1,
        safe_monthly_uah=100_000, max_monthly_uah=150_000,
        safe_tx_per_day=20,
        single_tx_limit_uah=29_999,
        p2p_fee=P2PFee(pct=0.5, cross_bank_only=True,
                       label="ПриватБанк міжбанк (0.5%)"),
    ),
    "pumb": BankProfile(
        tier=1,
        safe_monthly_uah=70_000, max_monthly_uah=100_000,
        # tx/день, стеля переказу й комісія — даних немає, дозаповнити
        # з першоджерела. Порожнє поле піде на глобальний дефолт.
    ),
    "globus": BankProfile(
        tier=1,
        safe_monthly_uah=100_000,
        safe_tx_per_day=10,
        single_tx_limit_uah=24_999,
        p2p_fee=P2PFee(pct=1.0, free_until_uah=50_000, free_tx_per_month=10,
                       label="Глобус (1% понад 50к / 10 переказів)"),
        business_days_only=True,
        note="між банками 1 переказ на день",
    ),

    # ── Tier 2 ────────────────────────────────────────────────────────────
    "oschadbank": BankProfile(
        tier=2,
        safe_monthly_uah=80_000,
        # «не критично» — окремої цифри джерело не дає
        single_tx_limit_uah=29_999,
        p2p_fee=P2PFee(pct=1.0, fixed_uah=5.0, label="Ощадбанк (1% + 5 ₴)"),
        night_window=NightWindow(from_hour=22, to_hour=7, max_uah=5_000),
    ),
    "kredobank": BankProfile(
        tier=2,
        safe_monthly_uah=80_000,
        safe_tx_per_day=7,
        single_tx_limit_uah=24_999,
        p2p_fee=P2PFee(pct=0.7, fixed_uah=2.5, label="KredoBank (0.7% + 2.5 ₴)"),
    ),
    "sense": BankProfile(
        tier=2,
        safe_monthly_uah=70_000, max_monthly_uah=100_000,
        safe_tx_per_day=10,
        single_tx_limit_uah=29_999,
        p2p_fee=P2PFee(pct=1.0, fixed_uah=5.0, free_until_uah=20_000,
                       label="Sense (1% + 5 ₴ понад 20к)"),
        note="IBAN 24/7",
    ),
    "a-bank": BankProfile(
        tier=2,
        safe_monthly_uah=60_000, max_monthly_uah=100_000,
        safe_tx_per_day=5,
        single_tx_limit_uah=29_999,
        p2p_fee=P2PFee(pct=2.0, free_until_uah=100_000,
                       label="А-Банк (2% понад 100к)"),
        termination_fee_pct=30.0,
    ),
    "izibank": BankProfile(
        tier=2,
        safe_monthly_uah=60_000, max_monthly_uah=100_000,
        safe_tx_per_day=3,
        single_tx_limit_uah=29_999,
        p2p_fee=P2PFee(pct=2.0, fixed_uah=5.0, free_until_uah=100_000,
                       free_tx_per_month=20,
                       label="Izibank (2% + 5 ₴ понад 100к / 20 переказів)"),
        license_group="tascombank",
        termination_fee_pct=20.0,
    ),
    "taskombank": BankProfile(
        tier=2,
        safe_monthly_uah=50_000,
        safe_tx_per_day=5,
        single_tx_limit_uah=24_999,
        p2p_fee=P2PFee(pct=0.5, fixed_uah=10.0, label="Таскомбанк (0.5% + 10 ₴)"),
        license_group="tascombank",
        termination_fee_pct=20.0,
    ),
    "bvr": BankProfile(
        tier=2,
        safe_monthly_uah=40_000,
        safe_tx_per_day=7,
        single_tx_limit_uah=29_999,
        p2p_fee=P2PFee(pct=0.5, free_tx_per_month=5,
                       label="БВР (0.5% після 5 переказів)"),
        business_days_only=True,
        license_group="vostok",
        termination_fee_pct=30.0,
    ),
    "vostok": BankProfile(
        tier=2,
        license_group="vostok",
    ),

    # ── Банки, про які джерело дає лише окремі факти ───────────────────────
    # Решта полів свідомо порожня: краще глобальний дефолт, ніж вигадана цифра.
    "otp": BankProfile(single_tx_limit_uah=UNLIMITED),
    "credit-dnipro": BankProfile(single_tx_limit_uah=99_999, business_days_only=True),
    "lviv": BankProfile(single_tx_limit_uah=24_999),
    "grant": BankProfile(single_tx_limit_uah=20_000, safe_tx_per_day=5,
                         safe_monthly_uah=50_000, business_days_only=True),
    "procredit": BankProfile(business_days_only=True),
    "rada": BankProfile(business_days_only=True),
    "cominbank": BankProfile(business_days_only=True),
    "ukrsibbank": BankProfile(business_days_only=True),
    "unex": BankProfile(business_days_only=True),
    "pivdenny": BankProfile(business_days_only=True),
    "credit-agricole": BankProfile(business_days_only=True,
                                   note="відділення працює 10:00–16:00"),
}

# Профіль за замовчуванням для банку, якого немає в довіднику.
# Порожній: усі рішення падають на глобальні дефолти з config/card_limits.py.
DEFAULT_BANK_PROFILE = BankProfile()


def get_bank_profile(bank: str) -> BankProfile:
    """
    Профіль банку за будь-яким написанням — кодом, слагом, назвою.
    Невідомий банк отримує порожній профіль, а не виняток.
    """
    return BANK_PROFILES.get(normalize_bank(bank), DEFAULT_BANK_PROFILE)


# Людські назви для слагів. BANKS покриває лише банки, які віддає хоч одна
# біржа, тож для «карткових» банків із довідника назви задані тут.
BANK_SLUG_NAMES: dict[str, str] = {
    **{_CODE_TO_SLUG[b.internal_code]: b.name
       for b in BANKS if b.internal_code in _CODE_TO_SLUG},
    "izibank": "Izibank",
    "globus": "Глобус",
    "kredobank": "KredoBank",
    "taskombank": "Таскомбанк",
    "bvr": "БВР",
    "vostok": "Банк Восток",
    "credit-dnipro": "Кредит Дніпро",
    "grant": "Грант",
    "lviv": "Банк Львів",
    "procredit": "ProCredit",
    "rada": "РадаБанк",
    "cominbank": "Комінбанк",
    "ukrsibbank": "Укрсиббанк",
    "unex": "Unex",
    "pivdenny": "Південний",
    "credit-agricole": "CreditAgricole",
}

# Банки, доступні для вибору при додаванні картки й налаштуванні лімітів.
# Порядок — за tier довідника: спершу ті, з якими працювати безпечніше.
# Додати банк у BANK_PROFILES достатньо, щоб він тут з'явився.
CARD_BANK_SLUGS: list[str] = [
    slug for slug, _ in sorted(
        ((s, p) for s, p in BANK_PROFILES.items() if p.safe_monthly_uah),
        key=lambda kv: (kv[1].tier, -(kv[1].safe_monthly_uah or 0)),
    )
]


def bank_display_name(bank: str) -> str:
    """Слаг/код/назва → людська назва для кнопок і алертів."""
    slug = normalize_bank(bank)
    if slug in BANK_SLUG_NAMES:
        return BANK_SLUG_NAMES[slug]
    if is_unmapped_code(slug):
        # Не вигадуємо назву коду, якого не знаємо: «Банк 545» виглядав би
        # як справжній банк і ховав би те, що реєстр неповний.
        return f"код {slug}"
    return slug.capitalize()


# Способи оплати, які взагалі не є банком.
#
# «Bank Transfer», «Банковский перевод», «Global Transfer» — це переказ на
# рахунок, і приймається він з БУДЬ-ЯКОГО банку. Система ж бачила в них
# черговий невідомий «банк», якого в користувача немає, і чесно писала
# «немає твоєї картки» на ордерах, які насправді підходять усім.
_ANY_BANK_TOKENS = frozenset({
    "transfer",
    "bank transfer",
    "banktransfer",
    "global transfer",
    "globalbanktransfer",
    "банковский перевод",
    "банківський переказ",
    "переказ на рахунок",
    "bank",
})


def is_any_bank(bank: str) -> bool:
    """
    Чи це «переказ звідки завгодно», а не конкретний банк.

    Такий метод не звужує вибір карток, а знімає обмеження: підходить будь-яка.
    """
    value = str(bank or "").strip().lower()
    if not value:
        return False
    return value in _ANY_BANK_TOKENS or normalize_bank(value) in _ANY_BANK_TOKENS


def is_unmapped_code(bank: str) -> bool:
    """
    Чи це числовий код біржі, якого немає в реєстрі.

    Такий код проходить `normalize_bank` наскрізь і далі поводиться як
    окремий «банк»: під нього не знайдеться жодної картки, ордер відпаде, і
    в статистиці причин з'явиться рядок «немає активних карток — 545».
    Виглядає як факт про картки, а насправді це прогалина в `_CODE_TO_SLUG`.
    """
    slug = normalize_bank(bank)
    return bool(slug) and slug.isdigit() and slug not in _CODE_TO_SLUG


def bank_view_list(codes) -> list[dict]:
    """
    Банки ордера у вигляді, придатному для інтерфейсу.

    Біржі віддають банки числами, і одному банку відповідає кілька кодів:
    Monobank — і «43», і «1»; А-Банк — і «48», і «61». Реєстр `/banks`
    знає лише канонічні, тож дашборд, маючи саму лише мапу код→назва,
    показував «код 1» і «код 61» як невідомі банки — і людина йшла шукати
    помилку у своїх картках.

    Мапа тут одна на весь проєкт, і клієнту віддається вже результат:
    друга копія на фронтенді розійшлася б із цією за перший же новий код.

    `known=false` означає, що коду немає в реєстрі бота: під нього картка
    не підбереться, скільки б їх не завести.
    """
    result: list[dict] = []
    seen: set[str] = set()

    for raw in (codes or []):
        code = str(raw).strip()
        if not code:
            continue
        slug = normalize_bank(code)
        if slug in seen:
            continue
        seen.add(slug)
        any_bank = is_any_bank(code)
        result.append({
            "code": code,
            "slug": slug,
            # «Переказ» — не банк, і показувати його як невідомий код
            # означало б натякати на прогалину в реєстрі там, де її немає.
            "name": "Переказ з будь-якого банку" if any_bank else bank_display_name(code),
            "known": any_bank or not is_unmapped_code(code),
            "anyBank": any_bank,
        })
    return result


def license_group_of(bank: str) -> str:
    """
    Група спільної ліцензії або '' — тоді банк сам по собі.

    Спільна тут не лише стеля лімітів, а й фінансовий моніторинг: блок на
    одному банку групи тягне другий.
    """
    return get_bank_profile(bank).license_group