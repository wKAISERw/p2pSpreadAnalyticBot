#!/usr/bin/env python3
"""
migrate_to_v2.py — Міграція p2p_scanner → v2 архітектура
=========================================================

Запуск:
    python migrate_to_v2.py                    # dry-run (показує що буде зроблено)
    python migrate_to_v2.py --apply            # реальна міграція
    python migrate_to_v2.py --apply --verbose  # з детальними логами

Що робить:
  1. Створює нові директорії + __init__.py
  2. Переміщує файли на нові місця
  3. Оновлює імпорти у всіх .py файлах
  4. Створює config/settings.py, config/defaults.py, config/runtime.py
  5. Замінює config.py на shim
  6. Створює infrastructure/http/base_client.py
  7. Розбиває telegram_notifier.py на bot/notifier.py + bot/formatters.py
  8. Створює bot/commands.py і bot/keyboards.py (заготовки)
  9. Додає .gitkeep у порожні директорії

БЕЗПЕКА: Перед реальним запуском робить резервну копію в _backup_v1/
"""

from __future__ import annotations

import argparse
import os
import re
import shutil
import sys
import textwrap
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path


# ═══════════════════════════════════════════════════════════════════════════
# Конфігурація міграції
# ═══════════════════════════════════════════════════════════════════════════

@dataclass
class MigrationPlan:
    root: Path

    # ── Нові директорії (буде створено + __init__.py) ──────────────────────
    new_packages: list[str] = field(default_factory=lambda: [
        "config",
        "core/analysis",
        "core/engine",
        "core/workers",
        "core/storage",
        "core/storage/migrations",
        "core/utils",
        "bot",
        "infrastructure/api",
        "tests/core",
        "tests/exchanges",
        "tools",
    ])

    # ── Переміщення файлів: (src_relative, dst_relative) ──────────────────
    moves: list[tuple[str, str]] = field(default_factory=lambda: [
        # core/analysis
        ("core/regex_analyzer.py",          "core/analysis/regex_analyzer.py"),
        ("core/behavioral_analyzer.py",     "core/analysis/behavioral_analyzer.py"),
        ("core/identity_analyzer.py",       "core/analysis/identity_analyzer.py"),
        ("core/rules.py",                   "core/analysis/rules.py"),

        # core/engine
        ("core/risk_engine.py",             "core/engine/risk_engine.py"),
        ("core/cross_matcher.py",           "core/engine/cross_matcher.py"),
        ("core/stability.py",               "core/engine/stability.py"),

        # core/workers
        ("core/llm_worker.py",              "core/workers/llm_worker.py"),
        ("core/review_fetcher.py",          "core/workers/review_fetcher.py"),

        # core/storage
        ("core/merchant_db.py",             "core/storage/merchant_db.py"),

        # core/utils
        ("core/circuit_breaker.py",         "core/utils/circuit_breaker.py"),
        ("core/dedup_cache.py",             "core/utils/dedup_cache.py"),
        ("core/fees.py",                    "core/utils/fees.py"),

        # bot (telegram_notifier → notifier; formatters буде створено окремо)
        ("notifications/telegram_notifier.py", "bot/notifier.py"),

        # tests → tests/core
        ("core/test_cb.py",                 "tests/core/test_cb.py"),
        ("core/test_gemini.py",             "tests/core/test_gemini.py"),
        ("core/test_risk_engine.py",        "tests/core/test_risk_engine.py"),
        ("core/test_rules.py",              "tests/core/test_rules.py"),

        # tests → tests/exchanges
        ("exchanges/mexc_test.py",          "tests/exchanges/test_mexc.py"),
        ("exchanges/test_binance.py",       "tests/exchanges/test_binance.py"),
        ("exchanges/test_wallet.py",        "tests/exchanges/test_wallet.py"),

        # tests у корені
        ("test_fetch.py",                   "tests/test_fetch.py"),
        ("test_tg.py",                      "tests/test_tg.py"),

        # tools
        ("buld_rules.py",                   "tools/build_rules.py"),   # + виправляємо назву
        ("antifrod_concepts.json",          "tools/antifrod_concepts.json"),
        ("antifrod_reviews.json",           "tools/antifrod_reviews.json"),
        ("antifrod_trade_terms.json",       "tools/antifrod_trade_terms.json"),
        ("data/dashboard.py",               "tools/dashboard.py"),

        # exchanges — прибираємо артефакти
        ("exchanges/cryptobot_inspector.py","tools/cryptobot_inspector.py"),
    ])

    # ── Файли що треба видалити (артефакти) ────────────────────────────────
    to_delete: list[str] = field(default_factory=lambda: [
        "exchanges/cryptobot_raw_log.json",
        "exchanges/inspector_session.session",
    ])

    # ── Файли в .gitignore (додати якщо немає) ─────────────────────────────
    gitignore_additions: list[str] = field(default_factory=lambda: [
        "*.session",
        "data/*.db",
        "data/*.db-shm",
        "data/*.db-wal",
        "logs/",
        "_backup_v1/",
    ])


