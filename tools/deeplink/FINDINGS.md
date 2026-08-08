# Диплінки P2P-бірж — результати розбору APK + .well-known

Джерела: `AndroidManifest.xml` та dex-строки з `apks/{binance,okx,bybit}/base.apk`,
плюс публічні `.well-known/apple-app-site-association` та `assetlinks.json`.

---

## ФІНАЛЬНИЙ СТАН (усе підтверджено на пристрої / живому акаунті)

| Біржа | Екран | Механізм | Стан |
|---|---|---|---|
| Binance | оголошення | `adv-share` -> dplk (`data.share.qrCode`) | ✅ нативний |
| Binance | профіль | `advertiser-share` -> dplk (`data.qrCode`) | ✅ нативний |
| OKX | профіль | `merchant/share` -> shareCode -> `okx://exchange/merchanthome.com?shareCode=` | ✅ `UserProfilePageActivity` |
| OKX | ордер | `okx://exchange/p2p/order?id=` | ✅ `OrderDetailActivity` |
| Bybit | ордер | `app.bybit.com/inapp/p2p/order/<id>` | ⚠️ інтент ловиться, екран не підтверджено скріншотом |
| Bybit | профіль | `bybitapp://open/p2p/user/home?userId=` | ⚠️ те саме |

Binance: dplk лежить у `data.share.qrCode` (оголошення) і `data.qrCode`
(профіль). Обидва мають `expireTime` (кілька днів) — кеш у `binance_share.py`
6 год, тобто завжди свіжіший за термін дії. Приватний виклик вимагає:
референс на сторінку саме того мерчанта + заголовок `csrftoken` (окремий від
куки `cr00`). Це підтверджено на живому акаунті: `code=000000` + реальний dplk.

Лишилось **тільки виконати**, коду вже не треба:
- залити оновлений `redirect.html` на GitHub Pages (без нього OKX і Bybit
  профіль не працюють — кнопка веде на сторінку, а сторінка мертва);
- тримати живу авторизовану сесію Binance у `auth_sessions` (протухає —
  тоді Binance тихо падає на веб, бот не ламається).

### Bybit — що саме лишилось (єдина незакрита ділянка)

Відомо точно:

- маніфест: `https://app.bybit.com/inapp/*` (+12 дзеркал), `bybitapp://open` (будь-який шлях);
- роутинг по шляху, а не `?page=`: `bybitapp://open/home?tab=1`, `.../discovery/earnings/eventPredict?eventId=`;
- внутрішні маршрути mini-app: `by-mini://p2p/home`, `by-mini://p2p/order/detail`, `by-mini://p2p/user/home`;
- https-шлюз приймає закодований диплінк у `by_dp`;
- share-API в P2P-модулі **немає** (є лише pnl/roi/spot/post) — цей шлях закритий остаточно.

Невідомо: **ім'я параметра з id**. У Dart AOT рядки лежать ізольовано, прив'язки
параметра до маршруту в снапшоті немає, статично не витягти.

Кандидати, за спаданням імовірності:

1. `targetUserId` — поряд у снапшоті існує `targetNickName`, тобто є родина
   `target*` для контрагента, якого дивишся. Найсильніший кандидат.
2. `makerUserId`
3. `userId`
4. `accountId`

Головна пастка: усі 11 варіантів дали `MainActivity`, бо у Flutter одна
Activity на весь застосунок. **Вердикт за activity тут не значить нічого** —
рівно та сама помилка, що з Binance у раунді 2. Розрізнити може лише скріншот.

Перевіряє `test_landing_v2.ps1` (screencap уже виправлений на
`adb shell screencap` + `adb pull`, кандидати переставлені в порядку
імовірності):

```powershell
.\test_landing_v2.ps1 -BybitUserId <id мерчанта> -BybitOrderNo <id ордера>
```

Дивитись треба саме `shots\`: чи видно на екрані ім'я мерчанта, чи головна.

---

## Головна причина, чому нічого не працювало (Binance)

Застосунок `com.binance.dev` **не реєструє домен `p2p.binance.com` взагалі**.
У маніфесті єдиний https-хост для P2P-роутингу — `app.binance.com`:

```
[com.eaas.launcher.activities.dispatchrouter.FirstDispatchRouterActivity]  autoVerify=true
  scheme : https          host: app.binance.com, app.binance.info   path: <будь-який>
  scheme : binance, bnc   host: app.binance.com                     path: <будь-який>
```

`assetlinks.json` на `p2p.binance.com` існує і покриває `com.binance.dev` —
саме тому це виглядало так, ніби все має працювати. Але assetlinks лише
**авторизує** домен; вирішує intent-filter у маніфесті, а там `p2p.binance.com`
немає. Тому `https://p2p.binance.com/en/trade/detail/<id>` завжди йде в браузер.

Усі диплінки Binance проходять через один диспетчер, шлях є ключем маршруту.
Маршрути P2P, витягнуті з dex:

