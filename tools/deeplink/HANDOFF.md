# Бриф для Claude Code — диплінки P2P

Прочитай це ПЕРШИМ. Тут підсумок ~10 раундів налагодження на реальному
пристрої. Головна цінність — список **уже перевірених і відкинутих** гіпотез:
без нього ти повториш ті самі помилки, кожна з яких коштувала окремого циклу.

Детальні докази — у `FINDINGS.md` поруч.

---

## Що працює (підтверджено на пристрої, не чіпати)

| Біржа | Екран | Механізм |
|---|---|---|
| Binance | оголошення | `adv-share` → dplk (`data.share.qrCode`) |
| Binance | профіль | `advertiser-share` → dplk (`data.qrCode`) |
| OKX | ордер | `okx://exchange/p2p/order?id=` → `OrderDetailActivity` |
| OKX | профіль | `okx://app/web?url=<веб-картка>` — **сесія не потрібна** |
| OKX | профіль (краще) | `merchant/share` → shareCode → `okx://exchange/merchanthome.com?shareCode=` → нативна `UserProfilePageActivity` |
| Bybit | профіль | `bybitapp://open/web?url=<enc веб-профіль>` → картка контрагента у вбудованому webview |

Код: `bot/deeplinks.py`, `bot/binance_share.py`, `bot/okx_share.py`,
`redirect.html`. Тести: `tests/test_deeplinks.py` (56, усі зелені).

---

## ЗАБОРОНЕНО пробувати вдруге — перевірено, не працює

1. **Ті конкретні маршрути Binance, що вели на сутність за id.**
   `/fiat/ads/detail`, `/p2p/advertiserProfile`, `/p2p/orderDetail`,
   `/fiat/orderDetails` — дають `NoSupportRouterPathActivity`. Причина не в
   іменах: у dex є `externalDeeplinkAllows` + серверний конфіг
   `android_nezha_enable_external_deeplink_allowed_v2`. Це **серверний білий
   список**, його немає в APK.

   ⚠️ Формулювання «будь-який маршрут Binance заблокований» було завеликим —
   05.08 воно спростоване. Зі схемою `bnc://` ззовні відкриваються справжні
   C2C-екрани:

   | Маршрут | Activity |
   |---|---|
   | `bnc://app.binance.com/fiat/merchant/store` | `FiatMerchantStoreListActivity` |
   | `bnc://app.binance.com/fiat/ads/share` | `FiatPostAdsShareActivity` |
   | `bnc://app.binance.com/fiat/adSharingCode` | `AdSharingCodeActivity` |
   | `bnc://app.binance.com/mp/web?appId=…` | `NezhaNormalActivity` (міні-апка) |

   Тобто двері не замкнені — просто **жоден із цих екранів не приймає
   advertiserNo і не веде на конкретного мерчанта**. `merchant/store` — це
   список магазинів, `adSharingCode` — форма вводу коду доступу до приватного
   оголошення (код видає сам рекламодавець). Для конкретної сутності досі
   лишається лише dplk.

2. **`csrftoken` = кука `cr00`.** НІ. Це різні значення, доведено на живому
   cURL. Підстановка `cr00` дає `401 Please log in first`. Брати треба
   заголовок `csrftoken` зі збереженої сесії. Є тест-охоронець.

3. **`okx://exchange/p2p/profile?userId=`.** Ігнорує переданий id і відкриває
   профіль ЗАЛОГІНЕНОГО користувача (тобто себе). Гірше за браузер. Прибрано,
   є тест-охоронець.

4. **Вердикт `APP` від `am start` як доказ.** Нічого не доводить: у Binance
   (`FirstDispatchRouterActivity`) і Bybit (`MainActivity`) один диспетчер на
   всі маршрути. Потрібен скріншот або ім'я кінцевої Activity.

5. **`bybitapp://open/p2p/...`.** Такого маршруту в роутері НЕМАЄ. У `libapp.so`
   лежить таблиця з 92 декларованих маршрутів у нотації
   `://шлях{param?,param?}` — жодного `open/p2p` серед них. Тому перебір імені
   параметра всередині цієї форми був безнадійний за побудовою. Витягти
   таблицю: `grep` по рядках, що матчать `://[\w/-]+\{[^}]*\}`.

