"""
config/runtime.py — Runtime overrides з БД.
UI бота пише сюди, scanner.py читає на кожному циклі.
Таблиця bot_settings: key TEXT PK, value TEXT, updated_at REAL
"""
from __future__ import annotations
import logging
from typing import Any, Optional

logger = logging.getLogger("RuntimeConfig")

# Ключі що дозволено змінювати через UI
# Тільки системні параметри — персональні (capital/spread/banks) в scanner_users
ALLOWED_KEYS = frozenset({
    "risk_mode",
    "behavior_alert_score",
    "velocity_spike_per_hour",
    "sticky_min_chain",
    "review_ttl_hours",
    "max_alerts_per_cycle",
    "is_scanner_active",
    "disabled_exchanges",
    "require_sessions",
    "min_spread_pct",
    "safety_buffer_pct",
    "show_spread_logs",
    "block_fop_tov",
    "block_banka_jar",
    "W_REGEX",
    "W_BEHAVIOR",
    "W_REVIEWS_PCT",
    "W_REVIEWS_TEXT",
    "W_LLM",
    "W_IDENTITY",
})


class RuntimeConfig:
    """
    Читає overrides з таблиці bot_settings.
    Якщо override є → повертає його, інакше → settings.*
    """

    def __init__(self, db=None):
        self._db = db
        self._cache: dict[str, Any] = {}

    async def init_table(self) -> None:
        """Створює таблицю, якщо вона ще не існує."""
        if not self._db:
            return
        try:
            conn = getattr(self._db, "db", None) or getattr(self._db, "_db", self._db)
            if conn:
                await conn.execute(
                    """CREATE TABLE IF NOT EXISTS bot_settings
                    (
                        user_id
                        INTEGER
                        NOT
                        NULL
                        DEFAULT
                        0,
                        key
                        TEXT
                        NOT
                        NULL,
                        value
                        TEXT,
                        updated_at
                        REAL,
                        PRIMARY
                        KEY
                       (
                        user_id,
                        key
                       )
                        )"""
                )
                await conn.commit()
        except Exception as e:
            logger.error("RuntimeConfig init_table error: %s", e)

    async def load(self) -> None:
        """Завантажує всі overrides з БД у кеш."""
        if not self._db:
            return
        try:
            await self.init_table()
            conn = getattr(self._db, "db", None) or getattr(self._db, "_db", self._db)

            # 🚀 ФІКС: Завантажуємо тільки глобальні налаштування (user_id = 0)
            async with conn.execute("SELECT key, value FROM bot_settings WHERE user_id = 0") as cur:
                rows = await cur.fetchall()

            self._cache = {r[0]: r[1] for r in rows} if rows and isinstance(rows[0], tuple) else {r["key"]: r["value"]
                                                                                                  for r in rows}
            logger.debug("RuntimeConfig: loaded %d overrides", len(self._cache))
        except Exception as e:
            logger.warning("RuntimeConfig load error: %s", e)

    def get(self, key: str, default: Any = None) -> Any:
        return self._cache.get(key, default)

    async def set(self, key: str, value: Any) -> bool:
        if key not in ALLOWED_KEYS:
            logger.warning("RuntimeConfig: key %r not in ALLOWED_KEYS", key)
            return False
        if not self._db:
            return False
        import time
        try:
            conn = getattr(self._db, "db", None) or getattr(self._db, "_db", self._db)

            # 🚀 ФІКС: Правильний ON CONFLICT(user_id, key)
            await conn.execute(
                """INSERT INTO bot_settings (user_id, key, value, updated_at)
                   VALUES (0, ?, ?, ?) ON CONFLICT(user_id, key) DO
                UPDATE SET value = excluded.value,
                    updated_at = excluded.updated_at""",
                (key, str(value), time.time()),
            )
            await conn.commit()
            self._cache[key] = str(value)
            return True
        except Exception as e:
            logger.error("RuntimeConfig set error: %s", e)
            return False


runtime_config = RuntimeConfig()