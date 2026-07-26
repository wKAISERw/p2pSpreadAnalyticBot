"""
deeplinks.py — єдине джерело правди для мобільних диплінків P2P-бірж.

Усі шаблони нижче підтверджені прогоном `adb shell am start -W` на реальному
пристрої (див. tools/deeplink/FINDINGS.md і results_*.txt). Не міняти "на око":
кожен маршрут відповідає intent-filter'у в AndroidManifest відповідного APK.

Ключове обмеження Telegram
--------------------------
InlineKeyboardButton(url=...) приймає лише http/https/tg. Custom scheme
(`bnc://`, `okx://`, `bybitapp://`) у кнопку покласти НЕ можна — Telegram
відхилить повідомлення. Тому:

  * Binance -> https://app.binance.com/...        кнопка працює напряму
  * Bybit   -> https://app.bybit.com/inapp/...    кнопка працює напряму
  * OKX     -> тільки okx://...                   потрібна проміжна сторінка

Для OKX https-шляхи P2P не зареєстровані ні в манифесті, ні в iOS AASA
(покриті лише /ul/*, /download, /campaigns/*, /learn/*, /web3/detail/*,
/walletappconnect/*, /copy-trading/*), а /ul/<код> — серверний шортлінк, який
неможливо зібрати самому. Тому саме і тільки для OKX лишається redirect.html.
"""

from __future__ import annotations

import logging
from base64 import b64encode
from typing import Literal, Optional
from urllib.parse import quote, urlencode

_log = logging.getLogger(__name__)

# Проміжна сторінка потрібна лише там, де немає робочого https-маршруту.
REDIRECT_BASE = "https://wkaiserw.github.io/p2pSpreadAnalyticBot/redirect.html"

Kind = Literal["ad", "order", "profile"]


# ─────────────────────────── сирі схеми (не для TG-кнопок) ───────────────────

def app_scheme_url(exchange: str, kind: Kind, entity_id: str) -> str:
    """
    Custom-scheme диплінк. Відкриває застосунок незалежно від того, чи
    увімкнена в системі верифікація App Links. Придатний для redirect.html,
    Android Intent URL, iOS, QR — але НЕ для InlineKeyboardButton(url=...).
    """
    if not entity_id:
        return ""
    eid = quote(str(entity_id), safe="")

    if exchange == "Binance":
        # Порожньо навмисно. Binance фільтрує зовнішні диплінки серверним білим
        # списком (externalDeeplinkAllows, конфіг nezha). Прогін на пристрої
        # показав NoSupportRouterPathActivity на ВСІХ перевірених шляхах,
        # включно з /fiat/orderDetails, який раніше вважався робочим.
        # Єдиний санкціонований вхід — короткий share-лінк dplk, див.
        # binance_share.py. Не додавати сюди маршрути «за списком 177»:
        # присутність шляху в застосунку не означає дозвіл ззовні.
        return ""

    if exchange == "OKX":
        # окремого маршруту на оголошення немає — див. resolve_target()
        return {
            "order":   f"okx://exchange/p2p/order?id={eid}",
            "profile": f"okx://exchange/p2p/profile?userId={eid}",
        }.get(kind, "")

    if exchange == "Bybit":
        # Bybit на Flutter: справжній формат — bybitapp://open/<шлях>, а не
        # ?page=. Внутрішні маршрути mini-app дістали з libapp.so:
        # by-mini://p2p/home, /p2p/order/detail, /p2p/user/home.
        return {
            "order":   f"bybitapp://open/p2p/order/detail?orderId={eid}",
            "profile": f"bybitapp://open/p2p/user/home?userId={eid}",
            "ad":      "",
        }.get(kind, "")

    return ""


def binance_webview_url(web_url: str) -> str:
    """
    Універсальний шлюз Binance: відкриває будь-яку веб-сторінку у власному
    вже авторизованому webview застосунку, а не в браузері.
    """
    if not web_url:
        return ""
    token = quote(b64encode(web_url.encode()).decode(), safe="")
    return f"https://app.binance.com/webview/webview?type=default&url={token}"


# Мініаппа P2P всередині застосунку Binance. Знайдена в dex: маршрут `/mp/web`
# входить до списку 177 підтверджених шляхів диспетчера, тобто це нативний
# перехід, який НЕ потребує ані dplk, ані входу в особистий акаунт.
BINANCE_P2P_MINIAPP = "Bzp9defeaRgNqhgV4wEG5C"