| Екран | Маршрут |
|---|---|
| Деталі оголошення (ордера) | `/fiat/ads/detail` |
| Профіль рекламодавця | `/p2p/advertiserProfile` |
| Профіль користувача | `/p2p/userProfile` |
| Картка мерчанта | `/fiat/merchant/details`, `/fiat/merchant/store` |
| Деталі ордера | `/p2p/orderDetail`, `/fiat/orderDetails?id=`, `/c2c/orderDetails` |
| Шеринг оголошення | `/fiat/ads/advshare`, `/p2p/advShare` |
| Історія угод мерчанта | `/fiat/merchant/trade/history` |

Імена параметрів, що зустрічаються в dex: `advertiserNo`, `merchantNo`,
`orderNo`, `orderId`, `userNo`, `advOrderNumber`.

Форма лінка, яку треба перевірити:

```
https://app.binance.com/fiat/ads/detail?adNo=<AD_ID>
https://app.binance.com/p2p/advertiserProfile?advertiserNo=<MERCHANT_ID>
https://app.binance.com/p2p/orderDetail?orderNo=<ORDER_ID>
```

Плюс ті самі шляхи через `bnc://app.binance.com/...` — custom scheme не залежить
від App Link верифікації, тобто спрацює навіть якщо користувач вимкнув
«Відкривати підтримувані посилання» в налаштуваннях застосунку.

---

## OKX

Кастомна схема зареєстрована на `com.okinc.okex.deeplink.SchemeActivity`
(`okex://`, `okx://`, `okxweb3://`, будь-який шлях). Маршрути P2P з dex:

```
okx://exchange/p2p/order
okx://exchange/p2p/orders
okx://exchange/p2p/profile
okx://exchange/p2p/trading?type=buy&crypto=USDT
okx://exchange/p2p/express?type=buy&crypto=<COIN>
okx://exchange/p2p/appeal
okx://exchange/p2p/create_ads
```

**Https-шляхи для OKX покривають P2P НЕ повністю.** Реально зареєстровані
path-патерни (хости `www.okx.com`, `my.okx.com`, `app.okx.com`, `link.okx.com`
та дзеркала):

```
/ul/.*            /download          /copy-trading/.*
/campaigns/.*     /learn/.*          /web3/detail/.*      /walletappconnect/.*
```

`/p2p/order/...` там немає ані на Android, ані в iOS AASA. Тобто «вкрасти лінку
напряму» у формі `https://www.okx.com/p2p/order/<id>` не вийде — те, що ти бачив
з пропозицією відкрити в додатку, майже напевно було `/ul/<код>`, а це
**серверний шортлінк** (`www.okx.com/ul/m3w2P7`), його не можна зібрати самому.

Отже для OKX робочий детермінований варіант — `okx://exchange/p2p/order?...`.

---

## Bybit

Маніфест:

```
[com.bybit.pro.MainActivity]  autoVerify=true
  scheme: https   host: app.bybit.com, go.bybit.com, t.bybit.com, mobile.bybit.com,
                        d.bybit.com, dl.bybit.com, download.bybit.com, install.bybit.com,
                        load.bybit.com, market.bybit.com, pkg.bybit.com, soft.bybit.com,
                        store.bybit.com
  path  : prefix:/inapp, prefix:/ton-connect, prefix:/testnet-inapp

  scheme: bybitapp   host: open   path: <будь-який>
```

iOS AASA на `www.bybit.com` покриває лише `/inapp/*`, `/wechat/*`, `/qq_conn/*`.

**Тобто `https://www.bybit.com/uk-UA/p2p/order/{id}`, який зараз генерується в
`bot/maker_builder.py:177`, ніколи не відкриє застосунок — ні на Android, ні на
iOS.** Заміна має бути на `https://app.bybit.com/inapp/...` або `bybitapp://open?...`.

Формат параметрів поки невідомий: Bybit — Flutter-застосунок, маршрути лежать
в AOT-снапшоті `libapp.so` всередині `split_config.arm64_v8a.apk`, а не в dex
(в `base.apk` всього 260 строк). Це наступний крок, якщо Bybit потрібен.

---

## Що це означає для коду

- `bot/alert_builder.py:442,447` і `bot/taker_builder.py:334` — проміжна
  сторінка на GitHub Pages не потрібна взагалі, щойно шаблони підтвердяться.
- `bot/maker_builder.py:177` — лінк Bybit гарантовано мертвий для застосунку.
- `core/analytics/merchant_profile.py:68-72` — шаблони ордерів на всіх біржах
  вказують на web-шляхи, не покриті App Links.

---

## Підтверджено на пристрої

Прогін `test_deeplinks.ps1` через `adb shell am start -W`:

| Біржа | Лінк | Результат |
|---|---|---|
| Binance | `https://app.binance.com/fiat/ads/detail?adNo=<ID>` | ✅ `FirstDispatchRouterActivity` |
| Binance | `https://app.binance.com/p2p/advertiserProfile?advertiserNo=<ID>` | ✅ `FirstDispatchRouterActivity` |
| Binance | `https://app.binance.com/p2p/orderDetail?orderNo=<ID>` | ✅ `FirstDispatchRouterActivity` |
| Binance | `bnc://app.binance.com/...` (ті самі шляхи) | ✅ |
| Binance | `https://p2p.binance.com/en/trade/detail/<ID>` | ❌ браузер |
| OKX | `okx://exchange/p2p/order?id=<ID>` | ✅ `OrderDetailActivity` |
| OKX | `okx://exchange/p2p/profile?userId=<ID>` | ✅ `HomePageActivity` |
| OKX | `https://www.okx.com/p2p/order/<ID>` | ❌ браузер |
| Bybit | `https://app.bybit.com/inapp/p2p/order/<ID>` | ✅ `MainActivity` |
| Bybit | `bybitapp://open?page=p2pOrderDetail&orderId=<ID>` | ✅ `MainActivity` |
| Bybit | `https://www.bybit.com/uk-UA/p2p/order/<ID>` | ❌ браузер |

