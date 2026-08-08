"""
Тести шаблонів диплінків.

Еталон — фактичний вивід `adb shell am start -W` на реальному пристрої
(tools/deeplink/results_*.txt). Якщо тест впав, значить шаблон розійшовся
з тим, що застосунок реально перехоплює.
"""

from base64 import b64decode
from decimal import Decimal
from urllib.parse import quote, unquote

import pytest

from bot.deeplinks import (
    android_intent_url,
    app_https_url,
    app_scheme_url,
    bybit_web_url,
    resolve_target,
    supports_app_link,
    tg_button_url,
)
from exchanges.base import Order

AD = "11523456789012345678"
MERCHANT = "s1234567890"
ORDER = "20512345678901234567"


# ── підтверджені прогоном через adb ──────────────────────────────────────────

@pytest.mark.parametrize("exchange,kind,eid,expected", [
    ("Bybit",   "order",   ORDER,    f"https://app.bybit.com/inapp/p2p/order/{ORDER}"),
])
def test_https_deeplinks_match_adb_results(exchange, kind, eid, expected):
    assert app_https_url(exchange, kind, eid) == expected


@pytest.mark.parametrize("exchange,kind,eid,expected", [
    ("OKX",     "order",   ORDER,    f"okx://exchange/p2p/order?id={ORDER}"),
])
def test_scheme_deeplinks_match_adb_results(exchange, kind, eid, expected):
    assert app_scheme_url(exchange, kind, eid) == expected


# ── маршрути, які роутер Binance відкинув (NoSupportRouterPathActivity) ──────

DEAD_BINANCE_PATHS = ("/fiat/ads/detail", "/p2p/advertiserProfile",
                      "/p2p/userProfile", "/fiat/merchant/details",
                      "/p2p/orderDetail")


@pytest.mark.parametrize("kind", ["ad", "order", "profile"])
def test_binance_never_uses_rejected_routes_raw(kind):
    """
    Ці шляхи дають NoSupportRouterPathActivity, якщо кинути їх у застосунок
    ГОЛИМ зовнішнім інтентом — серверний білий список їх не пропускає.

    Важливе уточнення 05.08: заборонена саме гола форма, а не сам шлях.
    Усередині офіційної обгортки `_dp` на verified-домені той самий
    `/p2p/advertiserProfile` відкриває нативну картку мерчанта — перевірено
    на пристрої. Тому тут перевіряємо лише те, що шлях не пішов «як є».

    Розкодовувати `_dp` обов'язково: інакше тест проходить вхолосту, бо
    всередині base64 літерального збігу немає — і охоронець стає декорацією.
    """
    for url in (app_https_url("Binance", kind, "X"), app_scheme_url("Binance", kind, "X")):
        if not url:
            continue
        wrapped = "_dp=" in url
        for dead in DEAD_BINANCE_PATHS:
            assert dead not in url, f"голий відкинутий шлях: {url}"
        if wrapped:
            inner = b64decode(unquote(url.split("_dp=")[1]) + "==").decode()
            assert inner.startswith("/p2p/advertiserProfile"), (
                f"через _dp дозволений лише підтверджений шлях, а прийшов: {inner}")


def test_binance_ad_falls_back_to_web():
    """
    Для оголошення нативного екрана немає: `/fiat/ads/detail` — це керування
    ВЛАСНИМИ оголошеннями, для чужого advNo він порожній (перевірено на
    пристрої). Тому оголошення лишається на канонічному веб-URL або dplk.
    """
    assert tg_button_url("Binance", "ad", "X").startswith("https://p2p.binance.com/")


def test_binance_profile_is_native_without_session():
    """
    Головна зміна 05.08. Профіль Binance більше не залежить ані від dplk, ані
    від сесії: `_dp` на verified-домені app.binance.com відкриває нативну
    картку мерчанта (FiatMerchantDetailsActivity, підтверджено на пристрої).
    """
    url = tg_button_url("Binance", "profile", MERCHANT)
    assert url.startswith("https://app.binance.com/en/download?_dp=")
    assert "redirect.html" not in url
    decoded = b64decode(unquote(url.split("_dp=")[1]) + "==").decode()
    assert decoded == f"/p2p/advertiserProfile?advertiserNo={MERCHANT}"


# ── OKX: профіль без сесії ───────────────────────────────────────────────────