# ═══════════════════════════════════════════════════════════════════════════
# Таблиця переіменувань імпортів
# ═══════════════════════════════════════════════════════════════════════════

IMPORT_REMAP: list[tuple[str, str]] = [
    # core.* → нові шляхи
    ("from core.analysis.regex_analyzer",        "from core.analysis.regex_analyzer"),
    ("import core.analysis.regex_analyzer",      "import core.analysis.regex_analyzer"),
    ("from core.analysis.behavioral_analyzer",   "from core.analysis.behavioral_analyzer"),
    ("import core.analysis.behavioral_analyzer", "import core.analysis.behavioral_analyzer"),
    ("from core.analysis.identity_analyzer",     "from core.analysis.identity_analyzer"),
    ("import core.analysis.identity_analyzer",   "import core.analysis.identity_analyzer"),
    ("from core.analysis.rules",                 "from core.analysis.rules"),
    ("import core.analysis.rules",               "import core.analysis.rules"),

    ("from core.engine.risk_engine",           "from core.engine.risk_engine"),
    ("import core.engine.risk_engine",         "import core.engine.risk_engine"),
    ("from core.engine.cross_matcher",         "from core.engine.cross_matcher"),
    ("import core.engine.cross_matcher",       "import core.engine.cross_matcher"),
    ("from core.engine.stability",             "from core.engine.stability"),
    ("import core.engine.stability",           "import core.engine.stability"),

    ("from core.workers.llm_worker",            "from core.workers.llm_worker"),
    ("import core.workers.llm_worker",          "import core.workers.llm_worker"),
    ("from core.workers.review_fetcher",        "from core.workers.review_fetcher"),
    ("import core.workers.review_fetcher",      "import core.workers.review_fetcher"),

    ("from core.storage.merchant_db",           "from core.storage.merchant_db"),
    ("import core.storage.merchant_db",         "import core.storage.merchant_db"),

    ("from core.utils.circuit_breaker",       "from core.utils.circuit_breaker"),
    ("import core.utils.circuit_breaker",     "import core.utils.circuit_breaker"),
    ("from core.utils.dedup_cache",           "from core.utils.dedup_cache"),
    ("import core.utils.dedup_cache",         "import core.utils.dedup_cache"),
    ("from core.utils.fees",                  "from core.utils.fees"),
    ("import core.utils.fees",                "import core.utils.fees"),

    # notifications → bot
    ("from bot.notifier", "from bot.notifier"),
    ("import bot.notifier", "import bot.notifier"),
]


# ═══════════════════════════════════════════════════════════════════════════
# Нові файли що генеруються
# ═══════════════════════════════════════════════════════════════════════════