---

## Обмеження Telegram, яке визначає архітектуру

`InlineKeyboardButton(url=...)` приймає лише `http/https/tg`. Custom scheme у
кнопку покласти не можна — Telegram відхилить повідомлення. Звідси:

- **Binance, Bybit** — проміжна сторінка більше не потрібна взагалі, кнопка
  веде прямо на `app.binance.com` / `app.bybit.com`.
- **OKX** — прокладка лишається, бо жодного https-маршруту на P2P у нього
  немає. Але тепер вона знає точну ціль і редіректить одразу, без перебору
  кандидатів і без 5-секундного очікування.

---

## Що змінено в коді

- `bot/deeplinks.py` — новий модуль, єдине джерело правди.
- `redirect.html` — переписано: одна відома ціль замість перебору схем.
- `bot/alert_builder.py`, `bot/taker_builder.py` — кнопка «App» більше не
  йде через GitHub Pages для Binance/Bybit.
- `bot/maker_builder.py` — мертвий Bybit-шаблон замінено.
- `bot/handlers/trading.py` — після відкриття ордера бот додатково шле окреме
  повідомлення з посиланням у тілі, щоб воно лишилось в історії чату.
- `core/analytics/merchant_profile.py` — вгадані схеми прибрано, функції
  делегують у `bot/deeplinks.py`.
- `tests/test_deeplinks.py` — 41 тест, у т.ч. охорона від повернення мертвих
  шаблонів і перевірка, що в кнопку ніколи не потрапить custom scheme.

---

## Раунд 2: Bybit Flutter + OKX shareCode

### Bybit — маршрути з `libapp.so`

`base.apk` містить лише 260 строк, бо весь код у Flutter AOT-снапшоті
`lib/arm64-v8a/libapp.so` (99 МБ) всередині `split_config.arm64_v8a.apk`.
Звідти дістали справжній формат — **роутинг по шляху, а не по `?page=`**:

```
bybitapp://open/home?tab=1&initialL1Tab=overview
bybitapp://open/discovery/earnings/eventPredict?eventId=
```

Внутрішні маршрути mini-app (схема `by-mini://`):

```
by-mini://p2p/home
by-mini://p2p/order/detail     ← ордер
by-mini://p2p/user/home        ← профіль мерчанта
```

Https-гейтвей приймає закодований диплінк у параметрі `by_dp`:

```
https://app.bybit.com/inapp?by_dp=<urlencoded by-mini://...>
```

Кандидати на ім'я параметра (є в снапшоті): `userId`, `makerUserId`,
`targetUserId`, `accountId`, `orderId`, `itemId`.

**Наслідок:** старий `bybitapp://open?page=p2pOrderDetail&orderId=` з раунду 1
застосунок перехоплював, але роутер такий формат не розуміє — тобто майже
напевно відкривалась головна.

### OKX — профіль мерчанта через shareCode

У dex є маршрут `exchange/merchanthome.com`. Тобто:

```
okx://exchange/merchanthome.com?shareCode=<share_code>
```

Сканер уже збирає цей код у полі `Order.share_code`, нічого нового тягнути не
треба. Старий коментар у коді (`okex://merchanthome.com?shareCode=`) був
близько, але без префікса `exchange/`.

**Важливо про публічний shareCode-лінк.** `https://okx.com/otc/transfer?shareCode=…`
не є диплінком на мерчанта: він 302-редіректить на
`https://www.okx.com/en-us/p2p?shareCode=…`, тобто на загальну вітрину P2P з
реферальною міткою. Застосунок він відкриває, але не на потрібному продавці.

---

## Чого «вердикт APP» НЕ доводить

`am start` показує, який компонент піймав інтент. Але і в Binance
(`FirstDispatchRouterActivity`), і в Bybit (`MainActivity`) це **один диспетчер
на всі маршрути**. Він відповість `APP` навіть тоді, коли не зрозуміє ім'я
параметра і тихо відкриє головну — рівно та поведінка, з якої все почалось.

Тому раунд 1 доводить лише половину: лінк більше не йде в браузер. Що він
приземляється саме на картку ордера, доводить лише скріншот.
Для цього — `test_landing_v2.ps1`: холодний старт застосунку, лінк,
пауза, `screencap`.

---

## Раунд 2: вердикт (скріншоти не знадобились)

Скріншоти вийшли пошкодженими — `adb exec-out screencap -p > file.png` у
PowerShell пише потік як текст і нормалізує переводи рядка неоднозначно
(і `\n`, і `\r`, і `\r\n` стають `\r\n`), тому PNG не відновлюється. У скрипті
виправлено на `adb shell screencap` + `adb pull`.