BINANCE_MINIAPP_PAGES = {
    "profile": "pages/merchant-detail/index",
    "ad":      "pages/ads/index",
    "order":   "pages/order-detail/index",
}


def binance_miniapp_url(page: str, query: str = "", app_id: str = BINANCE_P2P_MINIAPP) -> str:
    """
    Лінк на сторінку мініаппи Binance.

    Формат узятий з dex дослівно: і шлях сторінки, і рядок запиту передаються
    окремими параметрами в base64.

        https://app.binance.com/mp/web
            ?appId=<id>
            &startPagePath=<base64 "pages/merchant-detail/index">
            &startPageQuery=<base64 "advertiserNo=…&fromNative=true">

    Приклад з dex: `startPageQuery=ZnJvbU5hdGl2ZT10cnVl` = `fromNative=true`.
    """
    if not page:
        return ""
    url = (f"https://app.binance.com/mp/web?appId={quote(app_id, safe='')}"
           f"&startPagePath={quote(b64encode(page.encode()).decode(), safe='')}")
    if query:
        url += f"&startPageQuery={quote(b64encode(query.encode()).decode(), safe='')}"
    return url


def binance_share_url(dplk_url: str) -> str:
    """
    Короткий share-лінк Binance (`https://www.binance.com/<lang>/qr/dplk<hash>`),
    придатний для Telegram.

    Сам dplk генерується на сервері — рядка `dplk` у dex немає, вгадати код
    неможливо. Отримати його можна лише через приватний ендпоінт
    `/bapi/c2c/v1/private/c2c/share/adv-share` (див. probe_binance_share.py),
    для якого у бота вже є авторизована сесія.

    Хост www.binance.com у манифесті не зареєстрований, тому голий dplk
    відкриє браузер. Обгортаємо у webview-шлюз.
    """
    if not dplk_url:
        return ""
    return binance_webview_url(dplk_url)


def okx_merchant_url(share_code: str) -> str:
    """
    Нативна картка продавця OKX за shareCode.

    Підтверджено на пристрої:
    `okx://exchange/merchanthome.com?shareCode=AysnnUZlACimN`
    → `com.okinc.p2p.userinfo.profile.UserProfilePageActivity`.

    Код видає `GET /v3/c2c/merchant/share?pubUserId=…`. У відповіді є ще
    `qrCode` виду `https://okx.com/ua/p2p?action=otcTransfer&shareCode=…` —
    його брати НЕ треба: шлях `/ua/p2p` у манифесті не зареєстрований, тому
    таке посилання піде в браузер.
    """
    if not share_code:
        return ""
    return f"okx://exchange/merchanthome.com?shareCode={quote(str(share_code), safe='')}"


def okx_universal_link(code_or_url: str) -> str:
    """
    Короткий універсальний лінк OKX (`https://www.okx.com/ul/<код>`).

    Це єдиний P2P-придатний https-шлях, зареєстрований у манифесті OKX
    (`/ul/.*`), тому його — на відміну від `okx://` — можна класти прямо в
    InlineKeyboardButton без проміжної сторінки.

    Код генерується сервером; отримати його можна через `/v3/c2c/merchant/share`
    (див. probe_okx_share.py). Приймає як голий код, так і готовий URL.
    """
    if not code_or_url:
        return ""
    s = str(code_or_url).strip()
    if s.startswith("http"):
        return s if "/ul/" in s else ""
    return f"https://www.okx.com/ul/{quote(s, safe='')}"


def okx_merchant_scheme_url(share_code: str) -> str:
    """
    Профіль мерчанта OKX через shareCode.

    Маршрут `exchange/merchanthome.com` витягнутий з dex застосунку. Сканер уже
    збирає цей код у полі Order.share_code, тобто нічого додатково тягнути з
    біржі не треба.

    Увага: публічний лінк виду https://okx.com/otc/transfer?shareCode=... — це
    НЕ те саме. Він 302-редіректить на www.okx.com/en-us/p2p?shareCode=, тобто
    на загальну вітрину P2P з реферальною міткою, а не на конкретного мерчанта.
    """
    if not share_code:
        return ""
    return f"okx://exchange/merchanthome.com?shareCode={quote(str(share_code), safe='')}"


# ─────────────────────────── https, придатні для TG-кнопок ───────────────────