def _generate_config_defaults(root: Path) -> str:
    """Читає існуючий config.py і переносить константи в defaults.py"""
    config_path = root / "config.py"
    existing = config_path.read_text(encoding="utf-8") if config_path.exists() else ""
    return textwrap.dedent(f'''\
        """
        config/defaults.py — Всі константи проекту з коментарями.
        Змінювати тут або через .env (Settings) або через UI бота (RuntimeConfig).
        Автоматично згенеровано migrate_to_v2.py — {datetime.now().strftime("%Y-%m-%d")}
        """

        # ── Поведінковий аналіз ────────────────────────────────────────────────
        BEHAVIOR_HISTORY_MINUTES: int = 60       # глибина history для аналізу
        BEHAVIOR_ALERT_SCORE: int = 60           # поріг для логування підозри
        BEHAVIOR_LLM_THRESHOLD: int = 60         # поріг для ескалації в LLM

        EXACT_LIMITS_SCORE: int = 20
        STICKY_LIMITS_SCORE: int = 40
        VELOCITY_SPIKE_SCORE: int = 30

        STICKY_MIN_CHAIN: int = 3               # мін. кількість snapshot-ів підряд
        VELOCITY_MIN_WINDOW_HOURS: float = 0.16  # ~10 хв
        VELOCITY_SPIKE_PER_HOUR: float = 20.0

        # ── Risk Engine ────────────────────────────────────────────────────────
        MIN_ORDERS: dict[str, int] = {{
            "Binance": 30, "Bybit": 30, "OKX": 30,
            "Wallet": 10, "MEXC": 10, "CryptoBot": 5,
        }}
        MIN_COMPLETION: dict[str, float] = {{
            "Binance": 90.0, "Bybit": 90.0, "OKX": 90.0,
            "Wallet": 85.0, "MEXC": 85.0, "CryptoBot": 80.0,
        }}
        BOT_ALERT_COOLDOWN_SEC: int = 900        # 15 хв між повторними алертами

        # ── Regex аналіз ──────────────────────────────────────────────────────
        LLM_SCORE_THRESHOLD: int = 30
        MIN_MULTI_SIGNAL_SCORE: int = 20

        # ── Review Fetcher ─────────────────────────────────────────────────────
        REVIEW_TTL_HOURS: float = 24.0
        REVIEW_MAX_QUEUE: int = 500
        BAD_REVIEW_THRESHOLD_PCT: float = 15.0
        REVIEW_WARN_NEG_PCT: float = 15.0
        REVIEW_WARN_MIN_NEG: int = 3
        REVIEW_BLOCK_NEG_PCT: float = 25.0
        REVIEW_BLOCK_MIN_NEG: int = 5
        RATE_LIMITS: dict[str, float] = {{
            "Binance": 2.0, "Bybit": 1.5, "OKX": 1.5,
        }}

        # ── LLM Worker ────────────────────────────────────────────────────────
        LLM_TIMEOUT: float = 20.0
        LLM_MAX_QUEUE: int = 100
        LLM_WORKERS: int = 2

        # ── Identity аналіз ────────────────────────────────────────────────────
        LIMIT_EPSILON: float = 0.01             # допуск для порівняння лімітів
        IDENTITY_TWIN_MINUTES: int = 15         # вікно пошуку двійників

        # ── DB / Storage ──────────────────────────────────────────────────────
        SNAPSHOT_RETENTION_HOURS: int = 168     # 7 днів
        SNAPSHOT_HEARTBEAT_MINUTES: int = 10
        DB_ASYNC_ANALYZE_CONCURRENCY: int = 8

        # ── Scanner ────────────────────────────────────────────────────────────
        ADAPTIVE_SLEEP_MIN: float = 0.5
        ADAPTIVE_SLEEP_MAX: float = 3.0
        ADAPTIVE_SLEEP_TARGET: float = 3.0
        MAX_ALERTS_PER_CYCLE: int = 5
        STABILITY_REQUIRED_HITS: int = 2
        STABILITY_TTL_SECONDS: float = 15.0
    ''')