Але `landing_report.txt` виявився інформативнішим за картинки: Android пише
фактичну **topResumedActivity**, а імена класів говорять самі за себе.

### Binance — обрані шляхи виявились неправильними

```
[1] /fiat/ads/detail?adNo=…   → com.eaas.startup.router.activity.NoSupportRouterPathActivity
[2] /fiat/ads/detail?advNo=…  → NoSupportRouterPathActivity
```

`NoSupportRouterPathActivity` — це буквально «роутер не знає такого шляху».
Тобто диспетчер приймає хост `app.binance.com`, а далі відкидає шлях. Рівно те,
про що йшлося: вердикт `APP` з раунду 1 не означав нічого.

Причина помилки: рядки `/fiat/ads/detail`, `/p2p/advertiserProfile` у dex
належать **іншому** роутеру (внутрішня навігація), а не диплінк-диспетчеру.
Справжній список маршрутів диспетчера — це 177 готових рядків виду
`bnc://app.binance.com/<шлях>`. Серед них немає ані картки оголошення, ані
профілю рекламодавця. Що там для P2P є:

```
/fiat/orderDetails?id=      /fiat/hold        /p2p/chatList
/p2p/groupChat              /trade/trade?at=fiat&symbol=
/mp/app?appId=…&startPagePath=<base64>
/webview/webview?type=default&url=<base64 повного веб-URL>
```

Останній — універсальний шлюз: у `url` кладеться **base64 звичайного
веб-посилання**, і застосунок відкриває його у власному авторизованому
webview. Перевірено на прикладах з dex:
`aHR0cHM6Ly93d3cuYmluYW5jZS5jb20vZml4ZWRMb2Fu` = `https://www.binance.com/fixedLoan`.

Це не нативний екран, але це застосунок, потрібна сторінка і вже залогінена
сесія — на практиці саме те, що потрібно.

Кейси [3]–[8] недостовірні: зверху опинявся то Google Play
(`PlayCoreAcquisitionActivity`), то взагалі Bybit з попереднього прогону —
диспетчер Binance миттєво закривав себе, і наверх спливала стара активність.

### OKX — ордер підтверджено остаточно

```
[13] okx://exchange/p2p/order?id=…      → com.okinc.p2p.order.detail.OrderDetailActivity
[14] okx://exchange/p2p/order?orderId=… → com.okinc.p2p.order.detail.OrderDetailActivity
```

Ім'я класу не залишає простору для тлумачень — це екран деталей ордера.
Обидва імені параметра працюють.

```
[11][12] okx://exchange/p2p/profile?userId= / ?publicUserId=
         → com.okinc.p2p.trade.HomePageActivity
```

Тут гірше: `HomePageActivity` у пакеті `p2p.trade` — це вітрина P2P, а не
картка продавця. Профіль мерчанта поки не підтверджений.

```
[9]  okx://exchange/merchanthome.com?shareCode= → SchemeActivity, LaunchState UNKNOWN(0)
[10] okx://exchange/merchanthome?shareCode=     → …ok_app.homepage.pro.MainActivity
```

Тобто гіпотеза про `merchanthome` не спрацювала: перший варіант не дорулив
нікуди, другий відкрив головну застосунку.

### Bybit — нерозв'язно за логом

Усі 11 варіантів (`bybitapp://open/p2p/...`, `by_dp`, `/inapp/...`) дали
`com.bybit.app/com.bybit.pro.MainActivity`, бо у Flutter одна Activity на весь
застосунок. Без робочих скріншотів сказати нічого не можна.

Тому в раунді 3 скрипт додатково знімає:

- `uiautomator dump` — текст, реально видимий на екрані (для Binance/OKX цього
  досить, щоб упізнати екран без картинки);
- `adb logcat` з фільтром по route/deeplink/by-mini — Flutter-роутер логує
  перехід, і це єдиний надійний спосіб побачити, куди він пішов;
- гасяться всі три застосунки перед кожним кейсом, бо в раунді 2 зверху
  лишалась чужа активність з попереднього прогону і псувала вимір.

---

## Раунд 3: webview-шлюз спрацював, але лишилось вузьке місце

Кнопка «Поділитися» в застосунку Binance віддає короткий лінк:

```
https://www.binance.com/uk-UA/qr/dplk393b631d811449868289e4123ae90536
```

Напряму він іде в браузер — хоста `www.binance.com` у манифесті немає.
Обгорнутий у webview-шлюз він приземлився на
`com.binance.c2c.main.FiatMainActivity`, тобто нативний C2C-модуль:

```
https://app.binance.com/webview/webview?type=default&url=<base64 dplk-лінка>
```

**Вузьке місце:** рядка `dplk` у dex немає взагалі. Отже код генерується на
сервері під конкретне оголошення, і зібрати його самому неможливо. Один
захардкоджений dplk у боті нічого не дає — потрібен свій код на кожне
оголошення.

Розв'язок є. У dex лежать три приватні ендпоінти, які цей код і видають:

```
/bapi/c2c/v1/private/c2c/share/adv-share          — оголошення
/bapi/c2c/v1/private/c2c/share/advertiser-share   — профіль мерчанта
/bapi/c2c/v1/private/c2c/share/adv-search-share   — пошукова видача
```