def test_okx_profile_uses_webview_gateway():
    """
    Підтверджено на пристрої (OKX 6.161.0): `okx://app/web?url=<веб-картка>`
    відкриває картку саме переданого мерчанта.

    Це головне — маршрут НЕ потребує shareCode, а отже й живої сесії бота.
    Раніше профіль без коду падав у браузер, і кнопка мовчки деградувала
    щоразу, коли протухала сесія.
    """
    url = app_scheme_url("OKX", "profile", MERCHANT)
    assert url.startswith("okx://app/web?url=")
    assert quote(f"https://www.okx.com/p2p/ads-merchant?publicUserId={MERCHANT}",
                 safe="") in url


def test_okx_profile_never_uses_own_profile_route():
    """
    Охоронець. `okx://exchange/p2p/profile?userId=` ігнорує переданий id і
    відкриває профіль залогіненого користувача.
    """
    for kind in ("ad", "order", "profile"):
        assert "p2p/profile" not in app_scheme_url("OKX", kind, MERCHANT)


def test_okx_profile_needs_no_live_session():
    """
    Кнопка профілю OKX має будуватись без звернення до біржі. db=None — це
    і є «сесії немає»: раніше в цьому випадку кнопка вела в браузер.
    """
    import asyncio as _asyncio
    from bot.deeplinks import tg_button_url_async
    url = _asyncio.run(tg_button_url_async("OKX", "profile", MERCHANT, db=None))
    assert url.startswith("https://www.okx.com/download?")
    assert "deeplink=" in url
    assert MERCHANT in unquote(unquote(url))


# ── Bybit ────────────────────────────────────────────────────────────────────

def test_bybit_order_uses_declared_native_route():
    """
    `open/fiat_otc_order_detail{orderId?,sourcePage?}` — дослівно з таблиці
    декларованих маршрутів у libapp.so апки, знятої з пристрою (5.21.0).
    З несправжнім id відкриває головну, тому на реальному ордері ще не
    підтверджено — але це строго краще за `open/p2p/order/detail`, якого в
    таблиці немає взагалі.
    """
    assert app_scheme_url("Bybit", "order", ORDER) == (
        f"bybitapp://open/fiat_otc_order_detail?orderId={ORDER}&sourcePage=deeplink"
    )


def test_bybit_profile_goes_through_webview_gateway():
    """
    Сира схема (для redirect.html і Intent). Підтверджено на пристрої:
    відкривається картка саме переданого мерчанта.
    """
    url = app_scheme_url("Bybit", "profile", MERCHANT)
    assert url.startswith("bybitapp://open/web?url=")
    assert quote(f"https://www.bybit.com/uk-UA/p2p/profile/{MERCHANT}/USDT/UAH/item",
                 safe="") in url


def test_bybit_profile_needs_no_redirect_page():
    """
    `app.bybit.com/inapp?by_dp=<https веб-картка>` — обгортка на
    verified-домені, рівно як `_dp` у Binance і `deeplink=` в OKX.
    Підтверджено на пристрої: картка мерчанта відкривається у застосунку.

    Схему `bybitapp://` ця обгортка НЕ приймає — падає на головну. Роутер
    знає лише by-mini://, by://, http(s)://, intent:// і кілька службових.
    """
    url = tg_button_url("Bybit", "profile", MERCHANT)
    assert url.startswith("https://app.bybit.com/inapp?by_dp=")
    assert "redirect.html" not in url
    assert unquote(url.split("by_dp=")[1]) == \
        f"https://www.bybit.com/uk-UA/p2p/profile/{MERCHANT}/USDT/UAH/item"


def test_bybit_profile_never_uses_own_profile_route():
    """
    Охоронець. `by-mini://p2p/user/home` через `open/route?targetUrl=` відкриває
    профіль ЗАЛОГІНЕНОГО користувача і мовчки ігнорує переданий id — перевірено
    з шістьма іменами параметра (maskId, targetUserId, makerUserId, userId,
    accountId, targetAccountId) плюс без кодування і через by_dp: усі 10 разів
    той самий власний профіль. Показати людині її ж профіль замість продавця
    гірше, ніж відкрити браузер.
    """
    for kind in ("ad", "order", "profile"):
        url = app_scheme_url("Bybit", kind, MERCHANT)
        assert "p2p/user/home" not in url, f"повернувся маршрут власного профілю: {url}"
        assert "open/p2p/" not in url, f"маршруту open/p2p/* у роутері немає: {url}"