def _generate_config_settings() -> str:
    return textwrap.dedent('''\
        """
        config/settings.py — Pydantic BaseSettings.
        Читає з .env, fallback → defaults.py.
        Це єдиний файл який треба імпортувати в бойовому коді.

        Використання:
            from config.settings import settings
            print(settings.min_spread_pct)
        """
        from __future__ import annotations
        from pathlib import Path
        from typing import Optional
        try:
            from pydantic_settings import BaseSettings
            from pydantic import Field
        except ImportError:
            from pydantic import BaseSettings, Field  # pydantic v1

        from config.defaults import (
            BEHAVIOR_HISTORY_MINUTES, BEHAVIOR_ALERT_SCORE, BEHAVIOR_LLM_THRESHOLD,
            STICKY_MIN_CHAIN, VELOCITY_MIN_WINDOW_HOURS, VELOCITY_SPIKE_PER_HOUR,
            REVIEW_TTL_HOURS, REVIEW_MAX_QUEUE, BAD_REVIEW_THRESHOLD_PCT,
            LLM_TIMEOUT, LLM_MAX_QUEUE, LLM_WORKERS,
            SNAPSHOT_RETENTION_HOURS, SNAPSHOT_HEARTBEAT_MINUTES,
            DB_ASYNC_ANALYZE_CONCURRENCY, MAX_ALERTS_PER_CYCLE,
            STABILITY_REQUIRED_HITS, STABILITY_TTL_SECONDS,
            ADAPTIVE_SLEEP_MIN, ADAPTIVE_SLEEP_MAX, ADAPTIVE_SLEEP_TARGET,
        )

        _BASE_DIR = Path(__file__).resolve().parent.parent


        class Settings(BaseSettings):
            # ── Telegram ──────────────────────────────────────────────────
            telegram_bot_token: str = ""
            telegram_chat_id: str = ""
            telegram_api_id: int = 0
            telegram_api_hash: str = ""

            # ── LLM ──────────────────────────────────────────────────────
            groq_api_key: str = ""
            gemini_api_key: str = ""

            # ── Scanner ───────────────────────────────────────────────────
            working_capital_uah: float = 5000.0
            min_spread_pct: float = 1.5
            safety_buffer_pct: float = 0.3
            search_amounts_uah: list[float] = Field(default_factory=lambda: [1000.0, 2500.0, 5100.0])
            dedup_ttl_seconds: float = 30.0
            dedup_max_size: int = 1000
            max_alerts_per_cycle: int = MAX_ALERTS_PER_CYCLE
            risk_mode: str = "WARNING"

            # ── DB ───────────────────────────────────────────────────────
            db_path: Path = _BASE_DIR / "data" / "merchants.db"
            snapshot_retention_hours: int = SNAPSHOT_RETENTION_HOURS
            snapshot_heartbeat_minutes: int = SNAPSHOT_HEARTBEAT_MINUTES
            db_async_analyze_concurrency: int = DB_ASYNC_ANALYZE_CONCURRENCY

            # ── Behavioral ────────────────────────────────────────────────
            behavior_history_minutes: int = BEHAVIOR_HISTORY_MINUTES
            behavior_alert_score: int = BEHAVIOR_ALERT_SCORE
            behavior_llm_threshold: int = BEHAVIOR_LLM_THRESHOLD
            sticky_min_chain: int = STICKY_MIN_CHAIN
            velocity_min_window_hours: float = VELOCITY_MIN_WINDOW_HOURS
            velocity_spike_per_hour: float = VELOCITY_SPIKE_PER_HOUR

            # ── Reviews ───────────────────────────────────────────────────
            review_ttl_hours: float = REVIEW_TTL_HOURS
            review_max_queue: int = REVIEW_MAX_QUEUE

            # ── LLM Worker ───────────────────────────────────────────────
            llm_timeout: float = LLM_TIMEOUT
            llm_max_queue: int = LLM_MAX_QUEUE
            llm_workers: int = LLM_WORKERS

            # ── Stability ─────────────────────────────────────────────────
            stability_required_hits: int = STABILITY_REQUIRED_HITS
            stability_ttl_seconds: float = STABILITY_TTL_SECONDS

            class Config:
                env_file = str(_BASE_DIR / ".env")
                env_file_encoding = "utf-8"
                extra = "ignore"


        settings = Settings()
    ''')


def _generate_config_runtime() -> str:
    return textwrap.dedent('''\
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
    ''')


def _generate_config_shim() -> str:
    return textwrap.dedent('''\
        """
        config.py — Shim для зворотної сумісності під час міграції.
        Весь новий код імпортує з config.settings напряму.
        Цей файл можна видалити після повної міграції імпортів.
        """
        from config.settings import settings  # noqa: F401
        from config.defaults import *         # noqa: F401, F403
    ''')