Вони приватні, але авторизована сесія Binance у бота вже є (`auth_sessions`,
`SessionManager._try_binance_refresh`). Тобто ланцюг замикається:

```
сканер знайшов оголошення
  → POST adv-share з номером оголошення
  → сервер віддав dplk
  → обгортка у webview-шлюз
  → кнопка в Telegram
  → нативний екран у застосунку
```

Лишилось зафіксувати контракт ендпоінта — імена полів запиту і форму
відповіді. Для цього `probe_binance_share.py`: бере наявну сесію, пробує
кілька варіантів іменування, друкує сирі відповіді.

### Застереження щодо FiatMainActivity

`FiatMainActivity` — це головна C2C-модуля. Чи показала вона саме картку
оголошення, чи вітрину P2P, з імені активності не видно: всередині можуть бути
фрагменти. Оскільки лінк був справжній share-лінк конкретного оголошення,
логічно очікувати картку — але це поки очікування, а не факт. Підтвердить
`uiautomator dump` у раунді 3.

---

## Поточний стан коду

`bot/binance_share.py` — клієнт приватних share-ендпоінтів: бере сесію з
`auth_sessions`, кешує dplk (6 год на успіх, 10 хв на невдачу, щоб не довбати
ендпоінт), ніколи не кидає виняток назовні. Імена полів запиту фіксуються
після прогону `probe_binance_share.py` — константа `_REQUEST` угорі файлу.

`tg_button_url_async()` у `bot/deeplinks.py` реалізує три рівні деградації:

| Рівень | Лінк | Куди приводить |
|---|---|---|
| 1 | dplk у webview-шлюзі | нативний екран застосунку |
| 2 | веб-URL у webview-шлюзі | потрібна сторінка в застосунку, вже авторизовано |
| 3 | звичайний веб-URL | браузер |

Рівень 2 не потребує жодного мережевого виклику, тому кнопка працює навіть
якщо приватний ендпоінт відвалиться або Binance перейменує поля. Перевірено:
виклик без БД тихо дає рівень 2, а не помилку.

Маршрути `/fiat/ads/detail`, `/p2p/advertiserProfile`, `/p2p/orderDetail` з
коду прибрані — прогін дав на них `NoSupportRouterPathActivity`. Лишився
єдиний підтверджений нативний маршрут Binance: `/fiat/orderDetails?id=`
(він є у списку 177). Тест `test_binance_never_uses_rejected_routes` не дасть
їм повернутись.

Підсумкова таблиця того, що бот шле зараз:

| Біржа | Екран | Лінк | Стан |
|---|---|---|---|
| Binance | оголошення / профіль | webview-шлюз (+dplk коли зонд підтвердить) | ✅ у застосунку |
| Binance | ордер | `app.binance.com/fiat/orderDetails?id=` | ✅ нативний |
| OKX | ордер | `okx://exchange/p2p/order?id=` через redirect.html | ✅ `OrderDetailActivity` |
| OKX | профіль | `okx://exchange/p2p/profile?userId=` | ⚠️ вітрина, не картка |
| Bybit | ордер | `app.bybit.com/inapp/p2p/order/<id>` | ⚠️ екран не підтверджено |
| Bybit | профіль | `bybitapp://open/p2p/user/home?userId=` | ⚠️ екран не підтверджено |

---

## Раунд 4: share-ендпоінти OKX і Bybit

Ідея шукати share-API замість реверсу роутера себе виправдала — але
асиметрично.

### OKX — знайдено, і це може закрити питання повністю

```
/v3/c2c/merchant/share          — поділитися мерчантом
/v3/c2c/merchant/sharedInfo     — дані вже створеного share
/v3/c2c/tradingOrders/share     — поділитися ордером
```

Чому це принципово. У манифесті OKX зареєстрований шлях **`/ul/.*`** —
короткі універсальні лінки виду `https://www.okx.com/ul/m3w2P7`. Це єдиний
P2P-придатний **https**-шлях, а значить його можна класти прямо в
`InlineKeyboardButton` — без `redirect.html`, без custom scheme.

Якщо `merchant/share` повертає саме `/ul/`-лінк, то:

- профіль мерчанта OKX закривається (зараз `p2p/profile` веде на вітрину);
- проміжна сторінка для OKX стає непотрібною зовсім.

Перевіряє `probe_okx_share.py` — він окремо підсвічує наявність `/ul/`
у відповіді. Сесія береться так само, як у `SessionManager` для
`/v3/c2c/review/history`.

Клієнт готовий: `bot/okx_share.py`, симетричний до `binance_share.py` —
кеш 6 год / 10 хв, ніколи не кидає виняток, `_extract_ul()` приймає **лише**
`/ul/`-лінки (інші застосунок не перехопить). Підключений у
`tg_button_url_async`. Після прогону зонда лишиться звірити імена полів у
`_REQUEST`.

> Статус гіпотези: те, що `/ul/.*` зареєстрований у манифесті — факт,
> перевірений у `apple-app-site-association` і `AndroidManifest`. А те, що
> `merchant/share` віддає саме `/ul/`-лінк — поки припущення. Підтвердить
> зонд; якщо ні, OKX лишиться на рівні 2 (redirect.html), який працює вже
> зараз.

### Bybit — тупик, share-API немає

