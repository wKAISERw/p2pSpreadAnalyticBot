"""
merchant_profile.py — Генерація URL профілів мерчантів на біржах.

Використовується для клікабельних посилань у Telegram-алертах.
"""


def build_profile_url(exchange: str, merchant_id: str, merchant_name: str = "") -> str:
    """
    Повертає URL профілю мерчанта на відповідній біржі.
    
    :param exchange: Назва біржі (Bybit, Binance, OKX, MEXC, Wallet)
    :param merchant_id: ID мерчанта на біржі
    :param merchant_name: Ім'я мерчанта (для OKX, де URL за нікнеймом)
    :returns: URL профілю або пустий рядок
    """
    _URLS = {
        "Bybit":   "https://www.bybit.com/fiat/trade/otc/profile/{id}/USDT/UAH/item",
        "Binance": "https://p2p.binance.com/en/advertiserDetail?id={id}",
        "OKX":     "https://www.okx.com/p2p/ads-merchant?publicUserId={id}",
        "MEXC":    "https://www.mexc.com/otc/user/{id}",
    }

    template = _URLS.get(exchange)
    if not template:
        return ""
    return template.format(id=merchant_id, name=merchant_name)


def format_merchant_link(exchange: str, merchant_id: str, merchant_name: str = "") -> str:
    """
    Повертає Markdown-посилання для Telegram.
    Приклад: [CryptoKing](https://www.bybit.com/fiat/trade/otc/profile/12345/...)
    """
    url = build_profile_url(exchange, merchant_id, merchant_name)
    display = merchant_name or merchant_id[:12]
    if url:
        return f"[{display}]({url})"
    return display