def app_https_url(exchange: str, kind: Kind, entity_id: str) -> str:
    """
    https-URL, зареєстрований у манифесті застосунку (autoVerify).
    На телефоні зі встановленим застосунком відкриється саме він; без
    застосунку — звичайна веб-сторінка. Порожній рядок = робочого https
    маршруту для цієї біржі/екрана не існує.
    """
    if not entity_id:
        return ""
    eid = quote(str(entity_id), safe="")

    if exchange == "Binance":
        # Те саме: жоден https-шлях на app.binance.com не проходить серверний
        # білий список для зовнішніх переходів. Лишається dplk або веб.
        return ""

    if exchange == "Bybit":
        # покритий лише префікс /inapp на app.bybit.com (+ дзеркала)
        return {
            "order": f"https://app.bybit.com/inapp/p2p/order/{eid}",
        }.get(kind, "")

    # OKX: жоден P2P https-шлях не зареєстрований
    return ""


def _redirect_url(exchange: str, kind: Kind, entity_id: str, side: str = "",
                  share_code: str = "", dplk: str = "") -> str:
    params = {"ex": exchange, "kind": kind, "id": str(entity_id)}
    if side:
        params["side"] = side
    if share_code:
        # Пріоритетний параметр для OKX: сторінка збудує з нього
        # okx://exchange/merchanthome.com?shareCode=… — нативну картку продавця.
        params["code"] = share_code
    if dplk:
        # Пріоритетний параметр для Binance: сторінка викличе Intent com.binance.dev
        params["dplk"] = dplk
    return f"{REDIRECT_BASE}?{urlencode(params)}"


# ─────────────────────────────── публічний API ───────────────────────────────

def tg_button_url(
    exchange: str,
    kind: Kind,
    entity_id: str,
    side: str = "",
    web_fallback: str = "",
) -> str:
    """
    URL, який можна безпечно покласти в InlineKeyboardButton(url=...).

    Порядок вибору:
      1. прямий https-диплінк, якщо біржа його реєструє (Binance, Bybit);
      2. проміжна сторінка, якщо є робоча custom scheme, але немає https (OKX);
      3. звичайне веб-посилання як останній варіант.
    """
    direct = app_https_url(exchange, kind, entity_id)
    if direct:
        return direct

    # Binance: маршруту в роутері немає для зовнішніх інтентів,
    # тому використовуємо прямий share-dplk або канонічний веб-лінк.
    if exchange == "Binance":
        web = web_fallback or binance_web_url(kind, entity_id)
        if web:
            return web

    if app_scheme_url(exchange, kind, entity_id):
        return _redirect_url(exchange, kind, entity_id, side)

    return web_fallback


async def tg_button_url_async(
    exchange: str,
    kind: Kind,
    entity_id: str,
    side: str = "",
    web_fallback: str = "",
    db=None,
    user_id: int = 0,
) -> str:
    """
    Те саме, що tg_button_url, але для Binance додатково пробує дістати
    короткий dplk-лінк через приватний ендпоінт — це єдиний спосіб потрапити
    на нативний екран оголошення.
    """
    if exchange == "Binance" and kind in ("ad", "profile", "order"):
        if db is None:
            _log.warning("deeplinks: Binance %s — db не передано, dplk неможливий, "
                         "кнопка піде у веб", kind)
        else:
            try:
                from bot.binance_share import get_share_link
                dplk = await get_share_link(kind, entity_id, db, user_id)
                if dplk:
                    return _redirect_url("Binance", kind, entity_id, side=side, dplk=dplk)
                # Порожньо без винятку — причину вже залогував binance_share.
                # Якщо в логах поруч нічого немає, значить спрацював кеш або
                # запобіжник, або kind не з тих, що вміє share.
                _log.warning("deeplinks: Binance %s (%s) — dplk порожній, "
                             "кнопка піде у веб", kind, entity_id)
            except Exception as e:
                # Тут раніше стояв голий pass, і через нього причина зникала
                # безслідно. Саме так ми втратили день на діагностику.
                _log.warning("deeplinks: Binance %s — dplk впав: %s: %s",
                             kind, type(e).__name__, e, exc_info=True)
        web = web_fallback or binance_web_url(kind, entity_id)
        if web:
            return web

    # OKX ПРОФІЛЬ (лише!): shareCode -> okx://exchange/merchanthome.com?shareCode=…
    # відкриває нативну картку продавця (UserProfilePageActivity, перевірено на
    # пристрої). Схему не можна класти в кнопку Telegram, тому веземо код через
    # redirect.html.
    #
    # Для ОРДЕРА shareCode НЕ використовуємо: merchanthome — це профіль, а не
    # ордер. Ордер OKX має власний прямий маршрут okx://exchange/p2p/order?id=,
    # який redirect.html будує сам. Якби ми сюди пустили order — кнопка ордера
    # відкривала б профіль продавця.
    if exchange == "OKX" and kind == "profile" and db is not None:
        try:
            from bot.okx_share import get_share_code
            code = await get_share_code("profile", entity_id, db, user_id)
            if code:
                return _redirect_url(exchange, kind, entity_id, side, share_code=code)
        except Exception as e:
            _log.warning("deeplinks: OKX profile — shareCode впав: %s: %s",
                         type(e).__name__, e, exc_info=True)

    return tg_button_url(exchange, kind, entity_id, side=side, web_fallback=web_fallback)


