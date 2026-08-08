"""
Особистий чорний список — застосування в конвеєрі сканера.

Чому це окремо від global_blacklist: спільний список наповнюють ризик-движок
і адміністратор, і він діє на всіх. Вердикт по мерчанту рахується один раз на
цикл і кешується, тому вплетати туди «мені особисто цей не подобається»
означало б рахувати скоринг стільки разів, скільки в системі користувачів.

Тому персональний шар живе тут: він застосовується там, де user_id уже
відомий — в AlertDispatcher (режим SPREAD) і TakerScanner (TAKER_BUY/SELL).
Індекси приходять з MerchantRepo._user_blacklist_index і читаються з кешу,
щоб перевірка кожного ордера не била в базу.
"""
from __future__ import annotations


def clean_merchant_name(value: str) -> str:
    """
    Ім'я без емодзі, пробілів і розділових.

    Мерчанти регулярно переписують нік, лишаючи ті самі букви: «Ivan ⚡️» і
    «i-v-a-n» — та сама людина. Порівняння по очищеному рядку ловить це, а
    точний збіг — ні.
    """
    if not value:
        return ""
    return "".join(c for c in str(value).lower() if c.isalnum())


def in_personal_blacklist(order, by_id: dict, by_name: dict) -> bool:
    """
    Чи забанив цього мерчанта сам користувач.

    `by_id` ключується парою (біржа в нижньому регістрі, merchant_id),
    `by_name` — очищеним іменем без прив'язки до біржі: помічений на одній
    майданчику мерчант ховається всюди, так само як у спільному списку.
    """
    if not by_id and not by_name:
        return False

    exchange = str(getattr(order, "exchange", "")).lower()
    merchant_id = str(getattr(order, "merchant_id", ""))
    if (exchange, merchant_id) in by_id:
        return True

    cleaned = clean_merchant_name(getattr(order, "merchant_name", "") or "")
    return bool(cleaned) and cleaned in by_name