def _generate_base_client() -> str:
    return textwrap.dedent('''\
        """
        infrastructure/http/base_client.py — Спільна логіка HTTP-клієнтів.
        Всі клієнти успадковують або використовують цей клас.
        """
        from __future__ import annotations
        import asyncio
        import logging
        from typing import Any, Optional
        import aiohttp

        logger = logging.getLogger("BaseHttpClient")

        DEFAULT_HEADERS = {
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/124.0.0.0 Safari/537.36"
            ),
            "Accept-Language": "uk-UA,uk;q=0.9,en-US;q=0.8",
        }

        DEFAULT_TIMEOUT = aiohttp.ClientTimeout(total=10.0, connect=5.0, sock_read=8.0)


        class BaseHttpClient:
            """
            Базовий клас для всіх P2P HTTP-клієнтів.
            Надає: retry з backoff, спільні заголовки, централізоване логування.
            """
            MAX_RETRIES = 3
            RETRY_BACKOFF = [1.0, 2.0, 4.0]

            def __init__(
                self,
                base_url: str = "",
                extra_headers: Optional[dict] = None,
                timeout: Optional[aiohttp.ClientTimeout] = None,
            ):
                self._base_url = base_url
                self._headers = {**DEFAULT_HEADERS, **(extra_headers or {})}
                self._timeout = timeout or DEFAULT_TIMEOUT
                self._session: Optional[aiohttp.ClientSession] = None

            async def __aenter__(self) -> "BaseHttpClient":
                self._session = aiohttp.ClientSession(
                    headers=self._headers,
                    timeout=self._timeout,
                )
                return self

            async def __aexit__(self, *_) -> None:
                if self._session:
                    await self._session.close()
                    self._session = None

            async def _get(self, url: str, **kwargs) -> Any:
                return await self._request("GET", url, **kwargs)

            async def _post(self, url: str, **kwargs) -> Any:
                return await self._request("POST", url, **kwargs)

            async def _request(self, method: str, url: str, **kwargs) -> Any:
                last_exc: Optional[Exception] = None
                for attempt, backoff in enumerate(self.RETRY_BACKOFF, 1):
                    try:
                        async with self._session.request(method, url, **kwargs) as resp:
                            if resp.status == 429:
                                logger.warning(
                                    "%s 429 RateLimit on %s (attempt %d)",
                                    self.__class__.__name__, url, attempt,
                                )
                                await asyncio.sleep(backoff * 3)
                                continue
                            resp.raise_for_status()
                            return await resp.json(content_type=None)
                    except (aiohttp.ClientError, asyncio.TimeoutError) as e:
                        last_exc = e
                        logger.debug(
                            "%s request error (attempt %d/%d): %s",
                            self.__class__.__name__, attempt, self.MAX_RETRIES, e,
                        )
                        if attempt < self.MAX_RETRIES:
                            await asyncio.sleep(backoff)
                raise RuntimeError(
                    f"{self.__class__.__name__} failed after {self.MAX_RETRIES} retries: {last_exc}"
                )
    ''')