6. **`by-mini://p2p/user/home` з будь-яким параметром.** Відкривається (через
   `open/route?targetUrl=`), але показує профіль ЗАЛОГІНЕНОГО користувача й
   ігнорує переданий id. Перевірено 6 імен (`maskId`, `targetUserId`,
   `makerUserId`, `userId`, `accountId`, `targetAccountId`) × кодований і
   сирий варіант × `by_dp`, плюс пара і трійця
   `targetUserId`+`targetNickName`(+`targetUserAuthStatus`) — усі 13 прогонів
   дали один і той самий власний профіль. Точний двійник пастки №3 з OKX.
   Є тест-охоронець.

8. **`bybitapp://open/otc/exchange/entry?targetPageName=…`.** Попри назву, це
   НЕ P2P: відкриває екран «Конвертувати». `targetPageName` не діє. Реєстр
   імен сторінок OTC у бінарнику існує (`userTradeInfoDetails`, `userCenter`,
   `blockUser`, `orderHistory`, …), але цим маршрутом він не адресується.

7. **`https://app.bybit.com/inapp?by_dp=` з внутрішньою схемою `bybitapp://`.**
   Не приймає, падає на головну. `by_dp` розуміє лише `by-mini://` і `by://`.
   Отже https-обгортки для профілю Bybit не існує — тільки `redirect.html`.

5. **`adb exec-out screencap -p > file.png` у PowerShell.** Псує PNG
   безповоротно. Тільки `adb shell screencap` + `adb pull`.

---

## Пастки, на яких ми вже спіткнулись

- **Префікс `bn_`.** `exchanges/binance.py` зберігає `id=f"bn_{advNo}"`.
  Якщо долетить до API — `code=083626 Оголошення не існує`. Знімається
  в `strip_exchange_prefix()`.
- **Реферер.** Приватні C2C-ендпоінти Binance звіряють `Referer`. Має бути
  сторінка мерчанта, не загальний `p2p.binance.com/`.
- **Хост у Intent.** dplk приходить на `www.binance.com`, але в манифесті
  зареєстровані лише `app.binance.com`, `app.binance.info`,
  `accounts.binance.com`. Для Intent хост підміняється на `app.binance.com`.
- **Кнопки Telegram запікаються при відправці.** Старі повідомлення тримають
  старі URL назавжди. Тестувати тільки на свіжому алерті.
- **`InlineKeyboardButton(url=)` приймає лише http/https.** Custom scheme
  (`okx://`, `bybitapp://`) — тільки через `redirect.html`.
- **APK у `C:\apks\` застаріли.** Апки на пристрої оновлюються самі, і таблиця
  маршрутів разом з ними. Перед аналізом тягнути актуальну:
  `adb shell pm path <pkg>` → `adb pull`.
- **`am start -d "intent://…#Intent;…"` не працює.** `am` не розбирає
  Intent-фрагмент і каже `unable to resolve Intent`. Це межа стенду, а не
  провал маршруту. Щоб перевірити саме ту форму, яку шле `redirect.html`:
  підняти локальний сервер, `adb reverse tcp:8765 tcp:8765`, відкрити на
  пристрої `http://localhost:8765/redirect.html?…`.
- **Git Bash ламає adb-шляхи.** `/sdcard/x.png` перетворюється на
  `C:/Program Files/Git/sdcard/x.png`, і `pull` тихо не знаходить файл.
  Лікується `export MSYS_NO_PATHCONV=1`.
- **`python` у PATH — заглушка Microsoft Store** (виходить з кодом 49, нічого
  не виконавши). Запускати `.venv\Scripts\python.exe` явно.
- **PowerShell 5.1 читає `.ps1` як ANSI без BOM** — кирилиця в скрипті
  розсипається і файл падає з `missing terminator`. Зберігати з UTF-8 BOM.

---

## Відкриті питання

**0-БІС. ГОЛОВНЕ ВІДКРИТТЯ (05.08, друга половина дня).**

У кожної біржі є **власна https-обгортка на verified-домені**, яка везе в собі
диплінк і яку застосунок обробляє САМ, в обхід серверного білого списку:

```
OKX      https://www.okx.com/download?pageSource=p2p&deeplink=<urlencoded okx://…>
Binance  https://app.binance.com/en/download?_dp=<urlencoded base64 внутрішнього шляху>
Bybit    https://app.bybit.com/inapp?by_dp=<urlencoded https-адреса веб-картки>
```

Різниця між ними лише в тому, що кожна приймає всередину. OKX і Binance —
власні внутрішні маршрути. Bybit — **звичайний https**: схему `bybitapp://`
його обгортка відкидає (падає на головну), бо роутер знає тільки
`by-mini://`, `by://`, `http(s)://`, `intent://` і кілька службових.

Це знімає залежність від share-API і сесії для обох бірж, і прибирає
`redirect.html` з ланцюга: посилання http(s), тому лягає прямо в
InlineKeyboardButton.

Як знайдено. Підказка від користувача: поширення профілю мерчанта з мобільної
OKX дає `okx.com/otc/transfer?shareCode=…`, що редіректить на
`okx.com/<lang>/p2p/mobile-share`, а її JS-бандл будує пару
`/download?pageSource=` + `&deeplink=` + `encodeURIComponent`. Далі та сама
ідея перевірена на Binance — `app.binance.com` verified, і `?_dp=` спрацював.

Підтверджено на пристрої:

| Посилання | Activity |
|---|---|
| `okx.com/download?…&deeplink=okx://exchange/merchanthome.com?shareCode=` | `UserProfilePageActivity` (нативна картка) |
| `okx.com/download?…&deeplink=okx://app/web?url=` | `WebActivity` (картка у webview) |
| `app.binance.com/en/download?_dp=b64("/p2p/advertiserProfile?advertiserNo=…")` | `FiatMerchantDetailsActivity` (нативна картка) |
| `app.bybit.com/inapp?by_dp=<https веб-картка мерчанта>` | картка мерчанта у застосунку |

**`redirect.html` більше не потрібен жодній біржі.** Він лишається в репозиторії
як запасний шлях, але кнопки в нього не ведуть.

⚠️ Наслідок для заборони №1: `/p2p/advertiserProfile` роками лежав у списку
мертвих. Мертва там **гола форма**, а не шлях. Усередині `_dp` він живий.

**0. Головний архітектурний висновок (05.08).** Кнопка не повинна залежати від
живої сесії бота. Сесії протухають, і тоді кнопка мовчки деградує у браузер —
саме це й сталося. Правильний шлях: **webview-шлюз застосунку**, який є в
кожної з трьох бірж і не потребує авторизації:

```
Bybit  bybitapp://open/web?url=<веб-картка>     ✓ працює
OKX    okx://app/web?url=<веб-картка>            ✓ працює
Binance bnc://app.binance.com/webview/webview?type=default&url=<base64>
        відкриває BardActivity, але сторінку НЕ рендерить — навіть URL,
        узятий дослівно з dex. Порожній екран. Це третій рівень захисту
        Binance, окремий від білого списку маршрутів.
```

Тому share-API лишається лише як **покращення** (нативна картка замість
webview), а не як умова роботи кнопки.

**0-ТЕР. OKX: shareCode тепер кешується назавжди.**
Код мерчанта сталий — він не протухає разом із сесією, якою його дістали.
Тому він лягає в таблицю `okx_share_codes` і далі відкриває нативну картку
без будь-якої авторизації. Масовий разовий збір:
`python -m tools.deeplink.harvest_okx_codes` (у базі ~721 мерчант OKX).

Що з'ясовано про доступ до коду:
* `/v3/c2c/merchant/liteProfile?publicUserId=` — **публічний**, 200 без
  авторизації, 62 поля даних про мерчанта. Але `shareCode` там немає.
* `/v3/c2c/merchant/share`, `merchant/sharedInfo`, `merchant/scoreDetail`,
  `tradingOrders/share` — 403. Оскільки сусідні `/v3/c2c/` публічні, це
  схоже на авторизаційний гейт, а не на edge-блок.
* Веб-кнопка «Поділитися» на сторінці мерчанта віддає звичайний URL з
  `publicUserId`, а `merchant/share` у веб-трафіку не з'являється взагалі —
  тобто код, найімовірніше, видається лише мобільній апці.

