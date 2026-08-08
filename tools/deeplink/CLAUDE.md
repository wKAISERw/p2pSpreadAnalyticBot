# Бриф для Claude Code: диплінки P2P-бірж

Прочитай це перед будь-якою роботою з диплінками. Тут зафіксовано те, що вже
перевірено на реальному пристрої — щоб не повторювати кола, які ми вже пройшли.

Повний розбір: `tools/deeplink/FINDINGS.md`.
Джерело правди для коду: `bot/deeplinks.py`.

---

## Головне правило

**Вердикт `APP` від `adb shell am start` нічого не доводить.**

І Binance (`FirstDispatchRouterActivity`), і Bybit (`MainActivity`) — це один
диспетчер на всі маршрути. Він відповість `APP` навіть коли не зрозуміє шлях і
покаже головну або діалог «Це посилання не працює». Доводить лише
`topResumedActivity` з осмисленим ім'ям класу або скріншот.

---

## Що НЕ можна повертати в код

Перевірено, дає `NoSupportRouterPathActivity`:

```
bnc://app.binance.com/fiat/ads/detail
bnc://app.binance.com/p2p/advertiserProfile
bnc://app.binance.com/p2p/userProfile
bnc://app.binance.com/fiat/merchant/details
bnc://app.binance.com/p2p/orderDetail
https://app.binance.com/<ті самі шляхи>
https://app.binance.com/webview/webview   серверний білий список ріже і його
bybitapp://open?page=…                    роутер Bybit працює по шляху
bybitapp://open/p2p/…                     такого маршруту в таблиці немає
okx://exchange/p2p/profile?userId=…       відкриває ВЛАСНИЙ профіль
by-mini://p2p/user/home?<будь-що>=…       те саме: власний профіль
```

Останні два — одна й та сама пастка на двох біржах: маршрут спрацьовує, id
мовчки ігнорується, людина бачить свій профіль замість продавця. Гірше за
браузер, бо виглядає як помилка бота.

Тест `tests/test_deeplinks.py::test_binance_never_uses_rejected_routes`
стереже це. Якщо він упав — ти щось відкотив.

---

## Що підтверджено

**Спочатку перевір, чи не вирішується задача офіційною https-обгорткою біржі** —
вона є і в OKX (`/download?…&deeplink=`), і в Binance (`/download?_dp=`), працює
без сесії й без проміжної сторінки. Саме через неї відкриваються нативні картки
мерчантів. Деталі — у `HANDOFF.md`, пункт «0-БІС».

| Що | Лінк | Доказ |
|---|---|---|
| Binance профіль | `app.binance.com/en/download?_dp=<b64 /p2p/advertiserProfile?advertiserNo=>` | `FiatMerchantDetailsActivity` |
| OKX профіль | `www.okx.com/download?pageSource=p2p&deeplink=<okx://…>` | `UserProfilePageActivity` / `WebActivity` |
| OKX ордер | `okx://exchange/p2p/order?id=<id>` | `com.okinc.p2p.order.detail.OrderDetailActivity` |
| OKX профіль | `okx://app/web?url=<веб-картка>` | картка мерчанта, **без сесії** |
| OKX профіль (нативно) | `okx://exchange/merchanthome.com?shareCode=<код>` | `UserProfilePageActivity`, потребує сесії |
| Binance оголошення | `adv-share` → dplk | скріншот на пристрої |
| Binance профіль | `advertiser-share` → dplk | скріншот на пристрої |
| Bybit профіль | `bybitapp://open/web?url=<enc веб-профіль>` | скріншот: картка потрібного мерчанта |
| Bybit ордер | `bybitapp://open/fiat_otc_order_detail?orderId=<id>` | декларований маршрут, реальним id не підтверджений |

Мертві контролі: `p2p.binance.com/en/trade/detail/…`,
`www.okx.com/p2p/order/…`, `www.bybit.com/uk-UA/p2p/order/…` — усі в браузер.

---

## Обмеження, які визначають архітектуру

1. **Telegram-кнопка приймає лише http/https.** Custom scheme у
   `InlineKeyboardButton` покласти не можна — повідомлення буде відхилено.
   Тому OKX ходить через `redirect.html`, а Binance — через webview-шлюз.
2. **`adb exec-out screencap -p > file.png` у PowerShell руйнує PNG.**
   Потік пишеться як текст, переводи рядка нормалізуються неоднозначно,
   відновити неможливо. Тільки `adb shell screencap` + `adb pull`.
3. **Перед кожним кейсом гасити всі три застосунки.** Інакше зверху
   лишається активність з попереднього кейса і вимір бреше.
4. **`am start` ≠ тап у Telegram.** Вбудований браузер TG обробляє посилання
   інакше. Фінальна перевірка завжди за людиною.

---

## Що лишилось зробити

1. `python -m tools.deeplink.probe_binance_share --adv-no <id> --advertiser-no <id>`
   — зафіксувати імена полів у `bot/binance_share.py::_REQUEST`.
   Потрібна свіжа сесія Binance: раніше віддавав 401.
2. `python -m tools.deeplink.probe_okx_share --pub-user-id <id> --order-id <id>`
   — перевірити, чи `merchant/share` віддає `/ul/`-лінк. Якщо так,
   `redirect.html` для OKX стає непотрібним.
3. ~~Bybit профіль: перебрати ім'я параметра~~ — зроблено, маршруту не існує.
   Рішення через `bybitapp://open/web`, деталі в `HANDOFF.md`. Лишився ордер
   Bybit: маршрут покладений у код, але потрібен реальний `orderId`.
4. Перевіряти все з **реальними** id. Скарга «неправильний ID ордера» в раунді
   5 означала, що маршрут спрацював, а id був вигаданий.

---

## Деплой

`redirect.html` живе на GitHub Pages. У ньому є константа `VERSION` — підіймай
її при кожній зміні маршрутів. Перевірити, що саме задеплоєно:

```
https://wkaiserw.github.io/p2pSpreadAnalyticBot/redirect.html?debug=1
```

Сторінка покаже версію замість редіректу. Якщо версія стара — проблема в
деплої, а не в маршрутах. GitHub Pages кешує, оновлення може зайняти хвилину.
