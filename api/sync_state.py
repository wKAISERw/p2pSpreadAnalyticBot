# api/sync_state.py
"""
Відбитки стану по розділах — щоб дашборд перечитував лише те, що змінилось.

Досі відкрита вкладка опитувала всі десять розділів на кожному тіку: при
інтервалі 10 секунд це 60 HTTP-запитів на хвилину, з яких майже всі
повертали ті самі дані. Кожна відповідь ще й перемальовувала панель, бо SWR
віддає новий об'єкт незалежно від того, чи змінився вміст.

Тут рахується коротка контрольна сума кожного розділу. Дашборд тягне один
дешевий запит, порівнює її з тим, що має, і йде по дані лише для тих
розділів, де сума інша. Заразом стає відомо, ЩО саме змінилось — інакше
повідомити людину про зміну було б нічим.

Чому контрольна сума на читанні, а не лічильник на записі: записів у боті
десятки місць, і кожне довелося б не забути. Пропущений інкремент — це
розділ, який мовчки перестає оновлюватись, тобто рівно той клас дефекту,
на якому цей проєкт уже спіткнувся шість разів. Читання ж завжди бачить
поточний стан, скільки б нових шляхів запису не з'явилось.
"""
from __future__ import annotations

import hashlib
import json
import logging
from typing import Any, Iterable, Optional

logger = logging.getLogger(__name__)

# Розділи рівно ті, що в перемикачах дашборда (hooks/useBotSync.ts).
SECTIONS: tuple[str, ...] = (
    "filters", "taker", "merchant", "cards", "limits",
    "display", "sniper", "features", "blacklist", "exchanges",
)

# Колонки scanner_users, що належать кожному розділу. Хешувати рядок цілком
# не можна: тоді зміна капіталу «оновлювала» б і снайпер, і пороги мерчанта,
# і людина бачила б повідомлення про зміни, яких не було.
_USER_COLUMNS: dict[str, tuple[str, ...]] = {
    "filters": (
        "capital", "capital_mode", "min_amount", "min_spread", "max_spread",
        "spread_strategy", "bank_codes", "buy_bank_codes", "sell_bank_codes",
        "scanner_mode", "scanner_modes", "is_alerts_active", "bank_scopes",
    ),
    "taker": (
        "taker_sell_amount", "taker_sell_price", "taker_sell_exchange",
        "taker_sell_profit", "taker_sell_min_price", "taker_sell_speed",
        "taker_sell_price_strategy", "taker_sell_price_to",
        "taker_buy_amount", "taker_buy_max_price", "taker_buy_limit_min",
        "taker_buy_limit_max", "taker_buy_speed", "taker_buy_price_strategy",
        "taker_buy_price_from", "buy_balance_mode",
        "buy_auto_scale_down", "buy_auto_scale_up",
        "target_margin", "maker_buy_price",
    ),
    "merchant": (
        "min_orders", "min_finish_rate", "min_positive_rate", "verified_only",
        "min_account_age_days", "max_last_online_mins", "merchant_thresholds",
        "hide_new_user_subsidy",
    ),
    "sniper": ("sniper_rules",),
    "display": (
        "alert_fields", "alert_mode", "mute_until", "auto_cooldown",
        "price_range_mode", "price_range_from", "price_range_to",
    ),
}

# Розділи, стан яких лежить в окремих таблицях.
_TABLE_QUERIES: dict[str, tuple[str, str]] = {
    "cards": (
        "SELECT id, bank_name, last_four, label, category, status, balance,"
        " is_custom_limits, limits_override_json, is_warmed_up, cooldown_until,"
        " last_tx_timestamp, mono_tracker_enabled"
        " FROM cards WHERE owner_id = ? ORDER BY id",
        "owner",
    ),
    "limits": (
        "SELECT * FROM user_bank_limits WHERE user_id = ? ORDER BY bank_name",
        "user",
    ),
    "features": (
        "SELECT feature_key, is_enabled FROM user_features"
        " WHERE user_id = ? ORDER BY feature_key",
        "user",
    ),
    "blacklist": (
        "SELECT exchange, merchant_id, merchant_name FROM user_blacklist"
        " WHERE owner_id = ? ORDER BY exchange, merchant_id",
        "owner",
    ),
    "exchanges": (
        "SELECT exchange, label, updated_at FROM user_credentials"
        " WHERE user_id = ? ORDER BY exchange",
        "user",
    ),
}


def _digest(payload: Any) -> str:
    """Коротка стабільна сума. Не криптографія — лише «те саме чи ні»."""
    raw = json.dumps(payload, sort_keys=True, default=str, ensure_ascii=False)
    return hashlib.blake2s(raw.encode("utf-8"), digest_size=8).hexdigest()


def _from_user(user: dict, columns: Iterable[str]) -> str:
    # Відсутня колонка й порожня — те саме: важлива лише зміна значення.
    return _digest({c: user.get(c) for c in columns})


async def _from_table(db, sql: str, user_id: int) -> Optional[str]:
    try:
        async with db._db.execute(sql, (user_id,)) as cur:
            rows = await cur.fetchall()
        return _digest([tuple(r) for r in rows])
    except Exception as e:
        # Розділ, який не вдалось прочитати, не має вдавати «без змін»:
        # None змусить дашборд перечитати його звичайним шляхом.
        logger.debug("sync-state: %s", e)
        return None


async def build_sync_state(db, user_id: int) -> dict[str, Optional[str]]:
    """
    Відбиток кожного розділу. None — не вдалось порахувати.

    Дашборд порівнює ці рядки зі своїми: різні означає «перечитай саме цей
    розділ». Порівнювати самі дані на клієнті було б те саме за трафіком,
    що й зараз, — сенс якраз у тому, щоб не тягнути їх без потреби.
    """
    state: dict[str, Optional[str]] = {}

    user = await db.get_user_by_id(user_id) or {}
    for section, columns in _USER_COLUMNS.items():
        state[section] = _from_user(user, columns) if user else None

    # Картковий модуль ділить розділ «Вивід алертів» із налаштуваннями
    # алертів: у боті це одне меню, і роздільна галочка була б декорацією.
    card_settings = await db.get_user_card_settings(user_id) or {}
    state["display"] = _digest([state.get("display"), card_settings])

    for section, (sql, _kind) in _TABLE_QUERIES.items():
        state[section] = await _from_table(db, sql, user_id)

    # Розділ існує в інтерфейсі, тож мусить бути у відповіді навіть тоді,
    # коли рахувати його нема з чого — інакше дашборд вирішить, що змін
    # немає, і не оновить його ніколи.
    for section in SECTIONS:
        state.setdefault(section, None)

    return state
