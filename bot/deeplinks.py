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
        # Ордер має власний нативний маршрут.
        #
        # Профіль: `okx://exchange/p2p/profile?userId=` НЕ використовуємо — він
        # ігнорує переданий id і відкриває профіль залогіненого користувача.
        # Раніше через це профіль без shareCode падав у браузер, тобто кнопка
        # залежала від живої сесії бота. Тепер є okx_profile_scheme_url() —
        # webview-шлюз застосунку, який сесії не потребує взагалі.
        return {
            "order":   f"okx://exchange/p2p/order?id={eid}",
            "profile": okx_profile_scheme_url(entity_id),
        }.get(kind, "")

    if exchange == "Bybit":
        # Маршрутів `open/p2p/...` у роутері НЕМАЄ. Це не здогад: у libapp.so
        # апки, яка стоїть на пристрої, лежить таблиця з 92 декларованих
        # маршрутів у нотації `://шлях{param?,param?}`, і жодного `open/p2p`
        # серед них. Тому попередній перебір імені параметра
        # (userId/targetUserId/makerUserId/accountId) не мав шансу в принципі —
        # ми підбирали ключ до форми, якої роутер не розбирає.
        #
        # Ордер: `open/fiat_otc_order_detail{orderId?,sourcePage?}` — рівно як
        # у таблиці. З несправжнім id відкриває головну, тож на реальному
        # ордері ще не підтверджено (справжнього id під рукою не було).
        #
        # Профіль: нативного маршруту на картку контрагента не існує взагалі.
        # `by-mini://p2p/user/home` через `open/route?targetUrl=` відкривається,
        # але показує профіль ЗАЛОГІНЕНОГО користувача — та сама пастка, що і
        # `okx://exchange/p2p/profile?userId=`. Робочий шлях один: універсальний
        # webview-шлюз хоста, див. bybit_profile_scheme_url().
        return {
            "order":   f"bybitapp://open/fiat_otc_order_detail?orderId={eid}&sourcePage=deeplink",
            "profile": bybit_profile_scheme_url(entity_id),
            "ad":      "",
        }.get(kind, "")

    return ""


def binance_universal_deeplink(app_path: str) -> str:
    """
    https-обгортка Binance, яка везе в собі внутрішній шлях застосунку.

        https://app.binance.com/en/download?_dp=<urlencoded base64 шляху>

    Це те, що знімає багатомісячну залежність від dplk. Логіка та сама, що в
    OKX (`/download?…&deeplink=`), і знайдена так само — від протилежного:
    у самої біржі є verified-домен, і посилання з нього застосунок обробляє
    сам, БЕЗ перевірки серверного білого списку зовнішніх диплінків.

    Саме тому тут працює `/p2p/advertiserProfile`, який роками лежав у списку
    «мертвих назавжди»: як голий зовнішній `bnc://`-інтент він дає
    NoSupportRouterPathActivity, а через `_dp` на app.binance.com —
    відкриває нативну картку.

    Підтверджено на пристрої (Binance 3.17.1, SM-S918B):
      _dp=b64("/p2p/advertiserProfile?advertiserNo=s7b2138…")
        -> com.binance.c2c.merchant.FiatMerchantDetailsActivity
        (нік, рівень мерчанта, депозит, транзакції за 30 днів, % завершення,
         кількість контрагентів, вкладка «Оголошення»)

    Ліміт: `/fiat/ads/detail` сюди НЕ годиться — це екран керування ВЛАСНИМИ
    оголошеннями (`c2c.advertisement.manager`, з іконкою видалення), і для
    чужого оголошення він порожній. Тому Binance ведемо на профіль.
    """
    if not app_path:
        return ""
    token = quote(b64encode(app_path.encode()).decode(), safe="")
    return f"https://app.binance.com/en/download?_dp={token}"


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