def test_bybit_merchant_id_keeps_mask_prefix():
    """
    Мерчант у Bybit — це userMaskId з префіксом `s`. Числового id не існує:
    публічний фід віддає userId="0" і accountId="0" для всіх оголошень, тому
    підставляти туди щось інше нема з чого.
    """
    assert "/profile/s1234/" in bybit_web_url("profile", "1234")
    assert "/profile/s1234/" in bybit_web_url("profile", "s1234")


def test_binance_webview_gateway():
    """
    Формат підтверджений на пристрої: приземлення на
    com.binance.c2c.main.FiatMainActivity замість NoSupportRouterPathActivity.
    """
    from bot.deeplinks import binance_webview_url

    url = binance_webview_url("https://www.binance.com/fixedLoan")
    assert url == ("https://app.binance.com/webview/webview?type=default"
                   "&url=aHR0cHM6Ly93d3cuYmluYW5jZS5jb20vZml4ZWRMb2Fu")
    assert binance_webview_url("") == ""


def test_binance_share_url_wraps_dplk():
    """Голий dplk на www.binance.com пішов би в браузер — хост не в манифесті."""
    from bot.deeplinks import binance_share_url

    dplk = "https://www.binance.com/uk-UA/qr/dplk393b631d811449868289e4123ae90536"
    url = binance_share_url(dplk)
    assert url.startswith("https://app.binance.com/webview/webview?type=default&url=")
    assert "www.binance.com" not in url.split("url=")[0]
    from base64 import b64decode
    assert b64decode(url.split("url=")[1]).decode() == dplk


def test_binance_miniapp_url_matches_dex_literal():
    """
    Найсильніша перевірка формату: збираємо URL самі і звіряємо з рядком, який
    лежить у dex застосунку дослівно. Якщо збігається — формат правильний.
    """
    from bot.deeplinks import binance_miniapp_url

    assert binance_miniapp_url("pages/merchant-detail/index", "fromNative=true") == (
        "https://app.binance.com/mp/web?appId=Bzp9defeaRgNqhgV4wEG5C"
        "&startPagePath=cGFnZXMvbWVyY2hhbnQtZGV0YWlsL2luZGV4"
        "&startPageQuery=ZnJvbU5hdGl2ZT10cnVl"
    )
    assert binance_miniapp_url("") == ""


def test_okx_profile_without_sharecode_never_opens_own_profile():
    """
    Регрес із реального тесту на телефоні: `okx://exchange/p2p/profile?userId=`
    ІГНОРУЄ переданий id і відкриває профіль залогіненого користувача — тобто
    самого себе. Показати людині її власний профіль замість продавця гірше,
    ніж браузер, бо виглядає як помилка бота.

    Раніше тут стояло `app_scheme_url(...) == ""`, тобто перевірялась конкретна
    реалізація («схеми немає взагалі»), а не сам інваріант. Через це кнопка без
    shareCode вела в браузер і мовчки залежала від живої сесії. Тепер профіль
    іде через webview-шлюз `okx://app/web?url=`, сесії не потребує, а інваріант
    лишився той самий: маршрут власного профілю не повертати ніколи.
    """
    assert "p2p/profile" not in app_scheme_url("OKX", "profile", MERCHANT)
    url = tg_button_url("OKX", "profile", MERCHANT)
    assert url.startswith("https://")
    assert "p2p/profile" not in url
    assert MERCHANT in url


def test_okx_merchant_url_confirmed_on_device():
    """
    Єдиний підтверджений шлях до картки продавця OKX:
    okx://exchange/merchanthome.com?shareCode=… → UserProfilePageActivity.
    """
    from bot.deeplinks import okx_merchant_url

    assert okx_merchant_url("AysnnUZlACimN") == \
        "okx://exchange/merchanthome.com?shareCode=AysnnUZlACimN"
    assert okx_merchant_url("") == ""


def test_okx_qrcode_url_is_not_used():
    """
    У відповіді merchant/share є ще qrCode на /ua/p2p — цей шлях у манифесті
    не зареєстрований, тому в кнопку він потрапити не має.
    """
    from bot.deeplinks import okx_universal_link

    assert okx_universal_link(
        "https://okx.com/ua/p2p?action=otcTransfer&shareCode=AysnnUZlACimN") == ""