def binance_web_url(kind: Kind, entity_id: str) -> str:
    """Звичайна веб-сторінка Binance — вміст для webview-шлюзу."""
    if not entity_id:
        return ""
    eid = quote(str(entity_id), safe="")
    return {
        "ad":      f"https://p2p.binance.com/en/trade/detail/{eid}",
        "profile": f"https://p2p.binance.com/en/advertiserDetail?advertiserNo={eid}",
        "order":   f"https://p2p.binance.com/en/orderDetail?orderNo={eid}",
    }.get(kind, "")


# Префікси, які сканер додає до id оголошення для власних потреб.
_ID_PREFIXES: dict[str, tuple[str, ...]] = {
    "Binance": ("bn_",),
}


def strip_exchange_prefix(exchange: str, entity_id: str) -> str:
    """
    Прибирає внутрішній префікс сканера з id оголошення.

    `exchanges/binance.py` зберігає `id=f"bn_{advNo}"` — префікс потрібен
    самому сканеру, але біржа його не знає. Якщо відправити `bn_129082…`
    у `adv-share`, Binance відповість:

        code=083626 msg=Оголошення не існує

    Саме це й ламало dplk: сесія була жива, csrf правильний, а id — чужий.
    """
    if not entity_id:
        return ""
    for pref in _ID_PREFIXES.get(exchange, ()):
        if entity_id.startswith(pref):
            return entity_id[len(pref):]
    return entity_id


def resolve_target(order) -> tuple[Kind, str]:
    """
    Обирає найточніший екран для конкретного ордера сканера.

    Оголошення краще за профіль (менше кліків до потрібної ціни), але маршрут
    на оголошення підтверджений лише в Binance. Для решти — профіль мерчанта.
    Поле з id оголошення в моделі Order зветься `id`.
    """
    exchange = getattr(order, "exchange", "")
    ad_id = strip_exchange_prefix(exchange, str(getattr(order, "id", "") or ""))
    merchant_id = str(getattr(order, "merchant_id", "") or "")

    # Binance веде на оголошення через webview-шлюз (нативного маршруту немає),
    # решта бірж — лише на профіль мерчанта.
    if ad_id and (app_https_url(exchange, "ad", ad_id) or exchange == "Binance"):
        return "ad", ad_id
    if merchant_id:
        return "profile", merchant_id
    return "profile", ""


def tg_text_link(exchange: str, kind: Kind, entity_id: str, label: str = "") -> str:
    """
    HTML-посилання для тіла повідомлення. На відміну від кнопки, лишається
    в історії чату — зручно, щоб повернутись до ордера пізніше.
    """
    url = tg_button_url(exchange, kind, entity_id)
    if not url:
        return ""
    return f'<a href="{url}">{label or url}</a>'


def android_intent_url(exchange: str, kind: Kind, entity_id: str,
                       web_fallback: str = "") -> str:
    """
    Android Intent URL: відкриває застосунок, а якщо його немає — веб-версію.
    Використовується всередині redirect.html, не в Telegram.
    """
    scheme_url = app_scheme_url(exchange, kind, entity_id)
    if not scheme_url:
        return ""
    scheme, _, rest = scheme_url.partition("://")
    pkg = {
        "Binance": "com.binance.dev",
        "OKX": "com.okinc.okex.gp",
        "Bybit": "com.bybit.app",
    }.get(exchange, "")
    if not pkg:
        return ""
    intent = f"intent://{rest}#Intent;scheme={scheme};package={pkg};"
    if web_fallback:
        intent += f"S.browser_fallback_url={quote(web_fallback, safe='')};"
    return intent + "end"


def supports_app_link(exchange: str, kind: Kind = "order") -> bool:
    """Чи існує взагалі підтверджений маршрут у застосунок."""
    return bool(app_https_url(exchange, kind, "x") or app_scheme_url(exchange, kind, "x"))