def bybit_web_url(kind: Kind, entity_id: str) -> str:
    """
    Звичайна веб-сторінка Bybit. Використовується і як вміст для webview-шлюзу
    застосунку, і як фолбек у браузер.

    Мерчант у Bybit ідентифікується `userMaskId` — саме він у Order.merchant_id.
    Числового id у нас немає й не буде: публічний ендпоінт
    `api2.bybit.com/fiat/otc/item/online` віддає `userId="0"` і `accountId="0"`
    для всіх оголошень.

    Локаль `uk-UA` робоча. З десктопного IP вона 301-иться на головну, але це
    гео/бот-фільтр Bybit, а не поламаний маршрут: на пристрої сторінка
    відкривається українською і показує потрібного мерчанта.
    """
    if not entity_id:
        return ""
    eid = str(entity_id)
    if kind == "order":
        return ("https://www.bybit.com/fiat/trade/otc/order-detail"
                f"?orderId={quote(eid, safe='')}")
    mask = eid if eid.startswith("s") else f"s{eid}"
    return (f"https://www.bybit.com/uk-UA/p2p/profile/{quote(mask, safe='')}"
            "/USDT/UAH/item")


def bybit_profile_scheme_url(merchant_id: str) -> str:
    """
    Картка контрагента Bybit через універсальний webview-шлюз застосунку.

    Підтверджено на пристрої (Bybit 5.21.0, SM-S918B): відкривається профіль
    саме переданого мерчанта — нік, «Ордери за 30 днів», вкладки
    «Оголошення / Оцінка(N)», кнопка «Продати цьому продавцю». Сторінка
    рендериться всередині застосунку, під уже авторизованою сесією.

    Чому саме так, а не нативно:
      * `open/p2p/user/home` не існує в таблиці маршрутів;
      * `open/route?targetUrl=by-mini://p2p/user/home?<будь-який параметр>`
        відкриває ВЛАСНИЙ профіль, id ігнорується;
      * `app.bybit.com/inapp?by_dp=` не приймає всередину схему `bybitapp://`
        (перевірено — падає на головну), тому https-обгортки для профілю немає.

    Схему не можна класти в InlineKeyboardButton, тому шлях лежить через
    redirect.html — так само, як OKX-профіль.
    """
    web = bybit_web_url("profile", merchant_id)
    if not web:
        return ""
    return f"bybitapp://open/web?url={quote(web, safe='')}"


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


def okx_universal_deeplink(scheme_url: str) -> str:
    """
    https-обгортка OKX, яка везе в собі довільний `okx://`-диплінк.

        https://www.okx.com/download?pageSource=p2p&deeplink=<urlencoded okx://…>

    Звідки. Так робить сама OKX: поширення профілю з мобільного застосунку дає
    `okx.com/otc/transfer?shareCode=…`, що редіректить на
    `okx.com/<lang>/p2p/mobile-share`, а її бандл будує саме цю пару
    (`/download?pageSource=` + `&deeplink=` + encodeURIComponent).

    Чому це важливо. `www.okx.com` у застосунку **verified**, шлях обробляє
    `com.okinc.okex.deeplink.SchemeActivity`. Тобто це справжній App Link:
    посилання можна класти прямо в InlineKeyboardButton, і застосунок
    відкриється сам — без redirect.html і без Intent-костилів.

    Підтверджено на пристрої (OKX 6.161.0):
      deeplink=okx://exchange/merchanthome.com?shareCode=… → нативна
        `com.okinc.p2p.userinfo.profile.UserProfilePageActivity`;
      deeplink=okx://app/web?url=…                          → `com.okinc.web.WebActivity`.
    """
    if not scheme_url:
        return ""
    return ("https://www.okx.com/download?pageSource=p2p&deeplink="
            + quote(scheme_url, safe=""))