Прочесав `libapp.so` по всіх варіантах `share`: у P2P-модулі немає нічого.
Є `pnl_share`, `roi_share`, `spot_share`, `post/share`, `share-trade-data` —
тобто шеринг PnL, постів і графіків, але не оголошення чи профілю. Це
збігається з тим, що ти сам побачив: у мобільному Bybit просто немає кнопки
«Поділитися профілем».

Отже для Bybit share-шляху не існує в принципі, і залишається або підтвердити
`by-mini://p2p/user/home` перебором імені параметра, або визнати, що профіль
Bybit відкривається лише у вебі.

Ще одна деталь з `libapp.so`: у частини маршрутів параметри записані явно —
`by://contract_copy_trade/index{previewId?}`,
`by://trading/copy_trading/orderTpSl{symbol?,orderId?,orderInfoJson?}`.
Для `p2p/user/home` такого запису немає, тобто ім'я параметра лишається
здогадкою і перевіряється тільки прогоном.

---

## Раунд 5: живий тест на телефоні

### Binance — «Це посилання не працює»

Діалог у застосунку — це той самий `NoSupportRouterPathActivity`, лише в
людському вигляді. Причина не в маршрутах, а в розсинхроні: `redirect.html`
відкотився на старі шляхи (`bnc://app.binance.com/fiat/ads/detail`,
`/p2p/advertiserProfile`), які вже прибрані з `bot/deeplinks.py`.

Виправлено. У `redirect.html` тепер:

- Binance без нативного маршруту віддає **webview-шлюз одразу** — це
  звичайний https, тому ніякого intent і ніякого очікування;
- у коді стоїть список заборонених шляхів з поясненням, щоб вони не
  повернулись при наступному редагуванні.

### OKX — це насправді успіх, а не провал

«Чорний екран і неправильний ID ордера» означає, що застосунок **дійшов до
екрана деталей ордера** і поскаржився на вміст. ID у прогоні був вигаданий —
`20512345678901234567`. Тобто маршрут працює; помилка від фейкового id.

Це узгоджується з логом раунду 2, де той самий лінк дав
`com.okinc.p2p.order.detail.OrderDetailActivity`. З реальним id ордера екран
має відкритись нормально.

### Bybit — вічний спінер

Дві причини, обидві виправлені:

1. для профілю `schemeUrl()` повертав порожній рядок, і сторінка лишалась
   крутити спінер замість того, щоб піти у веб;
2. для ордера використовувався старий формат `bybitapp://open?page=…`, а
   роутер Bybit працює по шляху — `bybitapp://open/p2p/order/detail?orderId=`.

Додано ранній вихід: якщо маршруту немає, сторінка одразу йде у веб або
показує кнопки, а не висить.

---

## Раунд 6: webview-шлюз відпав. Binance у глухому куті без dplk

Експеримент дав відповідь на контрольному кейсі:

```
[1] control: www.binance.com/fixedLoan у шлюзі (адреса дослівно з dex)
    → NoSupportRouterPathActivity
```

Впала **адреса, взята з dex дослівно**. Отже справа не в білому списку
доменів, а в самому шлюзі: `/webview/webview` обробляє лише внутрішні
переходи всередині застосунку і не приймає зовнішні інтенти. Гіпотеза про
`www` не підтвердилась — вона була неправильна.

> Прогін неповний: у звіті лише 2 кейси з 5, скріншот один. Кейс
> `www-advertiserDetail` не виконався. На висновок це не впливає — контроль
> вирішує, — але доганяти решту сенсу вже немає.

### Що це означає насправді

Поточний фолбек — `https://p2p.binance.com/en/advertiserDetail?advertiserNo=…`.
Цей хост у манифесті **не зареєстрований**, тому він відкриває **браузер**.
Тобто для оголошення і профілю Binance ми повернулись рівно до тієї точки, з
якої почали.

Триступеневої деградації для Binance більше немає. Є два стани:

| | Оголошення / профіль | Ордер |
|---|---|---|
| з dplk | нативний екран | `app.binance.com/fiat/orderDetails?id=` ✅ |
| без dplk | **браузер** | те саме ✅ |

**dplk перестав бути покращенням і став єдиним шляхом.** Приватний ендпоінт
`adv-share` — тепер критичний шлях, а не приємний бонус. А він віддає `401`,
бо сесія Binance протухла.

### Відкрите протиріччя, яке треба закрити

Раніше зафіксовано, що dplk, обгорнутий у шлюз, приземлився на
`FiatMainActivity`. Але зараз контроль показує, що шлюз відкидає все.
Одночасно правдивими ці два факти бути не можуть. Або:

- шлюз має окрему обробку саме для `dplk`, або
- те вимірювання було забруднене, як у раунді 2, де зверху опинялась чужа
  активність з попереднього кейса.

Щойно сесія оновиться, це перевіряється двома командами — голий dplk проти
обгорнутого:

```
adb shell am start -a android.intent.action.VIEW -d "https://www.binance.com/uk-UA/qr/dplk<hash>"
adb shell am start -a android.intent.action.VIEW -d "https://app.binance.com/webview/webview?type=default&url=<base64 того самого>"
```

Якщо голий працює — обгортка не потрібна взагалі, і код спрощується.

