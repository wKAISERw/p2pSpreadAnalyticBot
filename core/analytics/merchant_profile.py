"""
merchant_profile.py — Генерація URL профілів мерчантів на біржах.
Використовується для клікабельних посилань у Telegram-алертах.
"""


def build_profile_url(exchange: str, merchant_id: str, merchant_name: str = "", side: str = "") -> str:
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
        "OKX":     "https://www.okx.com/ua/p2p/ads-merchant?publicUserId={id}&fiatCurrency=UAH&fiat=UAH&currency=UAH&cryptoCurrency=USDT&crypto=USDT&token=USDT&ccy=USDT",
        "MEXC":    "https://www.mexc.com/uk-UA/buy-crypto/merchant?id={id}",
    }
    template = _URLS.get(exchange)
    if not template:
        return ""
    if exchange == "Bybit":
        merchant_id = str(merchant_id).strip()
        if merchant_id and not merchant_id.startswith("s"):
            merchant_id = f"s{merchant_id}"
    url = template.format(id=merchant_id, name=merchant_name)
    if exchange == "OKX" and side:
        url += f"&side={side}"
    return url


def build_order_url(exchange: str, order_id: str) -> str:
    """
    Повертає прямий URL на відкритий P2P ордер (trade/order).
    Використовується після execute_taker_order → для прямого переходу.
    """
    _ORDER_URLS = {
        "Binance": "https://p2p.binance.com/en/orderDetail?orderNo={id}",
        "Bybit":   "https://www.bybit.com/fiat/trade/otc/order-detail?orderId={id}",
        "OKX":     "https://www.okx.com/p2p/order/{id}",
        "MEXC":    "https://www.mexc.com/uk-UA/buy-crypto/order/{id}",
    }
    template = _ORDER_URLS.get(exchange)
    if not template or not order_id:
        return ""
    return template.format(id=order_id)


def build_app_profile_url(exchange: str, merchant_id: str, merchant_name: str = "") -> str:
    """
    Повертає URL-схему (deep link) для мобільного додатку, що дозволяє відкрити
    профіль мерчанта безпосередньо в додатку (без логіну в браузері).
    """
    _APP_URLS = {
        "Binance": "binance://app/advertiserDetail?advertiserNo={id}",
        "Bybit":   "bybit://app/p2p",  # Bybit не має прямого лінку на профіль мерчанта, відкриваємо P2P
        "OKX":     "okx://app/p2p",
        "MEXC":    "mexc://app/p2p",
    }
    template = _APP_URLS.get(exchange)
    if not template or not merchant_id:
        return ""
    return template.format(id=merchant_id, name=merchant_name)


def build_app_order_url(exchange: str, order_id: str) -> str:
    """
    Повертає URL-схему (deep link) для мобільного додатку для прямого переходу до ордера.
    """
    _APP_ORDER_URLS = {
        "Binance": "binance://app/orderDetail?orderNo={id}",
        "Bybit":   "bybit://app/p2p",
        "OKX":     "okx://app/p2p",
        "MEXC":    "mexc://app/p2p",
    }
    template = _APP_ORDER_URLS.get(exchange)
    if not template or not order_id:
        return ""
    return template.format(id=order_id)


def format_merchant_link(exchange: str, merchant_id: str, merchant_name: str = "") -> str:
    """Повертає Markdown-посилання для Telegram."""
    url = build_profile_url(exchange, merchant_id, merchant_name)
    display = merchant_name or merchant_id[:12]
    if url:
        return f"[{display}]({url})"
    return display