def test_okx_order_never_uses_merchant_sharecode():
    """
    Регрес: shareCode веде на merchanthome (профіль). Для ордера це
    неправильний екран, тому асинхронний шлях ордера НЕ має чіпати
    get_share_code — інакше кнопка ордера відкриє профіль продавця.
    """
    import asyncio as _asyncio
    from bot.deeplinks import tg_button_url_async

    class ShouldNotBeCalled:
        async def get_auth_session(self, *a, **kw):
            raise AssertionError("get_share_code не має викликатись для ордера OKX")

    url = _asyncio.run(tg_button_url_async(
        "OKX", "order", ORDER, db=ShouldNotBeCalled(),
        web_fallback="https://www.okx.com/p2p/order/1"))
    # маршрут ордера будує сама сторінка redirect.html зі схемою p2p/order
    assert "redirect.html" in url or url.startswith("https://")
    assert "merchanthome" not in url


def test_okx_share_code_travels_via_redirect_page():
    """Схему не можна класти в кнопку TG, тому код їде параметром сторінки."""
    from bot.deeplinks import _redirect_url

    url = _redirect_url("OKX", "profile", "005977cb24", share_code="AysnnUZlACimN")
    assert "code=AysnnUZlACimN" in url
    assert url.startswith("https://")


def test_okx_universal_link():
    """`/ul/*` — єдиний P2P-придатний https-шлях у манифесті OKX."""
    from bot.deeplinks import okx_universal_link

    assert okx_universal_link("m3w2P7") == "https://www.okx.com/ul/m3w2P7"
    assert okx_universal_link("https://www.okx.com/ul/m3w2P7") == "https://www.okx.com/ul/m3w2P7"
    # не-/ul/ посилання відкидаємо: застосунок його не перехопить
    assert okx_universal_link("https://www.okx.com/p2p/order/1") == ""
    assert okx_universal_link("") == ""


def test_async_degrades_safely_without_db():
    """
    Головна вимога до асинхронного шляху: без БД, з мертвою сесією чи з будь-якою
    помилкою мережі кнопка все одно має бути валідним https, а не впасти.
    """
    import asyncio as _asyncio
    from bot.deeplinks import tg_button_url_async

    for exchange, kind, eid in (
        ("Binance", "ad", AD), ("Binance", "profile", MERCHANT),
        ("OKX", "order", ORDER), ("OKX", "profile", MERCHANT),
        ("Bybit", "order", ORDER),
    ):
        url = _asyncio.run(tg_button_url_async(exchange, kind, eid, db=None))
        assert url.startswith("https://"), f"{exchange}/{kind}: {url!r}"


def test_binance_ad_id_prefix_is_stripped():
    """
    Регрес, який коштував кількох ітерацій. Сканер зберігає id оголошення як
    `bn_<advNo>` (exchanges/binance.py). Якщо префікс дійде до adv-share,
    Binance відповідає `code=083626 Оголошення не існує`, і кнопка тихо падає
    у браузер — при цілком живій сесії.
    """
    from bot.deeplinks import resolve_target, strip_exchange_prefix

    assert strip_exchange_prefix("Binance", "bn_12908254847792041984") == "12908254847792041984"
    assert strip_exchange_prefix("Binance", "12908254847792041984") == "12908254847792041984"
    assert strip_exchange_prefix("OKX", "bn_keep") == "bn_keep"   # чужих не чіпаємо

    # resolve_target для Binance тепер веде на профіль (нативний екран
    # оголошення виявився керуванням власними оголошеннями), тому сам префікс
    # перевіряємо напряму — він досі потрібен для adv-share, коли кнопка
    # ордера/оголошення йде через dplk.
    assert strip_exchange_prefix("Binance", "bn_12908254847792041984") == \
        "12908254847792041984"