### Порядок дій

1. Оновити сесію Binance (`SessionManager`) — без цього все інше марне.
2. `probe_binance_share.py` — зафіксувати контракт `adv-share`.
3. Дві команди вище — з'ясувати, чи потрібна обгортка.

---

## Раунд 7: OKX закрито остаточно

```
GET /v3/c2c/merchant/share?pubUserId=005977cb24
→ {"shareCode": "AysnnUZlACimN",
   "qrCode": "https://okx.com/ua/p2p?action=otcTransfer&shareCode=AysnnUZlACimN"}

adb: okx://exchange/merchanthome.com?shareCode=AysnnUZlACimN
→ com.okinc.p2p.userinfo.profile.UserProfilePageActivity
```

Нативна картка продавця. Питання профілю OKX закрите.

Виправлення попереднього хибного висновку: у раунді 2 я записав `merchanthome`
як непрацюючий. Насправді маршрут робочий — тест був з реферальним кодом
`29849204` замість справжнього shareCode. Помилка була в даних, не в маршруті.

Два уточнення до контракту, які змінили код:

- **Це GET із query-параметром**, а не POST з JSON. `okx_share.py` виправлено.
- **Брати треба `shareCode`, а не `qrCode`.** `qrCode` веде на `/ua/p2p`, а цей
  шлях у манифесті не зареєстрований — тобто відкриє браузер. Гіпотеза про
  `/ul/` теж не підтвердилась: ендпоінт його не повертає.

Схему не можна класти в кнопку Telegram, тому код їде параметром `code` у
`redirect.html`, а сторінка вже будує з нього схему. Тобто **деплой сторінки
на GitHub Pages обов'язковий** — без нього профіль OKX не працюватиме.

## Binance: чому 401 при зеленій сесії

`SessionManager` показує `🟢 OK`, бо перевіряє публічний ендпоінт — цього
досить сканеру, щоб бачити стакан. Але `/bapi/c2c/v1/private/…` вимагає входу
в персональний акаунт. Візитерські куки для нього не годяться, звідси
`401 Please log in first`.

Наслідок неприємний і його варто називати прямо: **поки бот не має
авторизованої сесії особистого акаунта Binance, оголошення і профіль
відкриваються тільки в браузері.** Не «трохи гірше» — а рівно те, з чого все
починалось. Нативний екран лишається доступним лише для ордера
(`app.binance.com/fiat/orderDetails?id=`), бо він не потребує dplk.

Вибір тут продуктовий, не технічний:

| Варіант | Що дає | Ціна |
|---|---|---|
| Залогінитись у Binance у браузері, щоб `SessionManager` підхопив токен | dplk і нативний екран | сесія протухає, треба поновлювати |
| Лишити як є | браузер для оголошення і профілю | нічого не робити |

---

## Раунд 8: мініаппа P2P — обхід і dplk, і логіну

Замість того щоб добувати сесію, перевірив, чи є в застосунку інший шлях до
тих самих екранів. Виявилось, що є.

Усередині Binance живе мініаппа `appId = Bzp9defeaRgNqhgV4wEG5C`, і в ній
рівно ті сторінки, яких бракує:

```
pages/merchant-detail/index    профіль мерчанта
pages/ads/index                оголошення
pages/order-detail/index       деталі ордера
pages/order-list/index
pages/profile/index
```

Маршрут `/mp/web` **входить до списку 177 підтверджених шляхів диспетчера**.
Тобто це нативний перехід, який не потребує ані dplk, ані входу в особистий
акаунт — а отже обходить і `401`, і всю історію з сесіями.

Формат (узятий з dex дослівно, шлях і запит окремими параметрами в base64):

```
https://app.binance.com/mp/web
    ?appId=Bzp9defeaRgNqhgV4wEG5C
    &startPagePath=<base64 "pages/merchant-detail/index">
    &startPageQuery=<base64 "advertiserNo=…&fromNative=true">
```

Реалізація в `binance_miniapp_url()` перевірена найсильнішим можливим
способом: зібраний нею URL **побайтово збігається з рядком, що лежить у dex**
(тест `test_binance_miniapp_url_matches_dex_literal`).

Невідоме лишилось одне — як зветься параметр з id. `test_binance_miniapp.ps1`
перебирає `advertiserNo`, `merchantNo`, `userNo`, `advertiserId`, `id` і знімає
текст з екрана, тому ім'я мерчанта на скріншоті одразу скаже, який вгадано.

Контрольний кейс у скрипті — сторінка без параметрів. Якщо і вона не
відкриється, значить мініаппа не приймає зовнішні інтенти і шлях закритий;
тоді лишається тільки логін і dplk.

Чого шукав і не знайшов, щоб не шукали вдруге:

- публічного (`/friendly/`) аналога share-ендпоінта немає — тільки `/private/`;
- маршруту `/qr/<code>` на `app.binance.com` у dex немає, `dplk` резолвиться
  на сервері `www.binance.com`.

---

## Раунд 9: справжня причина — серверний білий список

Мініаппа теж дала `NoSupportRouterPathActivity`. Отже це вже не збіг, і я
пішов шукати не наступний маршрут, а сам механізм. Знайшов у dex:

```
externalDeeplinkAllows      externalDeeplinkBlocks
deeplink_allow_index        deeplink_block_index
isDeeplinkAllowed           deeplink_blocked_by_scene
supportsExternalLink
```

і ключі віддаленого конфігу:

```
android_nezha_enable_external_deeplink_allowed_v2
android_nezha_enable_external_deeplink_blocked_v2
```

**Binance має явний білий список зовнішніх диплінків, і керується він з
сервера.** Самих списків в APK немає — вони приходять з їхньої системи
конфігів `nezha`.

Це пояснює все попереднє одним реченням: `/webview/webview`, `/mp/web` і
картка оголошення відхиляються **не через неправильне ім'я параметра чи
шляху**, а тому що їх немає серед дозволених для зовнішнього входу. Перебір
маршрутів був приречений з самого початку — це не проблема іменування, а
навмисний контроль. Рівно від того, що ми намагаємось зробити.

Тому й `dplk` працює: короткий share-лінк — це **санкціонований** вхід, і
резолвиться він на сервері, а не в роутері застосунку.

### Що з цього ще можна витягнути

У dex є рядки `deeplink allowed ` і `deeplink denied ` — застосунок логує своє
рішення. Отже список можна не вгадувати, а прочитати з `logcat`. Це робить
`dump_binance_allowlist.ps1`: проганяє кілька диплінків і збирає рядки логу
про рішення роутера. Якщо пощастить, у логах буде і сам
`externalDeeplinkAllows=[…]`.

Якщо логування вимкнене в релізному збиранні — значить список недоступний, і
питання закрите остаточно.

### Що потребує чесної перевірки

Твердження «ордери 100% нативні» досі **не підтверджене**. Єдиний прогін
`/fiat/orderDetails?id=` був у раунді 2, і там зверху опинилась чужа
активність — вимір недійсний. `autoVerify=true` доводить лише те, що домен
верифіковано, але не що шлях у білому списку. Цілком можливо, що і він
відхиляється.

`dump_binance_allowlist.ps1` перевіряє його першим, у чистих умовах.

### Підсумок по Binance

| Шлях | Стан |
|---|---|
| dplk (share-лінк) | єдиний підтверджений вхід, потребує логіну |
| `/fiat/orderDetails?id=` | не перевірено чисто |
| усе інше | відхиляється білим списком |

---

## Раунд 10: фінальний стан

`dump_binance_allowlist.ps1` підтвердив найгірший з варіантів: **навіть
`/fiat/orderDetails` відхиляється** при зовнішньому переході. Отже у Binance
не лишилось жодного маршруту, крім dplk.

Це був не косметичний висновок, а баг у коді: `app_https_url` і
`app_scheme_url` досі повертали для ордера
`app.binance.com/fiat/orderDetails?id=`, тобто бот генерував кнопку, яка
гарантовано впаде в «Це посилання не працює». Обидві функції для Binance
тепер повертають порожньо, а `tg_button_url_async` пробує dplk і для ордера
теж. Тест `test_binance_has_no_external_route_at_all` не дасть маршрутам
повернутись «за списком 177» — присутність шляху в застосунку не означає
дозвіл ззовні.

### Що бот шле зараз (перевірено `show_links.py`)

| Біржа | Екран | Кнопка | Куди веде |
|---|---|---|---|
| OKX | профіль | redirect.html + shareCode | нативна картка продавця ✅ |
| OKX | ордер | redirect.html + `okx://…/p2p/order` | нативний екран ордера ✅ |
| Bybit | ордер | `app.bybit.com/inapp/p2p/order/<id>` | застосунок, екран не підтверджено ⚠️ |
| Bybit | профіль | redirect.html + `bybitapp://…/p2p/user/home` | застосунок, екран не підтверджено ⚠️ |
| Binance | усе | канонічний веб-URL | браузер ❌ |

Binance переходить у зелене автоматично, щойно з'явиться авторизована сесія:
`tg_button_url_async` спершу питає dplk і лише потім падає на веб.

### Чого не варто робити далі

Шукати обхідний маршрут для Binance. Білий список серверний
(`android_nezha_enable_external_deeplink_allowed_v2`), тобто це навмисний
захист саме від такого сценарію. Будь-який знайдений шлях або вже в списку,
або буде відхилений — і третього не дано. Єдиний легальний вхід —
share-лінк, а він потребує входу в акаунт.

---

## Найкоротший шлях до істини

Замість подальшого перебору — взяти готовий лінк у самого застосунку:
відкрити оголошення в мобільному Binance → **Поділитися** → скинути собі URL.
Те саме в Bybit. Це те, що застосунок генерує для себе, тобто гарантовано
правильний шлях і правильні імена параметрів. Хвилина роботи проти ще одного
раунду вгадування.

---

## Лишилось

- **Bybit-профіль мерчанта** — маршруту поки немає. Bybit на Flutter,
  таблиця маршрутів у AOT-снапшоті `libapp.so` всередині
  `split_config.arm64_v8a.apk`, у dex її немає.
- **iOS** — усі перевірки робились на Android. Для Binance i Bybit https-лінки
  мають працювати і там (шляхи збігаються з AASA), для OKX — треба окремо
  перевірити, чи ловить застосунок `okx://` з Safari.