def _generate_bot_formatters() -> str:
    return textwrap.dedent('''\
        """
        bot/formatters.py — Форматування алертів і бейджів для Telegram.
        Виділено з notifications/telegram_notifier.py.
        """
        from __future__ import annotations

        # ── Бейджі ризику ──────────────────────────────────────────────────────
        RISK_BADGES = {
            # Поведінкові (Deep Research)
            "BEHAVIOR_BOTLIKE":      "🤖",
            "EXACT_LIMITS":          "📏",
            "STICKY_LIMITS":         "📏",
            "VELOCITY_SPIKE":        "⚡",
            "CROSS_EXCHANGE_BOT":    "👥",
            "FLICKER_RELIST":        "🔄",

            # Класичні (Regex)
            "BLOCK":                 "🚫",
            "NEEDS_LLM":             "🔍",
            "EXTERNAL_LINK":         "🔗",
            "TRIANGLE":              "🔺",
            "CASINO":                "🎰",
            "FINCRIME":              "💸",
            "CHARGEBACK":            "↩️",
            "SUSPICIOUS_BIZ":        "⚠️",
            "CHAT_FIRST":            "💬",
            "APPEAL_PRESSURE":       "📢",
            "BADREVIEWS":            "👎",
            "HIGH_RISK_SCORE":       "📊",

            # Статуси
            "OK":                    "✅",
            "PENDING":               "⏳",
            "SAFE":                  "🛡️",
            "BLACKLIST":             "⛔",
            "WHITELIST":             "💚",
        }

        RISK_COLORS = {
            "BLOCK":     "🔴",
            "NEEDS_LLM": "🟡",
            "WARN":      "🟠",
            "OK":        "🟢",
        }


        def risk_badge(risk_flag: str) -> str:
            """Повертає емодзі-бейдж для risk_flag."""
            if not risk_flag:
                return "✅"
            flag_upper = risk_flag.upper()
            for key, badge in RISK_BADGES.items():
                if key in flag_upper:
                    return badge
            return "⚠️"


        def format_risk_line(risk_flag: str) -> str:
            """Форматує рядок ризику для Telegram-повідомлення."""
            if not risk_flag or risk_flag == "OK":
                return ""
            badge = risk_badge(risk_flag)
            # Вирізаємо технічні деталі для читабельності
            display = risk_flag.replace("BLOCK:", "").replace("NEEDS_LLM:", "")
            parts = display.split(":", 1)
            risk_type = parts[0].strip()
            detail = parts[1].strip() if len(parts) > 1 else ""
            if detail:
                return f"{badge} <b>{risk_type}</b>: <i>{detail[:80]}</i>"
            return f"{badge} <b>{risk_type}</b>"


        def format_behavioral_summary(risk_flag: str) -> str:
            """
            Виділяє поведінкові флаги для окремого блоку в алерті.
            Повертає порожній рядок якщо поведінкових флагів немає.
            """
            if not risk_flag:
                return ""
            behavioral_keys = {
                "BEHAVIOR_BOTLIKE", "EXACT_LIMITS", "STICKY_LIMITS",
                "VELOCITY_SPIKE", "CROSS_EXCHANGE_BOT", "FLICKER_RELIST",
            }
            found = []
            for key in behavioral_keys:
                if key in risk_flag.upper():
                    badge = RISK_BADGES.get(key, "⚠️")
                    # Витягуємо значення після ключа якщо є (напр. STICKY_LIMITS:42)
                    pattern = key + "[:\\\\w./]*"
                    import re
                    m = re.search(pattern, risk_flag, re.IGNORECASE)
                    found.append(f"{badge} {m.group(0) if m else key}")
            return " | ".join(found)
    ''')


def _generate_bot_commands() -> str:
    return textwrap.dedent('''\
        """
        bot/commands.py — Telegram команди (заготовка для UI бота).
        Буде заповнено в апдейті Telegram Command Center.
        """
        from __future__ import annotations
        # TODO: Реалізувати команди:
        #   /start     — привітання і статус
        #   /status    — поточний стан сканера (цикл, мерчанти, LLM queue)
        #   /settings  — перегляд і зміна налаштувань через RuntimeConfig
        #   /ban       — ручний бан мерчанта
        #   /whitelist — додати мерчанта у whitelist
        #   /stats     — статистика за 24h (спреди, заблоковані, боти)
    ''')


def _generate_bot_keyboards() -> str:
    return textwrap.dedent('''\
        """
        bot/keyboards.py — Inline клавіатури (заготовка для UI бота).
        """
        from __future__ import annotations
        # TODO: Реалізувати keyboards:
        #   settings_menu()     — головне меню налаштувань
        #   confirm_ban()       — підтвердження ручного бану
        #   confirm_whitelist() — підтвердження whitelist
    ''')


def _generate_init(package_comment: str = "") -> str:
    if package_comment:
        return f'# {package_comment}\n'
    return ''


# ═══════════════════════════════════════════════════════════════════════════
# Міграційний рушій
# ═══════════════════════════════════════════════════════════════════════════