def test_binance_csrf_prefers_real_header_not_cr00_cookie():
    """
    Регрес, який уже двічі ламав Binance. `csrftoken` і кука `cr00` — РІЗНІ
    значення (доведено на живому cURL). Якщо в запит піде `cr00`, Binance
    віддасть 401 і кнопка тихо впаде на веб.
    """
    import asyncio as _asyncio
    import bot.binance_share as bs

    captured = {}

    class FakeResp:
        status_code = 200

        def json(self):
            return {"code": "000000",
                    "data": {"qrCode": "https://www.binance.com/uk-UA/qr/dplkOK"}}

    class FakeSession:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

        async def post(self, url, headers=None, cookies=None, json=None, timeout=None):
            captured.update(headers or {})
            return FakeResp()

    class FakeDB:
        async def get_auth_session(self, exchange, user_id=0):
            return (
                {"csrftoken": "REAL_TOKEN", "user-agent": "UA"},   # справжній
                {"cr00": "COOKIE_CR00", "p20t": "web.1.x"},        # НЕ той самий
                0.0,
            )

    import sys
    import types
    fake_mod = types.ModuleType("curl_cffi.requests")
    fake_mod.AsyncSession = lambda **kw: FakeSession()
    fake_pkg = types.ModuleType("curl_cffi")
    fake_pkg.requests = fake_mod
    saved = (sys.modules.get("curl_cffi"), sys.modules.get("curl_cffi.requests"))
    sys.modules["curl_cffi"] = fake_pkg
    sys.modules["curl_cffi.requests"] = fake_mod
    try:
        bs.clear_cache()
        link = _asyncio.run(bs.get_share_link("profile", MERCHANT, FakeDB()))
        assert link == "https://www.binance.com/uk-UA/qr/dplkOK"
        assert captured.get("X-CSRF-TOKEN") == "REAL_TOKEN", \
            f"у запит пішов не той токен: {captured.get('X-CSRF-TOKEN')!r}"
        assert captured.get("X-CSRF-TOKEN") != "COOKIE_CR00"
    finally:
        for name, mod in zip(("curl_cffi", "curl_cffi.requests"), saved):
            if mod is None:
                sys.modules.pop(name, None)
            else:
                sys.modules[name] = mod
        bs.clear_cache()


def test_binance_profile_ignores_session_entirely():
    """
    Профіль не має ходити по dplk взагалі — ні з живою сесією, ні з мертвою.
    Раніше саме тут кнопка мовчки деградувала у браузер, щойно сесія протухала.
    """
    import asyncio as _asyncio
    import bot.binance_share as bs
    from bot.deeplinks import tg_button_url_async

    original = bs._fetch
    calls = {"n": 0}

    class DummyDB:
        pass

    try:
        async def counting(kind, eid, uid, db):
            calls["n"] += 1
            return "https://www.binance.com/uk-UA/qr/dplkTEST"

        bs._fetch = counting
        bs.clear_cache()
        url = _asyncio.run(tg_button_url_async("Binance", "profile", MERCHANT, db=DummyDB()))
        assert url.startswith("https://app.binance.com/en/download?_dp=")
        assert calls["n"] == 0, "профіль не повинен смикати share-ендпоінт"
    finally:
        bs._fetch = original
        bs.clear_cache()


def test_binance_hybrid_switches_by_session_state():
    """
    Гібрид для ОГОЛОШЕННЯ: жива сесія -> нативний dplk, мертва -> канонічний
    веб-URL. Користувач у будь-якому випадку потрапляє на потрібне оголошення.

    Раніше цей тест ганяв `profile`, але профіль тепер нативний і сесії не
    потребує — тож гібрид лишився актуальним саме для оголошення.
    """
    import asyncio as _asyncio
    import bot.binance_share as bs
    from bot.deeplinks import tg_button_url_async

    original = bs._fetch

    class DummyDB:
        pass

    try:
        async def alive(kind, eid, uid, db):
            return "https://www.binance.com/uk-UA/qr/dplkTEST"

        async def dead(kind, eid, uid, db):
            return ""

        bs._fetch = alive
        bs.clear_cache()
        url = _asyncio.run(tg_button_url_async("Binance", "ad", AD, db=DummyDB()))
        assert "redirect.html" in url
        assert "dplk=https%3A%2F%2Fwww.binance.com%2Fuk-UA%2Fqr%2FdplkTEST" in url

        bs._fetch = dead
        bs.clear_cache()
        url = _asyncio.run(tg_button_url_async("Binance", "ad", AD, db=DummyDB()))
        assert url.startswith("https://p2p.binance.com/en/trade/detail")
        assert AD in url
    finally:
        bs._fetch = original
        bs.clear_cache()


