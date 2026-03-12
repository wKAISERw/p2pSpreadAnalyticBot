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
ALLOWED_KEYS = frozenset({
    "min_spread_pct",
    "working_capital_uah",
    "risk_mode",
    "behavior_alert_score",
    "velocity_spike_per_hour",
    "sticky_min_chain",
    "review_ttl_hours",
    "max_alerts_per_cycle",
})


class RuntimeConfig:
    """
    Читає overrides з таблиці bot_settings.
    Якщо override є → повертає його, інакше → settings.*
    """
    def __init__(self, db=None):
        self._db = db
        self._cache: dict[str, Any] = {}

    async def load(self) -> None:
        """Завантажує всі overrides з БД у кеш."""
        if not self._db:
            return
        try:
            async with self._db.execute(
                "SELECT key, value FROM bot_settings"
            ) as cur:
                rows = await cur.fetchall()
            self._cache = {r["key"]: r["value"] for r in rows}
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
            await self._db.execute(
                """INSERT INTO bot_settings (key, value, updated_at)
                   VALUES (?, ?, ?)
                   ON CONFLICT(key) DO UPDATE SET value=excluded.value,
                   updated_at=excluded.updated_at""",
                (key, str(value), time.time()),
            )
            await self._db.commit()
            self._cache[key] = str(value)
            return True
        except Exception as e:
            logger.error("RuntimeConfig set error: %s", e)
            return False


runtime_config = RuntimeConfig()