**1. OKX: `merchant/share` не завжди віддає `shareCode`.**
Втратило гостроту: без коду кнопка тепер веде у webview-картку потрібного
мерчанта, а не в браузер. Код лише покращує вигляд.
У логах `okx_share merchant/share: shareCode у відповіді немає; data=…`.
Гіпотеза: код видається лише верифікованим мерчантам. Треба зібрати кілька
таких `data=` і порівняти з мерчантом, для якого код прийшов
(`005977cb24` → `AysnnUZlACimN`).

**2. Bybit: профіль — ЗАКРИТО.** Нативного маршруту на картку контрагента не
існує; ім'я параметра ні до чого (див. заборони 5–7). Робоче рішення —
універсальний webview-шлюз хоста:

```
bybitapp://open/web?url=<enc https://www.bybit.com/uk-UA/p2p/profile/<userMaskId>/USDT/UAH/item>
```

Підтверджено наскрізно на SM-S918B, Bybit 5.21.0: `redirect.html` → Intent →
застосунок → картка потрібного мерчанта (нік, % виконання, оцінки, банки,
«Купити USDT»). Сторінка рендериться під уже авторизованою сесією користувача.
Ідентифікатор — `userMaskId` (те, що вже лежить у `Order.merchant_id`); іншого
не буде: публічний фід віддає `userId="0"` і `accountId="0"` для всіх оголошень.

Важливий нюанс, який варто знати наперед. Нативна картка контрагента в
застосунку **існує** — `UserTradeInfoDetailsPage` (модуль
`fiat/otc/user_center/user_trade_info_details`). Перевірено вручну: тап по
ніку мерчанта в P2P-списку відкриває саме її, і вона багатша за веб —
вкладки Info / Advertisement / Відгук плюс блок «зі мною» (остання угода,
кількість угод, сума транзакцій, відсоток виконаних). Вебв'ю цього не дає.

Але ззовні вона недосяжна: у таблиці з 92 декларованих маршрутів запису на неї
немає, а всі три відомі маршрути міні-апки — `p2p/home`, `p2p/order/detail`,
`p2p/user/home`. Навігація на неї всередині застосунку йде об'єктом-аргументом
Flutter-роутера, а не URI. Тому вебв'ю — стеля, поки Bybit не експортує
маршрут. Не витрачай на це ще один раунд без нових вхідних.

Лишилось: **ордер Bybit**. Декларований маршрут —
`open/fiat_otc_order_detail{orderId?,sourcePage?}`, у код уже покладений, але з
несправжнім id відкриває головну, тож потрібен реальний orderId для
підтвердження. Share-API в P2P-модулі Bybit немає (перевірено).

**3. Чи взагалі відкривається апка Binance з dplk через Intent.**
Останній стан: dplk у кнопці є, сторінка будує Intent з `app.binance.com`,
але фінального підтвердження на пристрої ще не було.

---

## Інструменти (уже написані, використовуй)

- `show_links.py --live` — що бот віддасть ЗАРАЗ + стан сесій і запобіжників.
  Найшвидший спосіб відрізнити «код неправильний» від «сесія протухла».
- `probe_binance_share.py`, `probe_okx_share.py` — контракти share-API.
- `check_binance_session.py` — драбинка запитів, локалізує рівень поломки.
- `test_landing_v2.ps1` — прогін диплінків зі скріншотами й `uiautomator`.
- `extract_deeplinks.py <apk>` — маршрути з AndroidManifest.

APK лежать у `C:\apks\{binance,okx,bybit}\`. Для Bybit код у
`split_config.arm64_v8a.apk` → `lib/arm64-v8a/libapp.so` (Flutter AOT).

---

## Деплой

- Сервер: `deploy.ps1` (`root@167.233.147.232`, `/root/app`).
- `redirect.html` — GitHub Pages, репозиторій `p2pSpreadAnalyticBot`.
  **Без деплою сторінки OKX-профіль і Binance-dplk не працюють**: обидва
  йдуть через неї.

---

## Як не зламати

Перед кожною правкою в диплінках прогонь `pytest tests/test_deeplinks.py`.
Там 56 тестів, і частина з них — саме охоронці від повернення мертвих
маршрутів. Якщо тест заважає — це майже напевно не тест поганий, а правка
повертає те, що вже перевірено як непрацююче.