class Migrator:
    def __init__(self, root: Path, dry_run: bool = True, verbose: bool = False):
        self.root = root
        self.dry_run = dry_run
        self.verbose = verbose
        self.plan = MigrationPlan(root=root)
        self.actions: list[str] = []
        self.errors: list[str] = []

    def log(self, msg: str) -> None:
        prefix = "[DRY-RUN] " if self.dry_run else ""
        print(f"{prefix}{msg}")
        self.actions.append(msg)

    def vlog(self, msg: str) -> None:
        if self.verbose:
            self.log(f"  {msg}")

    def err(self, msg: str) -> None:
        print(f"  ❌ ERROR: {msg}", file=sys.stderr)
        self.errors.append(msg)

    # ── Backup ──────────────────────────────────────────────────────────────
    def backup(self) -> None:
        backup_dir = self.root / f"_backup_v1_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
        self.log(f"📦 Backup → {backup_dir.name}/")
        if not self.dry_run:
            shutil.copytree(
                self.root, backup_dir,
                ignore=shutil.ignore_patterns(".venv", "__pycache__", "*.pyc", "_backup_v1*"),
            )

    # ── Directories + __init__.py ───────────────────────────────────────────
    def create_packages(self) -> None:
        self.log("\n📁 Створення пакетів:")
        comments = {
            "config":                  "Єдине джерело конфігурації",
            "core/analysis":           "Аналізатори: regex, behavioral, identity, rules",
            "core/engine":             "Оркестратори: risk_engine, cross_matcher, stability",
            "core/workers":            "Фонові воркери: llm, review_fetcher",
            "core/storage":            "База даних і міграції",
            "core/storage/migrations": "SQL міграції схеми",
            "core/utils":              "Утиліти: circuit_breaker, dedup_cache, fees",
            "bot":                     "Telegram: notifier, commands, keyboards, formatters",
            "infrastructure/api":      "Офіційні SDK бірж (майбутнє)",
            "tests/core":              "Тести для core/",
            "tests/exchanges":         "Тести для exchanges/",
            "tools":                   "Інструменти розробки — не деплоїться в прод",
        }
        for pkg in self.plan.new_packages:
            pkg_path = self.root / pkg
            init_path = pkg_path / "__init__.py"
            self.vlog(f"mkdir {pkg}/")
            if not self.dry_run:
                pkg_path.mkdir(parents=True, exist_ok=True)
            comment = comments.get(pkg, "")
            self.vlog(f"touch {pkg}/__init__.py")
            if not self.dry_run:
                if not init_path.exists():
                    init_path.write_text(_generate_init(comment), encoding="utf-8")

        # migrations — .gitkeep замість __init__.py
        migrations_init = self.root / "core/storage/migrations/__init__.py"
        gitkeep = self.root / "infrastructure/api/.gitkeep"
        if not self.dry_run:
            if migrations_init.exists():
                migrations_init.unlink()
            (self.root / "core/storage/migrations").mkdir(parents=True, exist_ok=True)
            gitkeep.parent.mkdir(parents=True, exist_ok=True)
            gitkeep.touch()

    # ── File moves ──────────────────────────────────────────────────────────
    def move_files(self) -> None:
        self.log("\n🚚 Переміщення файлів:")
        for src_rel, dst_rel in self.plan.moves:
            src = self.root / src_rel
            dst = self.root / dst_rel
            if not src.exists():
                self.vlog(f"SKIP (not found): {src_rel}")
                continue
            self.log(f"  {src_rel} → {dst_rel}")
            if not self.dry_run:
                dst.parent.mkdir(parents=True, exist_ok=True)
                shutil.move(str(src), str(dst))

    # ── Delete artifacts ────────────────────────────────────────────────────
    def delete_artifacts(self) -> None:
        self.log("\n🗑️  Видалення артефактів:")
        for rel in self.plan.to_delete:
            path = self.root / rel
            if path.exists():
                self.log(f"  rm {rel}")
                if not self.dry_run:
                    path.unlink()
            else:
                self.vlog(f"  SKIP (not found): {rel}")

    # ── Update imports ───────────────────────────────────────────────────────
    def update_imports(self) -> None:
        self.log("\n🔄 Оновлення імпортів:")
        py_files = list(self.root.rglob("*.py"))
        py_files = [
            f for f in py_files
            if ".venv" not in f.parts
            and "_backup_v1" not in str(f)
            and "__pycache__" not in f.parts
        ]

        changed_count = 0
        for py_file in py_files:
            try:
                original = py_file.read_text(encoding="utf-8")
                updated = original
                for old, new in IMPORT_REMAP:
                    updated = updated.replace(old, new)
                if updated != original:
                    changed_count += 1
                    rel = py_file.relative_to(self.root)
                    self.log(f"  updated imports: {rel}")
                    if not self.dry_run:
                        py_file.write_text(updated, encoding="utf-8")
            except Exception as e:
                self.err(f"import update failed for {py_file}: {e}")

        self.log(f"  → {changed_count} файлів оновлено")

    # ── Generate new files ──────────────────────────────────────────────────
    def generate_files(self) -> None:
        self.log("\n✨ Генерація нових файлів:")

        new_files = {
            "config/defaults.py":              _generate_config_defaults(self.root),
            "config/settings.py":              _generate_config_settings(),
            "config/runtime.py":               _generate_config_runtime(),
            "infrastructure/http/base_client.py": _generate_base_client(),
            "bot/formatters.py":               _generate_bot_formatters(),
            "bot/commands.py":                 _generate_bot_commands(),
            "bot/keyboards.py":                _generate_bot_keyboards(),
        }

        for rel_path, content in new_files.items():
            full_path = self.root / rel_path
            self.log(f"  create {rel_path}")
            if not self.dry_run:
                full_path.parent.mkdir(parents=True, exist_ok=True)
                if not full_path.exists():
                    full_path.write_text(content, encoding="utf-8")
                else:
                    self.vlog(f"  SKIP (already exists): {rel_path}")

        # config.py shim (перезаписуємо існуючий)
        shim_path = self.root / "config.py"
        self.log("  overwrite config.py → shim")
        if not self.dry_run:
            shim_path.write_text(_generate_config_shim(), encoding="utf-8")

    # ── .gitignore update ───────────────────────────────────────────────────
    def update_gitignore(self) -> None:
        self.log("\n📝 Оновлення .gitignore:")
        gi_path = self.root / ".gitignore"
        existing = gi_path.read_text(encoding="utf-8") if gi_path.exists() else ""
        to_add = [
            line for line in self.plan.gitignore_additions
            if line not in existing
        ]
        if to_add:
            addition = "\n# === v2 migration ===\n" + "\n".join(to_add) + "\n"
            self.log(f"  adding {len(to_add)} entries")
            if not self.dry_run:
                with gi_path.open("a", encoding="utf-8") as f:
                    f.write(addition)
        else:
            self.vlog("  .gitignore вже актуальний")

    # ── Summary ─────────────────────────────────────────────────────────────
    def print_summary(self) -> None:
        print("\n" + "═" * 60)
        if self.dry_run:
            print("📋 DRY-RUN ЗАВЕРШЕНО — нічого не змінено")
            print("   Запусти з --apply щоб застосувати зміни")
        else:
            print("✅ МІГРАЦІЯ ЗАВЕРШЕНА")
        print(f"   Дій: {len(self.actions)}")
        if self.errors:
            print(f"   ⚠️  Помилок: {len(self.errors)}")
            for e in self.errors:
                print(f"      - {e}")
        print("═" * 60)

    def run(self) -> None:
        print(f"\n{'═'*60}")
        print(f"🚀 p2p_scanner v2 migration")
        print(f"   Root: {self.root}")
        print(f"   Mode: {'DRY-RUN' if self.dry_run else 'APPLY'}")
        print(f"{'═'*60}\n")

        if not self.dry_run:
            self.backup()

        self.create_packages()
        self.move_files()
        self.delete_artifacts()
        self.generate_files()
        self.update_imports()
        self.update_gitignore()
        self.print_summary()


# ═══════════════════════════════════════════════════════════════════════════
# Entrypoint
# ═══════════════════════════════════════════════════════════════════════════

def main() -> None:
    parser = argparse.ArgumentParser(
        description="Міграція p2p_scanner на v2 архітектуру"
    )
    parser.add_argument(
        "--root",
        default=r"C:\p2p_scanner",
        help="Шлях до кореня проекту (default: C:\\p2p_scanner)",
    )
    parser.add_argument(
        "--apply",
        action="store_true",
        help="Застосувати зміни (без цього флагу — dry-run)",
    )
    parser.add_argument(
        "--verbose", "-v",
        action="store_true",
        help="Детальний вивід",
    )
    args = parser.parse_args()

    root = Path(args.root)
    if not root.exists():
        print(f"❌ Директорія не знайдена: {root}", file=sys.stderr)
        sys.exit(1)

    migrator = Migrator(
        root=root,
        dry_run=not args.apply,
        verbose=args.verbose,
    )
    migrator.run()


if __name__ == "__main__":
    main()