def okx_profile_scheme_url(public_user_id: str) -> str:
    """
    Картка мерчанта OKX через webview-шлюз застосунку. Сесія НЕ потрібна.

    Підтверджено на пристрої (OKX 6.161.0, SM-S918B): відкривається картка
    саме переданого мерчанта — нік, «Супермерчант з …», виконані ордери за
    30 днів і всього, коефіцієнт виконання, середній час оплати.

    Маршрут `okx://app/web?url=` витягнутий з dex. Це прямий аналог того, що
    вирішило Bybit, і він знімає головну ваду попереднього рішення: shareCode
    видає лише приватний `/v3/c2c/merchant/share`, тобто кнопка працювала лише
    поки жива сесія бота. Тепер сесія впливає лише на те, чи буде картка
    нативною (shareCode) чи webview — але кнопка веде на потрібного мерчанта
    в будь-якому разі.

    Локаль у URL не обов'язкова: перевірено варіанти з `/ru/` і без — обидва
    відкривають ту саму картку.
    """
    if not public_user_id:
        return ""
    web = okx_web_url("profile", public_user_id)
    if not web:
        return ""
    return f"okx://app/web?url={quote(web, safe='')}"


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
        # Профіль мерчанта — нативно, без dplk і без сесії, через офіційну
        # обгортку `_dp` на verified-домені. Див. binance_universal_deeplink().
        #
        # Оголошення тут навмисно немає: `/fiat/ads/detail` веде на керування
        # власними оголошеннями і для чужого advNo показує порожній екран.
        if kind == "profile":
            return binance_universal_deeplink(f"/p2p/advertiserProfile?advertiserNo={eid}")
        return ""

    if exchange == "Bybit":
        # `by_dp` приймає звичайний https і відкриває його у вбудованому
        # браузері застосунку — це і є обгортка на verified-домені, рівно як
        # `_dp` у Binance і `deeplink=` в OKX. Схему `bybitapp://` вона НЕ
        # приймає (перевірено, падає на головну): роутер знає лише
        # by-mini://, by://, http(s)://, intent:// і кілька службових.
        #
        # Підтверджено на пристрої (Bybit 5.21.0): відкривається картка саме
        # переданого мерчанта — нік, ордери за 30 днів, вкладка «Оцінка»,
        # «Купити у цього продавця».
        if kind == "profile":
            web = bybit_web_url("profile", entity_id)
            if web:
                return f"https://app.bybit.com/inapp?by_dp={quote(web, safe='')}"
            return ""
        return {
            "order": f"https://app.bybit.com/inapp/p2p/order/{eid}",
        }.get(kind, "")

    if exchange == "OKX":
        # Раніше тут було порожньо з поміткою «жоден P2P https-шлях не
        # зареєстрований». Це виявилось неправдою: `www.okx.com` verified, а
        # `/download?…&deeplink=` — офіційна обгортка самої OKX, через яку вона
        # й відкриває застосунок зі своєї сторінки поширення. Тому проміжна
        # сторінка для OKX більше не потрібна.
        return okx_universal_deeplink(app_scheme_url(exchange, kind, entity_id))

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

    # OKX-профіль без shareCode: нативного шляху свідомо немає, тому веб.
    if exchange == "OKX":
        web = web_fallback or okx_web_url(kind, entity_id)
        if web:
            return web

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
    # Профіль Binance більше не потребує ані dplk, ані сесії: `_dp` на
    # verified-домені відкриває нативну картку мерчанта напряму. Раніше цей
    # шлях щоразу впирався в приватний share-ендпоінт і мовчки деградував у
    # браузер, щойно протухала сесія.
    if exchange == "Binance" and kind == "profile":
        direct = app_https_url("Binance", "profile", entity_id)
        if direct:
            return direct

    if exchange == "Binance" and kind in ("ad", "order"):
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
                # shareCode дає НАТИВНУ картку (UserProfilePageActivity), тому
                # він кращий за webview. Але веземо його вже не через
                # redirect.html, а офіційною https-обгорткою OKX — вона й так
                # відкриває застосунок, і на один перехід менше.
                return okx_universal_deeplink(okx_merchant_url(code))
        except Exception as e:
            _log.warning("deeplinks: OKX profile — shareCode впав: %s: %s",
                         type(e).__name__, e, exc_info=True)

    return tg_button_url(exchange, kind, entity_id, side=side, web_fallback=web_fallback)


def okx_web_url(kind: Kind, entity_id: str) -> str:
    """
    Звичайна веб-сторінка OKX. Потрібна як фолбек для профілю: нативний
    маршрут без shareCode веде на власний профіль користувача, тому краще
    чесно відкрити сторінку продавця у браузері.
    """
    if not entity_id:
        return ""
    eid = quote(str(entity_id), safe="")
    return {
        "profile": f"https://www.okx.com/p2p/ads-merchant?publicUserId={eid}",
        "order":   f"https://www.okx.com/p2p/order/{eid}",
    }.get(kind, "")


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

    # Binance тепер веде на ПРОФІЛЬ, а не на оголошення. Причина: єдиний
    # нативний екран оголошення (`/fiat/ads/detail`) — це керування власними
    # оголошеннями, для чужого advNo він порожній. А профіль через `_dp`
    # відкривається нативно, без сесії, і має вкладку «Оголошення».
    if ad_id and app_https_url(exchange, "ad", ad_id):
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