def test_share_clients_stop_hammering_after_repeated_failures():
    """
    Коли сесія померла, кожен мерчант ретраїв би окремо. Запобіжник має
    зупинити звернення, щоб не сипати сотнями 401 у бік біржі.
    """
    import asyncio as _asyncio
    import bot.binance_share as bs
    from bot.deeplinks import tg_button_url_async

    original = bs._fetch
    calls = {"n": 0}

    class DummyDB:
        pass

    try:
        async def always_fail(kind, eid, uid, db):
            calls["n"] += 1
            return ""

        bs._fetch = always_fail
        bs.clear_cache()
        for i in range(10):
            _asyncio.run(tg_button_url_async("Binance", "ad", f"a{i}", db=DummyDB()))

        assert calls["n"] <= bs._FAILS_TO_TRIP, \
            f"продовжує довбати біржу: {calls['n']} спроб"
        assert bs.is_available() is False
        bs.clear_cache()
        assert bs.is_available() is True      # після оновлення сесії знову працює
    finally:
        bs._fetch = original
        bs.clear_cache()


def test_share_clients_never_raise():
    """Білдер повідомлення не має права впасти через share-ендпоінт."""
    import asyncio as _asyncio

    class Boom:
        async def get_auth_session(self, *a, **kw):
            raise RuntimeError("БД лягла")

    from bot.binance_share import get_share_link, clear_cache as bc
    from bot.okx_share import get_share_code, clear_cache as oc
    bc(); oc()

    assert _asyncio.run(get_share_link("ad", AD, Boom())) == ""
    assert _asyncio.run(get_share_code("profile", MERCHANT, Boom())) == ""


def test_okx_merchant_by_share_code():
    from bot.deeplinks import okx_merchant_scheme_url
    assert okx_merchant_scheme_url("2Zh0v1gixDZCx") == \
        "okx://exchange/merchanthome.com?shareCode=2Zh0v1gixDZCx"
    assert okx_merchant_scheme_url("") == ""


def test_okx_extract_share_code_embedded_string():
    from bot.okx_share import _extract_share_code
    assert _extract_share_code({"shareCode": "AysnnUZlACimN"}) == "AysnnUZlACimN"
    data = {
        "nickname": "sh3***@gmail.com",
        "password": "Нажмите здесь для торговли криптой на OKX https://okx.com/otc/transfer?shareCode=9ZpaFCwJnn8my SQ7491 [Поделиться кодом:￥9ZpaFCwJnn8my￥]"
    }
    assert _extract_share_code(data) == "9ZpaFCwJnn8my"


# ── мертві шаблони не повинні повернутись ────────────────────────────────────

DEAD_PATTERNS = (
    "p2p.binance.com/en/trade/detail",
    "www.bybit.com/uk-UA/p2p/order",
    "www.okx.com/p2p/order",
    "binance://app/",          # неіснуюча схема зі старого коду
    "bybit://app/p2p",
    "okx://app/p2p",
    "com.binance.merchant",    # неіснуючий пакет
)


@pytest.mark.parametrize("exchange", ["Binance", "OKX", "Bybit"])
@pytest.mark.parametrize("kind", ["ad", "order", "profile"])
def test_no_dead_patterns(exchange, kind):
    for url in (app_https_url(exchange, kind, "X"), app_scheme_url(exchange, kind, "X")):
        for dead in DEAD_PATTERNS:
            assert dead not in url, f"{exchange}/{kind} повернув мертвий шаблон: {url}"


# ── обмеження Telegram: у кнопку можна класти лише http/https ────────────────

@pytest.mark.parametrize("exchange", ["Binance", "OKX", "Bybit"])
@pytest.mark.parametrize("kind", ["ad", "order", "profile"])
def test_tg_button_url_is_always_https(exchange, kind):
    url = tg_button_url(exchange, kind, "X", web_fallback="https://example.com")
    assert url.startswith("https://"), f"{exchange}/{kind}: Telegram відхилить {url}"


def test_okx_needs_no_redirect_page():
    """
    Раніше тут стояло протилежне: «у OKX немає https-маршрутів на P2P, тому
    кнопка веде на проміжну сторінку». Це виявилось неправдою — `www.okx.com`
    у застосунку verified, а `/download?…&deeplink=` це офіційна обгортка самої
    OKX (її будує бандл сторінки p2p/mobile-share). Підтверджено на пристрої:
    відкривається нативна картка мерчанта без будь-якої проміжної сторінки.
    """
    url = tg_button_url("OKX", "order", ORDER)
    assert "redirect.html" not in url
    assert url.startswith("https://www.okx.com/download?")
    assert ORDER in unquote(unquote(url))


