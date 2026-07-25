"""
merchant_profile.py - Генерація URL профілів мерчантів на біржах.
Використовується для клікабельних посилань у Telegram-алертах.
"""


def build_profile_url(
    exchange: str,
    merchant_id: str,
    merchant_name: str = "",
    side: str = "",
    profile_mode: str = "chat",
    offer_id: str = "",
) -> str:
    """
    Повертає URL профілю/оголошень мерчанта на відповідній біржі.

    Binance   - публічний профіль мерчанта (advertiserNo).
    Bybit     - сторінка оголошень мерчанта USDT/UAH.
    OKX       - оголошення мерчанта (publicUserId).
    MEXC      - профіль мерчанта.
    CryptoBot - chat: профіль у чаті (@CryptoBot?start=u-{id})
                webapp: конкретне оголошення в Mini App (startapp=offer-{offerID})
    """
    if exchange == "CryptoBot":
        if profile_mode == "webapp" and offer_id:
            # Відкриває конкретне оголошення в Mini App (@send)
            return f"https://t.me/send?startapp=offer-{offer_id}"
        elif profile_mode == "webapp":
            # Fallback: відкрити P2P-розділ (якщо немає ID ордера)
            return "https://t.me/send?startapp=p2p"
        else:
            # Чат-режим: профіль мерча через @CryptoBot
            return f"https://t.me/CryptoBot?start=u-{merchant_id}"

    if exchange == "Wallet":
        if offer_id and merchant_id:
            # Пряме посилання на конкретне оголошення Wallet Mini App, яке знайшов сканер
            return f"https://t.me/wallet?startapp=offerid_{offer_id}_{merchant_id}"
        return "https://t.me/wallet?startapp=market"

    _URLS = {
        "Binance": "https://p2p.binance.com/en/advertiserDetail?advertiserNo={id}",
        "Bybit":   "https://www.bybit.com/uk-UA/p2p/profile/{id}/USDT/UAH/item",
        "OKX":     "https://www.okx.com/p2p/ads-merchant?publicUserId={id}",
        "MEXC":    "https://www.mexc.com/uk-UA/buy-crypto/merchant?id={id}",
        "BingX":   "https://bingx.com/p2p?uid={id}",
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
    Використовується після execute_taker_order -> для прямого переходу.
    """
    _ORDER_URLS = {
        "Binance": "https://p2p.binance.com/en/orderDetail?orderNo={id}",
        "Bybit":   "https://www.bybit.com/fiat/trade/otc/order-detail?orderId={id}",
        "OKX":     "https://www.okx.com/p2p/order/{id}",
        "MEXC":    "https://www.mexc.com/uk-UA/buy-crypto/order/{id}",
        "BingX":   "https://bingx.com/p2p",
    }
    template = _ORDER_URLS.get(exchange)
    if not template or not order_id:
        return ""
    return template.format(id=order_id)


# ─────────────────────────────────────────────────────────────────────────────
# Мобільні диплінки.
#
# Раніше тут лежали вгадані схеми (binance://app/advertiserDetail,
# okx://app/p2p, пакет com.binance.merchant). Жодна з них не існує в реальних
# застосунках — саме тому «відкрити в App» вело на головну або в браузер.
# Підтверджені маршрути тепер в одному місці: bot/deeplinks.py.
# ─────────────────────────────────────────────────────────────────────────────

def build_app_profile_url(exchange: str, merchant_id: str, merchant_name: str = "") -> str:
    """Custom-scheme диплінк на профіль мерчанта. Не для InlineKeyboardButton."""
    from bot.deeplinks import app_scheme_url
    return app_scheme_url(exchange, "profile", merchant_id)


def build_app_order_url(exchange: str, order_id: str) -> str:
    """Custom-scheme диплінк на ордер. Не для InlineKeyboardButton."""
    from bot.deeplinks import app_scheme_url
    return app_scheme_url(exchange, "order", order_id)


def build_android_intent_profile_url(exchange: str, merchant_id: str, merchant_name: str = "", side: str = "") -> str:
    """Intent URL: застосунок, з fallback у браузер якщо його немає."""
    from bot.deeplinks import android_intent_url
    web_url = build_profile_url(exchange, merchant_id, merchant_name, side=side)
    return android_intent_url(exchange, "profile", merchant_id, web_url) or web_url


def build_android_intent_order_url(exchange: str, order_id: str) -> str:
    """Intent URL на ордер, з fallback у браузер."""
    from bot.deeplinks import android_intent_url
    web_url = build_order_url(exchange, order_id)
    return android_intent_url(exchange, "order", order_id, web_url) or web_url


def format_merchant_link(exchange: str, merchant_id: str, merchant_name: str = "") -> str:
    """Повертає Markdown-посилання для Telegram."""
    url = build_profile_url(exchange, merchant_id, merchant_name)
    display = merchant_name or merchant_id[:12]
    if url:
        return f"[{display}]({url})"
    return display
