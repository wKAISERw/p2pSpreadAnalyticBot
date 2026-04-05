"""
merchant_profile.py — Генерація URL профілів мерчантів на біржах.
Використовується для клікабельних посилань у Telegram-алертах.
"""


def build_profile_url(exchange: str, merchant_id: str, merchant_name: str = "") -> str:
    """
    Повертає URL профілю/оголошень мерчанта на відповідній біржі.

    Binance — публічний профіль мерчанта (advertiserNo).
    Bybit   — сторінка оголошень мерчанта USDT/UAH.
    OKX     — немає публічних P2P профілів.
    MEXC    — немає публічних P2P профілів.
    """
    _URLS = {
        "Binance": "https://c2c.binance.com/uk-UA/advertiserDetail?advertiserNo={id}",
        "Bybit":   "https://www.bybit.com/uk-UA/p2p/profile/{id}/USDT/UAH/item",
        "OKX":     "https://www.okx.com/p2p/ads-merchant?publicUserId={id}",
        "MEXC":    "https://www.mexc.com/uk-UA/buy-crypto/merchant?id={id}",
    }
    template = _URLS.get(exchange)
    if not template:
        return ""
    return template.format(id=merchant_id, name=merchant_name)


def format_merchant_link(exchange: str, merchant_id: str, merchant_name: str = "") -> str:
    """Повертає Markdown-посилання для Telegram."""
    url = build_profile_url(exchange, merchant_id, merchant_name)
    display = merchant_name or merchant_id[:12]
    if url:
        return f"[{display}]({url})"
    return display
