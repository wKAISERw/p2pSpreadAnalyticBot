"""
Канонічні назви бірж.

Навіщо окремий модуль: назва біржі — це ключ. Вона лежить у
`user_credentials.exchange`, в `auth_sessions`, у полях `taker_sell_exchange`
і в `ExchangeManager`. Колонка оголошена як `TEXT` без `COLLATE NOCASE`
(core/storage/base_db.py), тож SQLite порівнює її побайтово: рядок,
записаний як "Okx", ніколи не знайдеться за запитом "OKX".

Саме на цьому ламалось підключення бірж із веб-дашборду. HTTP-ендпоінт
робив `exchange.capitalize()`, і те, що фронтенд слав як "okx", лягало в
базу як "Okx" — бот же все життя писав "OKX". Ключі зберігались успішно,
дашборд показував "Connected", а сканер їх не бачив. Те саме з "mexc" →
"Mexc" і "bingx" → "Bingx". Binance і Bybit випадково вціліли лише тому,
що для них capitalize() дає правильний результат.

Тому назву більше ніде не «нормалізують на око»: є список канонічних
написань і функція, яка зводить до нього будь-який ввід.
"""
from __future__ import annotations

# Порядок має значення: у такому вигляді біржі показуються в меню /connect.
CANONICAL_EXCHANGES: tuple[str, ...] = (
    "Binance",
    "Bybit",
    "OKX",
    "MEXC",
    "Wallet",
    "BingX",
)

# Написання, які приходять ззовні і не збігаються з канонічним у нижньому
# регістрі. "Telegram Wallet" — те, як біржа підписана на фронтенді.
_ALIASES: dict[str, str] = {
    "telegram wallet": "Wallet",
    "telegram_wallet": "Wallet",
    "tg wallet": "Wallet",
    "tgwallet": "Wallet",
}

_BY_LOWER: dict[str, str] = {name.lower(): name for name in CANONICAL_EXCHANGES}


def canonical_exchange(raw: str | None) -> str | None:
    """
    Зводить довільне написання до канонічного або повертає None.

    None — це «такої біржі в системі немає», і викликач має відповісти
    помилкою, а не вигадувати назву. Мовчазний `.capitalize()` саме тому й
    був небезпечний: він завжди повертав щось, що виглядало правдоподібно.
    """
    if not raw:
        return None
    key = str(raw).strip().lower()
    return _BY_LOWER.get(key) or _ALIASES.get(key)


def is_known_exchange(raw: str | None) -> bool:
    return canonical_exchange(raw) is not None