def test_binance_needs_no_redirect_page():
    """А Binance і Bybit — навпаки, прокладка більше не потрібна."""
    assert "redirect.html" not in tg_button_url("Binance", "order", ORDER)
    assert "redirect.html" not in tg_button_url("Bybit", "order", ORDER)


# ── Android Intent URL ───────────────────────────────────────────────────────

def test_binance_external_route_exists_only_for_profile():
    """
    Раніше цей тест звався `..._has_no_external_route_at_all` і вимагав
    порожнечі для всіх трьох kind. Твердження спростоване на пристрої: через
    `_dp` на app.binance.com профіль відкривається нативно.

    Для оголошення й ордера зовнішнього маршруту досі немає — там лишається
    dplk або веб.
    """
    assert app_https_url("Binance", "profile", "X").startswith(
        "https://app.binance.com/en/download?_dp=")
    for kind in ("ad", "order"):
        assert app_https_url("Binance", kind, "X") == ""
        assert app_scheme_url("Binance", kind, "X") == ""


def test_binance_has_no_scheme_intent():
    """
    Голої схеми для Binance не будуємо в жодному разі: Intent-форма
    (`intent://…;scheme=bnc;package=com.binance.dev`) впирається в той самий
    серверний білий список. Вхід лише через `_dp` на verified-домені.
    """
    for kind in ("ad", "order", "profile"):
        assert app_scheme_url("Binance", kind, "X") == ""
        assert android_intent_url("Binance", kind, "X", "https://example.com") == ""


def test_android_intent_okx_package():
    intent = android_intent_url("OKX", "order", ORDER)
    assert "package=com.okinc.okex.gp;" in intent
    assert "scheme=okx;" in intent


# ── дрібниці ─────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("kind", ["ad", "order", "profile"])
def test_empty_id_returns_empty(kind):
    assert app_https_url("Binance", kind, "") == ""
    assert app_scheme_url("Binance", kind, "") == ""


def test_id_is_url_encoded():
    assert app_https_url("Bybit", "order", "a b&c") == \
        "https://app.bybit.com/inapp/p2p/order/a%20b%26c"


def _mk_order(exchange: str, ad_id: str, merchant_id: str) -> Order:
    return Order(
        id=ad_id, price=Decimal("41.5"), available_amount=Decimal("1000"),
        min_limit=Decimal("500"), max_limit=Decimal("50000"),
        merchant_id=merchant_id, merchant_name="test",
        month_order_count=100, finish_rate_pct=99.0, exchange=exchange,
    )


def test_resolve_target_binance_prefers_profile():
    """
    Раніше Binance вів на оголошення. Перевірка на пристрої показала, що
    єдиний нативний екран оголошення (`/fiat/ads/detail`) — це керування
    ВЛАСНИМИ оголошеннями: для чужого advNo відкривається порожня «Деталі
    оголошення» з іконкою видалення.

    Натомість профіль через `_dp` відкривається нативно й має вкладку
    «Оголошення» — тобто до потрібної ціни звідти один тап.
    """
    order = _mk_order("Binance", AD, MERCHANT)
    assert resolve_target(order) == ("profile", MERCHANT)


@pytest.mark.parametrize("exchange", ["OKX", "Bybit"])
def test_resolve_target_falls_back_to_profile(exchange):
    """Там, де маршруту на оголошення немає, беремо профіль мерчанта."""
    order = _mk_order(exchange, AD, MERCHANT)
    assert resolve_target(order) == ("profile", MERCHANT)


def test_resolve_target_without_ids():
    assert resolve_target(_mk_order("Binance", "", "")) == ("profile", "")


def test_unknown_exchange_falls_back_to_web():
    assert tg_button_url("MEXC", "order", "1", web_fallback="https://web") == "https://web"
    assert supports_app_link("MEXC") is False
    # Binance теж False: зовнішніх маршрутів у нього не лишилось.
    assert supports_app_link("Binance") is False
    assert supports_app_link("Bybit") is True
