# core/merchant_db.py
# =============================================================================
# БОЙОВА ВЕРСІЯ v1.0  (Крок 1: WAL + канонічна версія)
# =============================================================================
"""
Асинхронна SQLite база мерчантів (aiosqlite).

Задачі:
- кеш вердиктів по merchant_id
- risk_score та лічильники звернень
- глобальний blacklist
- review cache для Binance / Bybit / OKX

WAL (Write-Ahead Logging):
- вмикається при старті через PRAGMA journal_mode=WAL
- захищає від database locked при паралельних async читаннях/записах
- особливо важливо коли LLMWorkerPool і ReviewFetcher пишуть одночасно
"""

from __future__ import annotations
import re
import hashlib
import logging
import time
from pathlib import Path
from typing import Optional

import aiosqlite
from core.utils.crypto import encrypt, decrypt

logger = logging.getLogger("MerchantDB")

DB_PATH = Path("data/merchants.db")

TTL = {
    "OK": 43_200,
    "SUSPICIOUS": 43_200,
    "BLOCK": 259_200,
    "UNKNOWN": 1_800,
}
DEFAULT_TTL = 43_200

VERDICT_SCORE = {
    "OK": 0,
    "SUSPICIOUS": 30,
    "BLOCK": 100,
    "UNKNOWN": 10,
}

LLM_SOURCES = {"groq", "gemini", "llm"}


def hash_terms(trade_terms: str) -> str:
    # 🚀 Викидаємо цифри (таймштампи), пунктуацію та емодзі.
    # Тепер зміна "Оновлено о 20:15" не створить новий виклик LLM.
    import re
    normalized = re.sub(r'[\d\W_]+', '', (trade_terms or "").lower())
    return hashlib.md5(normalized.encode()).hexdigest()


class MerchantDB:
    def __init__(self, db_path: Path = DB_PATH):
        self._path = db_path
        self._db: Optional[aiosqlite.Connection] = None

    async def start(self) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._db = await aiosqlite.connect(str(self._path))
        self._db.row_factory = aiosqlite.Row

        # WAL: захист від database locked при паралельних async операціях.
        # LLMWorkerPool (2 воркери) + ReviewFetcher пишуть одночасно —
        # без WAL можливі помилки при конкурентному доступі.
        await self._db.execute("PRAGMA journal_mode=WAL")
        await self._db.execute("PRAGMA synchronous=NORMAL")  # безпечно + швидше
        await self._db.execute("PRAGMA cache_size=-65536")   # 64 MB кеш
        await self._db.execute("PRAGMA foreign_keys=ON")
        await self._db.commit()

        await self._init_schema()
        logger.info("MerchantDB запущено (WAL): %s", self._path)

    async def stop(self) -> None:
        if self._db:
            await self._db.close()
        logger.info("MerchantDB зупинено")

    async def _init_schema(self) -> None:
        await self._db.executescript("""
                                     CREATE TABLE IF NOT EXISTS merchant_verdict
                                     (
                                         exchange
                                         TEXT
                                         NOT
                                         NULL,
                                         merchant_id
                                         TEXT
                                         NOT
                                         NULL,
                                         merchant_name
                                         TEXT,
                                         terms_hash
                                         TEXT,
                                         verdict
                                         TEXT
                                         DEFAULT
                                         'UNKNOWN',
                                         risk_type
                                         TEXT,
                                         reason
                                         TEXT,
                                         risk_score
                                         INTEGER
                                         DEFAULT
                                         0,
                                         llm_calls_count
                                         INTEGER
                                         DEFAULT
                                         0,
                                         save_count
                                         INTEGER
                                         DEFAULT
                                         0,
                                         updated_at
                                         REAL
                                         DEFAULT
                                         0,
                                         PRIMARY
                                         KEY
                                     (
                                         exchange,
                                         merchant_id
                                     )
                                         );

                                     CREATE TABLE IF NOT EXISTS global_blacklist
                                     (
                                         exchange
                                         TEXT
                                         NOT
                                         NULL,
                                         merchant_id
                                         TEXT
                                         NOT
                                         NULL,
                                         merchant_name
                                         TEXT,
                                         reason
                                         TEXT,
                                         source
                                         TEXT,
                                         added_at
                                         REAL
                                         DEFAULT
                                         0,
                                         PRIMARY
                                         KEY
                                     (
                                         exchange,
                                         merchant_id
                                     )
                                         );

                                     CREATE TABLE IF NOT EXISTS block_log
                                     (
                                         exchange
                                         TEXT
                                         NOT
                                         NULL,
                                         merchant_id
                                         TEXT
                                         NOT
                                         NULL,
                                         merchant_name
                                         TEXT,
                                         verdict
                                         TEXT,
                                         risk_type
                                         TEXT,
                                         reason
                                         TEXT,
                                         source
                                         TEXT,
                                         logged_at
                                         REAL
                                         DEFAULT
                                         0
                                     );

                                     CREATE TABLE IF NOT EXISTS merchant_reviews
                                     (
                                         exchange
                                         TEXT
                                         NOT
                                         NULL,
                                         merchant_id
                                         TEXT
                                         NOT
                                         NULL,
                                         positive_count
                                         INTEGER
                                         DEFAULT
                                         0,
                                         negative_count
                                         INTEGER
                                         DEFAULT
                                         0,
                                         neutral_count
                                         INTEGER
                                         DEFAULT
                                         0,
                                         bad_texts_json
                                         TEXT
                                         DEFAULT
                                         '[]',
                                         updated_at
                                         REAL
                                         DEFAULT
                                         0,
                                         PRIMARY
                                         KEY
                                     (
                                         exchange,
                                         merchant_id
                                     )
                                         );

                                     /* 🚀 ДОДАЄМО ТАБЛИЦЮ СНАПШОТІВ */
                                     CREATE TABLE IF NOT EXISTS merchant_snapshots
                                     (
                                         exchange
                                         TEXT
                                         NOT
                                         NULL,
                                         merchant_id
                                         TEXT
                                         NOT
                                         NULL,
                                         merchant_name
                                         TEXT,
                                         side
                                         TEXT,
                                         price
                                         REAL,
                                         min_limit
                                         REAL,
                                         max_limit
                                         REAL,
                                         order_count
                                         INTEGER,
                                         finish_rate
                                         REAL,
                                         is_verified
                                         INTEGER,
                                         terms_hash
                                         TEXT,
                                         recorded_at
                                         REAL
                                         NOT
                                         NULL
                                     );
                                    CREATE TABLE IF NOT EXISTS merchant_review_history (
                                            id INTEGER PRIMARY KEY AUTOINCREMENT,
                                            exchange TEXT NOT NULL,
                                            merchant_id TEXT NOT NULL,
                                            positive_count INTEGER DEFAULT 0,
                                            negative_count INTEGER DEFAULT 0,
                                            neg_pct REAL DEFAULT 0.0,
                                            recorded_at REAL NOT NULL
                                        );
                                    CREATE INDEX IF NOT EXISTS idx_rev_hist ON merchant_review_history(exchange, merchant_id, recorded_at);
                                     CREATE INDEX IF NOT EXISTS idx_verdict_lookup
                                         ON merchant_verdict (exchange, merchant_id);

                                     /* 🚀 ІНДЕКСИ ДЛЯ СНАПШОТІВ */
                                     CREATE INDEX IF NOT EXISTS idx_snap_lookup
                                         ON merchant_snapshots (exchange, merchant_id, recorded_at);

                                     CREATE INDEX IF NOT EXISTS idx_snap_ts
                                         ON merchant_snapshots (recorded_at);

                                     /* 🚀 Для find_digital_twins — без цього індексу full table scan */
                                     CREATE INDEX IF NOT EXISTS idx_snap_name
                                         ON merchant_snapshots (merchant_name, exchange, recorded_at);

                                     -- API credentials (зашифровані Fernet)
                                     -- user_id: Telegram user_id (0 = legacy/single-user)
                                     CREATE TABLE IF NOT EXISTS user_credentials (
                                         user_id     INTEGER NOT NULL DEFAULT 0,
                                         exchange    TEXT NOT NULL,
                                         api_key     TEXT NOT NULL,
                                         api_secret  TEXT NOT NULL,
                                         passphrase  TEXT DEFAULT '',
                                         label       TEXT DEFAULT '',
                                         created_at  REAL DEFAULT 0,
                                         updated_at  REAL DEFAULT 0,
                                         PRIMARY KEY (user_id, exchange)
                                     );

                                     -- Runtime overrides від UI бота
                                     -- user_id: 0 = глобальні (single-user), >0 = персональні
                                     CREATE TABLE IF NOT EXISTS bot_settings (
                                         user_id    INTEGER NOT NULL DEFAULT 0,
                                         key        TEXT NOT NULL,
                                         value      TEXT NOT NULL,
                                         updated_at REAL DEFAULT 0,
                                         PRIMARY KEY (user_id, key)
                                     );

                                     -- Підписники сканера (multi-user)
                                     CREATE TABLE IF NOT EXISTS scanner_users (
                                         user_id          INTEGER NOT NULL PRIMARY KEY,
                                         telegram_chat_id INTEGER NOT NULL,
                                         working_capital  REAL DEFAULT 5100.0,
                                         min_spread_pct   REAL DEFAULT 0.5,
                                         bank_codes       TEXT DEFAULT '43,14,64',
                                         is_active        INTEGER DEFAULT 1,
                                         created_at       REAL DEFAULT 0
                                     );
                                     -- Сесії з браузера (перехоплені Headers та Cookies)
                                     CREATE TABLE IF NOT EXISTS auth_sessions (
                                         user_id      INTEGER NOT NULL DEFAULT 0,
                                         exchange     TEXT NOT NULL,
                                         headers_json TEXT DEFAULT '{}',
                                         cookies_json TEXT DEFAULT '{}',
                                         updated_at   REAL DEFAULT 0,
                                         PRIMARY KEY (user_id, exchange)
                                     );
                                     
                                     -- Таблиця: trade_sessions
                                     CREATE TABLE IF NOT EXISTS trade_sessions (
                                         id              INTEGER PRIMARY KEY AUTOINCREMENT,
                                         strategy        TEXT NOT NULL,
                                         route_type      TEXT NOT NULL,
                                         buy_exchange    TEXT,           -- 🚀 ДОДАНО
                                         buy_leg_id      INTEGER,
                                         sell_leg_id     INTEGER,
                                         session_status  TEXT NOT NULL DEFAULT 'OPEN',
                                         network         TEXT,
                                         network_fee     REAL DEFAULT 0,
                                         gross_profit    REAL,
                                         created_at      DATETIME DEFAULT CURRENT_TIMESTAMP,
                                         completed_at    DATETIME
                                     );

                                     -- Таблиця: active_trades
                                     CREATE TABLE IF NOT EXISTS active_trades (
                                         id               INTEGER PRIMARY KEY AUTOINCREMENT,
                                         session_id       INTEGER REFERENCES trade_sessions(id),
                                         strategy         TEXT,
                                         leg              TEXT,
                                         route_type       TEXT,
                                         network          TEXT,
                                         network_fee      REAL DEFAULT 0,
                                         expires_at       DATETIME,
                                         owner_user_id    INTEGER DEFAULT NULL,
                                         exchange         TEXT,
                                         order_id         TEXT UNIQUE,
                                         ad_id            TEXT,
                                         asset            TEXT,
                                         fiat             TEXT,
                                         price            REAL,
                                         amount           REAL,
                                         fiat_amount      REAL,
                                         counterparty_id  TEXT,
                                         counterparty_name TEXT,
                                         status           TEXT NOT NULL,
                                         created_at       DATETIME DEFAULT CURRENT_TIMESTAMP,
                                         updated_at       DATETIME
                                     );
                                     """)
        await self._db.commit()

        await self._ensure_column("merchant_verdict", "save_count", "INTEGER DEFAULT 0")
        await self._ensure_column("merchant_verdict", "llm_decision", "TEXT DEFAULT 'UNKNOWN'")
        await self._ensure_column("merchant_verdict", "trade_recommendation", "TEXT DEFAULT 'PENDING'")
        await self._ensure_column("merchant_reviews", "status", "TEXT DEFAULT 'OK'")
        # Міграція колонок scanner_users
        await self._ensure_column("scanner_users", "min_amount_uah", "REAL DEFAULT 0.0")
        await self._ensure_column("scanner_users", "merchant_filters_json", "TEXT DEFAULT '{}'")
        await self._ensure_column("scanner_users", "is_alerts_active", "INTEGER DEFAULT 1")
        # 🔥 ДОДАНО СЕКЦІЮ ДЛЯ ПРОТУХШИХ СЕСІЙ
        await self._ensure_column("auth_sessions", "is_active", "INTEGER DEFAULT 1")
        # 🚀 МІГРАЦІЯ ДЛЯ ТОРГОВИХ СЕСІЙ
        await self._ensure_column("trade_sessions", "buy_exchange", "TEXT")

    async def _ensure_column(self, table: str, column: str, ddl: str) -> None:
        async with self._db.execute(f"PRAGMA table_info({table})") as cur:
            rows = await cur.fetchall()
        cols = {row["name"] for row in rows}
        if column not in cols:
            await self._db.execute(f"ALTER TABLE {table} ADD COLUMN {column} {ddl}")
            await self._db.commit()

    async def get_risk_score(self, exchange: str, merchant_id: str) -> int:
        async with self._db.execute(
                "SELECT risk_score FROM merchant_verdict WHERE exchange=? AND merchant_id=?",
                (exchange, merchant_id),
        ) as cur:
            row = await cur.fetchone()
        return row["risk_score"] if row else 0

    async def is_blacklisted(self, exchange: str, merchant_id: str) -> tuple[bool, str]:
        async with self._db.execute(
                "SELECT reason, source FROM global_blacklist WHERE exchange=? AND merchant_id=?",
                (exchange, merchant_id),
        ) as cur:
            row = await cur.fetchone()
        if row:
            return True, f"[{row['source']}] {row['reason']}"
        return False, ""

    async def add_to_blacklist(
            self,
            exchange: str,
            merchant_id: str,
            merchant_name: str,
            reason: str,
            source: str = "manual",
    ) -> None:
        now = time.time()
        await self._db.execute(
            """
            INSERT OR REPLACE INTO global_blacklist
            (exchange, merchant_id, merchant_name, reason, source, added_at)
            VALUES (?,?,?,?,?,?)
            """,
            (exchange, merchant_id, merchant_name, reason, source, now),
        )
        await self._db.commit()
        logger.warning("🚫 Blacklist додано: %s [%s] — %s", merchant_name, exchange, reason)

    async def load_blacklist_from_file(self, path: str = "data/blacklist.json") -> int:
        import json
        p = Path(path)
        if not p.exists():
            return 0

        entries = json.loads(p.read_text(encoding="utf-8"))
        count = 0
        for e in entries:
            await self.add_to_blacklist(
                e["exchange"],
                e["merchant_id"],
                e.get("merchant_name", ""),
                e.get("reason", ""),
                e.get("source", "file"),
            )
            count += 1

        logger.info("Blacklist завантажено: %d записів з %s", count, path)
        return count

    async def get_verdict(
            self,
            exchange: str,
            merchant_id: str,
            current_terms: str,
    ) -> Optional[str]:
        async with self._db.execute(
                """
                SELECT verdict, terms_hash, updated_at, llm_calls_count
                FROM merchant_verdict
                WHERE exchange = ?
                  AND merchant_id = ?
                """,
                (exchange, merchant_id),
        ) as cur:
            row = await cur.fetchone()

        if row is None:
            return None

        verdict = row["verdict"]
        terms_hash = row["terms_hash"]
        updated_at = row["updated_at"] or 0
        llm_calls = row["llm_calls_count"] or 0

        if terms_hash != hash_terms(current_terms):
            # 🚀 Тепер SUSPICIOUS не буде перепровірятися кожні 5 секунд при зміні тексту
            if verdict == "BLOCK" or (verdict in ("OK", "SUSPICIOUS") and (time.time() - updated_at) < 3600):
                pass
            else:
                return None

        ttl = TTL.get(verdict, DEFAULT_TTL)
        if time.time() - updated_at > ttl:
            return None

        if verdict == "UNKNOWN" and llm_calls >= 3:
            return "UNKNOWN"

        return verdict

    async def get_reason(self, exchange: str, merchant_id: str) -> tuple[str, str]:
        async with self._db.execute(
                """
                SELECT risk_type, reason
                FROM merchant_verdict
                WHERE exchange = ?
                  AND merchant_id = ?
                """,
                (exchange, merchant_id),
        ) as cur:
            row = await cur.fetchone()
        if row:
            return row["risk_type"] or "", row["reason"] or ""
        return "", ""

    async def get_trade_recommendation(
        self, exchange: str, merchant_id: str
    ) -> str:
        """
        Повертає пряму рекомендацію LLM щодо проведення угоди:
            "APPROVE"     — торгувати можна
            "CONDITIONAL" — з обережністю
            "REJECT"      — не торгувати
            "PENDING"     — LLM ще не аналізував
        """
        if not self._db:
            return "PENDING"
        async with self._db.execute(
            "SELECT trade_recommendation FROM merchant_verdict "
            "WHERE exchange = ? AND merchant_id = ?",
            (exchange, merchant_id),
        ) as cur:
            row = await cur.fetchone()
        if row is None:
            return "PENDING"
        rec = (row["trade_recommendation"] or "PENDING").strip().upper()
        return rec if rec in ("APPROVE", "CONDITIONAL", "REJECT", "PENDING") else "PENDING"

    async def save_verdict(
            self,
            exchange: str,
            merchant_id: str,
            merchant_name: str,
            trade_terms: str,
            verdict: str,
            risk_type: str = "",
            reason: str = "",
            source: str = "",
            trade_recommendation: str = "CONDITIONAL",  # ← НОВЕ
    ) -> None:
        now = time.time()
        t_hash = hash_terms(trade_terms)
        score_delta = VERDICT_SCORE.get(verdict, 0)
        llm_inc = 1 if (source or "").lower() in LLM_SOURCES else 0

        # Валідація trade_recommendation
        if trade_recommendation not in ("APPROVE", "CONDITIONAL", "REJECT", "PENDING"):
            trade_recommendation = "CONDITIONAL"
        if verdict == "BLOCK":
            trade_recommendation = "REJECT"  # примусово

        await self._db.execute(
            """
            INSERT INTO merchant_verdict
            (exchange, merchant_id, merchant_name, terms_hash,
             verdict, risk_type, reason, risk_score,
             llm_calls_count, save_count, updated_at, trade_recommendation)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?) ON CONFLICT(exchange, merchant_id) DO
            UPDATE SET
                merchant_name = excluded.merchant_name,
                terms_hash = excluded.terms_hash,
                verdict = excluded.verdict,
                risk_type = excluded.risk_type,
                reason = excluded.reason,
                risk_score = MIN (merchant_verdict.risk_score + excluded.risk_score, 200),
                llm_calls_count = merchant_verdict.llm_calls_count + ?,
                save_count = merchant_verdict.save_count + 1,
                updated_at = excluded.updated_at,
                trade_recommendation = excluded.trade_recommendation
            """,
            (
                exchange,
                merchant_id,
                merchant_name,
                t_hash,
                verdict,
                risk_type,
                reason,
                score_delta,
                llm_inc,
                1,
                now,
                trade_recommendation,  # ← НОВЕ
                llm_inc,
            ),
        )

        if verdict == "BLOCK":
            await self._db.execute(
                """
                INSERT INTO block_log
                (exchange, merchant_id, merchant_name, verdict, risk_type, reason, source, logged_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (exchange, merchant_id, merchant_name, verdict, risk_type, reason, source, now),
            )

        await self._db.commit()
        logger.debug(
            "Збережено вердикт %s для %s [%s] source=%s",
            verdict, merchant_name, exchange, source
        )

    async def needs_review_fetch(
            self,
            exchange: str,
            merchant_id: str,
            review_ttl_hours: float = 24.0,
    ) -> bool:
        async with self._db.execute(
                """
                SELECT updated_at, status
                FROM merchant_reviews
                WHERE exchange = ?
                  AND merchant_id = ?
                """,
                (exchange, merchant_id),
        ) as cur:
            row = await cur.fetchone()

        if row is None:
            return True

        updated_at = row["updated_at"] or 0
        status = row["status"] or "OK"

        # NO_SESSION: re-check кожну годину — як тільки сесія з'явиться,
        # всі мерчанти підтягнуть відгуки протягом ~1h без ручних дій
        if status == "NO_SESSION":
            return (time.time() - updated_at) > 3600.0

        # PENDING / UNAVAILABLE: завжди потребує перефетч
        if status in ("PENDING", "UNAVAILABLE"):
            return True

        ttl_sec = review_ttl_hours * 3600.0
        return (time.time() - updated_at) > ttl_sec

    async def save_reviews(
            self, exchange: str, merchant_id: str,
            positive_count: int, negative_count: int, neutral_count: int,
            bad_texts: list[str], status: str = "OK"  # <--- Додали параметр
    ) -> None:
        import json
        now = time.time()
        bad_texts_json = json.dumps(bad_texts[:20], ensure_ascii=False)

        await self._db.execute(
            """
            INSERT INTO merchant_reviews
            (exchange, merchant_id, positive_count, negative_count, neutral_count, bad_texts_json, updated_at, status)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?) ON CONFLICT(exchange, merchant_id) DO
            UPDATE SET
                positive_count = excluded.positive_count,
                negative_count = excluded.negative_count,
                neutral_count = excluded.neutral_count,
                bad_texts_json = excluded.bad_texts_json,
                updated_at = excluded.updated_at,
                status = excluded.status
            """,
            (exchange, merchant_id, max(int(positive_count), 0), max(int(negative_count), 0),
             max(int(neutral_count), 0), bad_texts_json, now, status),
        )
        await self._db.commit()

    async def get_reviews_summary(self, exchange: str, merchant_id: str) -> dict:
        import json
        async with self._db.execute(
                "SELECT positive_count, negative_count, neutral_count, bad_texts_json, updated_at, status "
                "FROM merchant_reviews WHERE exchange=? AND merchant_id=?",
                (exchange, merchant_id),
        ) as cur:
            row = await cur.fetchone()

        if not row:
            return {"positive": 0, "negative": 0, "neutral": 0, "bad_texts": [], "updated_at": 0, "status": "UNKNOWN"}

        try:
            bad_texts = json.loads(row["bad_texts_json"] or "[]")
        except Exception:
            bad_texts = []

        return {
            "positive": row["positive_count"] or 0,
            "negative": row["negative_count"] or 0,
            "neutral": row["neutral_count"] or 0,
            "bad_texts": bad_texts,
            "updated_at": row["updated_at"] or 0,
            "status": row["status"] or "OK"  # <--- Повертаємо статус
        }

    async def add_snapshots_batch(self, orders: list, heartbeat_minutes: int = 10) -> int:
        """
        Масово додає снапшоти, записуючи лише змінені стани або Heartbeat.

        Оптимізований pipeline (O(1) замість O(N) round-trips до SQLite):
          1. Нормалізуємо всі ордери в пам'яті — без awaits.
          2. Один SELECT з GROUP BY дістає останні snapshot-и для ВСІХ мерчантів.
          3. Порівнюємо в пам'яті.
          4. executemany() вставляє всі нові записи одним викликом.
          5. Один commit() на весь батч.
        """
        if not self._db or not orders:
            return 0

        now = time.time()
        heartbeat_sec = heartbeat_minutes * 60

        # ── 1. Нормалізація (CPU-only, без await) ───────────────────────────
        # key: (exchange, merchant_id) → normalized dict
        candidates: dict[tuple[str, str], dict] = {}

        for o in orders:
            merchant_id = getattr(o, "merchant_id", None)
            if not merchant_id:
                continue

            exchange = getattr(o, "exchange", "") or ""
            merchant_name = getattr(o, "merchant_name", "") or ""
            side = getattr(o, "side", "") or ""
            trade_terms = getattr(o, "trade_terms", "") or ""

            try:
                price = float(getattr(o, "price", 0) or 0)
                min_limit = float(getattr(o, "min_limit", 0) or 0)
                max_limit = float(getattr(o, "max_limit", 0) or 0)
                order_count = int(getattr(o, "month_order_count", 0) or 0)
                finish_rate = float(getattr(o, "finish_rate_pct", 0) or 0)
                is_verified = 1 if bool(getattr(o, "is_verified", False)) else 0
            except Exception:
                logger.debug(
                    "Snapshot skip: bad numeric fields for %s [%s]",
                    getattr(o, "merchant_name", "?"), exchange,
                )
                continue

            # При дублях у батчі залишаємо останній (перезаписуємо)
            candidates[(exchange, merchant_id)] = {
                "exchange": exchange,
                "merchant_id": merchant_id,
                "merchant_name": merchant_name,
                "side": side,
                "price": price,
                "min_limit": min_limit,
                "max_limit": max_limit,
                "order_count": order_count,
                "finish_rate": finish_rate,
                "is_verified": is_verified,
                "terms_hash": hash_terms(trade_terms),
            }

        if not candidates:
            return 0

        # ── 2. Один SELECT — останні snapshot-и для всіх candidates ─────────
        # Використовуємо "max(recorded_at)" щоб дістати лише найновіший рядок
        # для кожної пари (exchange, merchant_id) за один запит.
        keys = list(candidates.keys())
        placeholders = ",".join("(?,?)" for _ in keys)
        flat_params: list = []
        for ex, mid in keys:
            flat_params.extend([ex, mid])

        last_snapshots: dict[tuple[str, str], aiosqlite.Row] = {}
        async with self._db.execute(
                f"""
            SELECT s.exchange,
                   s.merchant_id,
                   s.price,
                   s.min_limit,
                   s.max_limit,
                   s.order_count,
                   s.finish_rate,
                   s.is_verified,
                   s.terms_hash,
                   s.recorded_at
            FROM merchant_snapshots s
            INNER JOIN (
                SELECT exchange, merchant_id, MAX(recorded_at) AS max_ts
                FROM merchant_snapshots
                WHERE (exchange, merchant_id) IN ({placeholders})
                GROUP BY exchange, merchant_id
            ) latest
              ON s.exchange    = latest.exchange
             AND s.merchant_id = latest.merchant_id
             AND s.recorded_at = latest.max_ts
            """,
                flat_params,
        ) as cur:
            rows = await cur.fetchall()

        for row in rows:
            last_snapshots[(row["exchange"], row["merchant_id"])] = row

        # ── 3. Порівняння в пам'яті + збір рядків для INSERT ────────────────
        to_insert: list[tuple] = []

        for key, c in candidates.items():
            last = last_snapshots.get(key)

            if last is None:
                should_insert = True
            else:
                changed = (
                        float(last["price"] or 0) != c["price"]
                        or float(last["min_limit"] or 0) != c["min_limit"]
                        or float(last["max_limit"] or 0) != c["max_limit"]
                        or int(last["order_count"] or 0) != c["order_count"]
                        or float(last["finish_rate"] or 0) != c["finish_rate"]
                        or int(last["is_verified"] or 0) != c["is_verified"]
                        or (last["terms_hash"] or "") != c["terms_hash"]
                )
                overdue = (now - float(last["recorded_at"])) > heartbeat_sec
                should_insert = changed or overdue

            if should_insert:
                to_insert.append((
                    c["exchange"], c["merchant_id"], c["merchant_name"], c["side"],
                    c["price"], c["min_limit"], c["max_limit"],
                    c["order_count"], c["finish_rate"], c["is_verified"],
                    c["terms_hash"], now,
                ))

        # ── 4. executemany + один commit ─────────────────────────────────────
        if to_insert:
            await self._db.executemany(
                """
                INSERT INTO merchant_snapshots
                (exchange, merchant_id, merchant_name, side,
                 price, min_limit, max_limit,
                 order_count, finish_rate, is_verified,
                 terms_hash, recorded_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                to_insert,
            )
            await self._db.commit()
            logger.debug("MerchantDB: saved %d/%d snapshot(s)", len(to_insert), len(candidates))

        return len(to_insert)

    async def get_recent_snapshots(self, exchange: str, merchant_id: str, minutes: int = 60) -> list[dict]:
        if not self._db or not merchant_id:
            return []

        since = time.time() - (minutes * 60)
        async with self._db.execute(
                """
                SELECT *
                FROM merchant_snapshots
                WHERE exchange = ?
                  AND merchant_id = ?
                  AND recorded_at > ?
                ORDER BY recorded_at ASC
                """,
                (exchange, merchant_id, since),
        ) as cur:
            rows = await cur.fetchall()

        return [dict(r) for r in rows]

    async def prune_snapshots(self, max_age_hours: int = 168) -> int:
        """Очищення історії (за замовчуванням 7 днів для long-term патернів)."""
        if not self._db:
            return 0

        limit = time.time() - (max_age_hours * 3600)
        async with self._db.execute(
                "DELETE FROM merchant_snapshots WHERE recorded_at < ?",
                (limit,),
        ) as cur:
            deleted = cur.rowcount or 0

        if deleted > 0:
            await self._db.commit()
            logger.debug("MerchantDB: pruned %d old snapshot(s)", deleted)

        return deleted

    # ═══════════════════════════════════════════════════════════════════════
    # API Credentials (encrypted storage)
    # ═══════════════════════════════════════════════════════════════════════

    async def save_credentials(
        self,
        exchange: str,
        api_key: str,
        api_secret: str,
        passphrase: str = "",
        label: str = "",
        user_id: int = 0,
    ) -> bool:
        """
        Зберігає API ключі для біржі (зашифровано Fernet).
        user_id=0 → single-user режим (зворотна сумісність).
        user_id>0 → multi-user: ключі прив'язані до конкретного Telegram user.
        """
        if not self._db:
            return False
        import time
        try:
            now = time.time()
            await self._db.execute(
                """INSERT INTO user_credentials
                       (user_id, exchange, api_key, api_secret, passphrase, label, created_at, updated_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                   ON CONFLICT(user_id, exchange) DO UPDATE SET
                       api_key    = excluded.api_key,
                       api_secret = excluded.api_secret,
                       passphrase = excluded.passphrase,
                       label      = excluded.label,
                       updated_at = excluded.updated_at""",
                (
                    user_id,
                    exchange,
                    encrypt(api_key),
                    encrypt(api_secret),
                    encrypt(passphrase) if passphrase else "",
                    label,
                    now, now,
                ),
            )
            await self._db.commit()
            logger.info("Credentials saved for %s (user_id=%d)", exchange, user_id)
            return True
        except Exception as e:
            logger.error("save_credentials [%s]: %s", exchange, e)
            return False

    async def get_credentials(self, exchange: str, user_id: int = 0) -> dict | None:
        """
        Повертає розшифровані credentials для біржі або None.
        user_id=0 → single-user (зворотна сумісність).
        """
        if not self._db:
            return None
        try:
            async with self._db.execute(
                "SELECT api_key, api_secret, passphrase, label FROM user_credentials WHERE user_id=? AND exchange = ?",
                (user_id, exchange),
            ) as cur:
                row = await cur.fetchone()
            if not row:
                return None
            return {
                "api_key":    decrypt(row["api_key"]),
                "api_secret": decrypt(row["api_secret"]),
                "passphrase": decrypt(row["passphrase"]) if row["passphrase"] else "",
                "label":      row["label"] or "",
            }
        except Exception as e:
            logger.error("get_credentials [%s]: %s", exchange, e)
            return None

    async def get_all_credentials(self, user_id: int = 0) -> dict[str, dict]:
        """
        Повертає всі credentials як {exchange: {...}}.
        user_id=0 → single-user режим (зворотна сумісність).
        """
        if not self._db:
            return {}
        try:
            async with self._db.execute(
                "SELECT exchange, api_key, api_secret, passphrase, label FROM user_credentials WHERE user_id=?",
                (user_id,),
            ) as cur:
                rows = await cur.fetchall()
            return {
                row["exchange"]: {
                    "api_key":    decrypt(row["api_key"]),
                    "api_secret": decrypt(row["api_secret"]),
                    "passphrase": decrypt(row["passphrase"]) if row["passphrase"] else "",
                    "label":      row["label"] or "",
                }
                for row in rows
            }
        except Exception as e:
            logger.error("get_all_credentials: %s", e)
            return {}

    async def delete_credentials(self, exchange: str, user_id: int = 0) -> bool:
        """Видаляє credentials для біржі."""
        if not self._db:
            return False
        try:
            await self._db.execute(
                "DELETE FROM user_credentials WHERE user_id=? AND exchange = ?", (user_id, exchange,)
            )
            await self._db.commit()
            logger.info("Credentials deleted for %s", exchange)
            return True
        except Exception as e:
            logger.error("delete_credentials [%s]: %s", exchange, e)
            return False

    async def has_credentials(self, exchange: str, user_id: int = 0) -> bool:
        """Швидка перевірка чи є ключі для біржі."""
        if not self._db:
            return False
        try:
            async with self._db.execute(
                "SELECT 1 FROM user_credentials WHERE user_id=? AND exchange = ? LIMIT 1", (user_id, exchange,)
            ) as cur:
                return await cur.fetchone() is not None
        except Exception:
            return False

        # ═══════════════════════════════════════════════════════════════════════
        # Browser Auth Sessions (Interceptor)
        # ═══════════════════════════════════════════════════════════════════════

    async def save_auth_session(
            self,
            exchange: str,
            headers_dict: dict,
            cookies_dict: dict,
            user_id: int = 0
    ) -> bool:
        """Зберігає перехоплені браузерні заголовки та кукіси."""
        if not self._db:
            return False
        import json
        import time
        try:
            now = time.time()
            headers_json = json.dumps(headers_dict, ensure_ascii=False)
            cookies_json = json.dumps(cookies_dict, ensure_ascii=False)

            await self._db.execute(
                """INSERT INTO auth_sessions
                        (user_id, exchange, headers_json, cookies_json, updated_at, is_active)
                    VALUES (?, ?, ?, ?, ?, 1) 
                    ON CONFLICT(user_id, exchange) DO UPDATE SET
                        headers_json = excluded.headers_json,
                        cookies_json = excluded.cookies_json,
                        updated_at = excluded.updated_at,
                        is_active = 1""",
                (user_id, exchange, headers_json, cookies_json, now),
            )
            await self._db.commit()
            logger.info("Auth session saved for %s (user_id=%d)", exchange, user_id)
            return True
        except Exception as e:
            logger.error("save_auth_session [%s]: %s", exchange, e)
            return False

    async def get_auth_session(self, exchange: str, user_id: int = 0) -> tuple[dict, dict, float]:
        """Повертає (headers_dict, cookies_dict, updated_at). Якщо немає — ({}, {}, 0.0)"""
        if not self._db:
            return {}, {}, 0.0
        import json
        try:
            async with self._db.execute(
                    "SELECT headers_json, cookies_json, updated_at FROM auth_sessions WHERE user_id=? AND exchange=? AND is_active=1",
                    (user_id, exchange),
            ) as cur:
                row = await cur.fetchone()

            if not row:
                return {}, {}, 0.0

            headers = json.loads(row["headers_json"] or "{}")
            cookies = json.loads(row["cookies_json"] or "{}")
            return headers, cookies, float(row["updated_at"])

        except Exception as e:
            logger.error("get_auth_session [%s]: %s", exchange, e)
            return {}, {}, 0.0

    async def invalidate_auth_session(self, exchange: str, user_id: int = 0) -> bool:
        """Позначає сесію як протухшу (is_active=0)."""
        if not self._db:
            return False
        try:
            await self._db.execute(
                "UPDATE auth_sessions SET is_active=0 WHERE user_id=? AND exchange=?",
                (user_id, exchange)
            )
            await self._db.commit()
            logger.warning("Auth session invalidated (burnt out) for %s (user_id=%d)", exchange, user_id)
            return True
        except Exception as e:
            logger.error("invalidate_auth_session [%s]: %s", exchange, e)
            return False
    # ═══════════════════════════════════════════════════════════════════════
    # Scanner Users (multi-user)
    # ═══════════════════════════════════════════════════════════════════════

    async def register_user(
        self,
        user_id: int,
        chat_id: int,
        working_capital: float = 5100.0,
        min_spread_pct: float = 0.5,
        bank_codes: list[str] | None = None,
    ) -> bool:
        """Реєструє нового підписника сканера."""
        if not self._db:
            return False
        import time
        try:
            banks_str = ",".join(bank_codes or ["43", "14", "64"])
            await self._db.execute(
                """INSERT INTO scanner_users
                       (user_id, telegram_chat_id, working_capital, min_spread_pct, bank_codes, is_active, created_at)
                   VALUES (?, ?, ?, ?, ?, 1, ?)
                   ON CONFLICT(user_id) DO UPDATE SET
                       telegram_chat_id = excluded.telegram_chat_id,
                       is_active = 1""",
                (user_id, chat_id, working_capital, min_spread_pct, banks_str, time.time()),
            )
            await self._db.commit()
            return True
        except Exception as e:
            logger.error("register_user [%d]: %s", user_id, e)
            return False

    async def get_active_users(self) -> list[dict]:
        """Повертає всіх активних підписників для розсилки алертів."""
        if not self._db:
            return []
        try:
            async with self._db.execute(
                """SELECT user_id, telegram_chat_id, working_capital,
                          COALESCE(min_amount_uah, 0.0) as min_amount_uah,
                          min_spread_pct, bank_codes,
                          COALESCE(merchant_filters_json, '{}') as merchant_filters_json,
                          COALESCE(is_alerts_active, 1) as is_alerts_active
                   FROM scanner_users WHERE is_active=1 AND COALESCE(is_alerts_active,1)=1"""
            ) as cur:
                rows = await cur.fetchall()
            import json as _json
            return [
                {
                    "user_id":          row["user_id"],
                    "chat_id":          row["telegram_chat_id"],
                    "capital":          float(row["working_capital"]),
                    "min_amount":       float(row["min_amount_uah"]),
                    "min_spread":       float(row["min_spread_pct"]),
                    "bank_codes":       row["bank_codes"].split(","),
                    "merchant_filters": _json.loads(row["merchant_filters_json"] or "{}"),
                }
                for row in rows
            ]
        except Exception as e:
            logger.error("get_active_users: %s", e)
            return []

    async def find_digital_twins(self, merchant_name: str, exclude_exchange: str, minutes: int = 15) -> list[dict]:
        """Шукає унікальні стани лімітів двійників за короткий час (дедуплікація на рівні бази)."""
        if not self._db or not merchant_name:
            return []

        since = time.time() - (minutes * 60)

        async with self._db.execute(
                """
                SELECT DISTINCT exchange, min_limit, max_limit
                FROM merchant_snapshots
                WHERE merchant_name = ? COLLATE NOCASE
                  AND exchange!=? AND recorded_at > ?
                """,
                (merchant_name, exclude_exchange, since),
        ) as cur:
            rows = await cur.fetchall()

        return [dict(r) for r in rows]

    async def get_verdict_timestamp(self, exchange: str, merchant_id: str) -> float:
        if not self._db: return 0.0
        try:
            async with self._db.execute(
                    "SELECT updated_at FROM merchant_verdict WHERE exchange=? AND merchant_id=? ORDER BY updated_at DESC LIMIT 1",
                    (exchange, merchant_id),
            ) as cur:
                row = await cur.fetchone()
            return float(row["updated_at"]) if row else 0.0
        except Exception:
            return 0.0

    async def save_review_snapshot(self, exchange: str, merchant_id: str, pos: int, neg: int, neg_pct: float) -> None:
        if not self._db: return
        await self._db.execute(
            "INSERT INTO merchant_review_history (exchange, merchant_id, positive_count, negative_count, neg_pct, recorded_at) VALUES (?, ?, ?, ?, ?, ?)",
            (exchange, merchant_id, pos, neg, neg_pct, time.time())
        )
        await self._db.commit()

    async def get_review_trend(self, exchange: str, merchant_id: str, days: int = 7) -> dict:
        if not self._db: return {"trend": "stable", "delta": 0.0}
        since = time.time() - (days * 86400)
        async with self._db.execute(
                "SELECT neg_pct FROM merchant_review_history WHERE exchange=? AND merchant_id=? AND recorded_at > ? ORDER BY recorded_at ASC",
                (exchange, merchant_id, since)
        ) as cur:
            rows = await cur.fetchall()

        if len(rows) < 2: return {"trend": "stable", "delta": 0.0}
        delta = float(rows[-1]["neg_pct"]) - float(rows[0]["neg_pct"])
        trend = "worsening" if delta > 3.0 else "improving" if delta < -3.0 else "stable"
        return {"trend": trend, "delta": delta}

    # ==========================================
    # ── БЛОК 2: ТОРГОВІ СЕСІЇ (TRADE SESSIONS) ──
    # ==========================================

    async def create_trade_session(self, strategy: str, route_type: str, network: str, network_fee: float,
                                   gross_profit: float, buy_exchange: str = "") -> int:
        """Створює нову глобальну торгову сесію і повертає її ID."""
        if not self._db:
            return 0

        cursor = await self._db.execute(
            """
            INSERT INTO trade_sessions (strategy, route_type, network, network_fee, gross_profit, session_status,
                                        buy_exchange)
            VALUES (?, ?, ?, ?, ?, 'OPEN', ?)
            """,
            (strategy, route_type, network, network_fee, gross_profit, buy_exchange)
        )
        await self._db.commit()
        return cursor.lastrowid

    async def update_trade_session(self, session_id: int, status: str, buy_leg_id: Optional[int] = None, sell_leg_id: Optional[int] = None) -> None:
        """Оновлює стан торгової сесії."""
        if not self._db:
            return
            
        fields = ["session_status = ?"]
        values = [status]
        
        if buy_leg_id is not None:
            fields.append("buy_leg_id = ?")
            values.append(buy_leg_id)
        if sell_leg_id is not None:
            fields.append("sell_leg_id = ?")
            values.append(sell_leg_id)
            
        if status in ('COMPLETED', 'CANCELLED', 'FAILED'):
            fields.append("completed_at = CURRENT_TIMESTAMP")
            
        values.append(session_id)
        
        query = f"UPDATE trade_sessions SET {', '.join(fields)} WHERE id = ?"
        await self._db.execute(query, tuple(values))
        await self._db.commit()

    async def get_trade_session(self, session_id: int) -> dict | None:
        """Отримує дані торгової сесії по ID."""
        if not self._db:
            return None
        async with self._db.execute("SELECT * FROM trade_sessions WHERE id = ?", (session_id,)) as cur:
            row = await cur.fetchone()
        return dict(row) if row else None
    # ==========================================
    # ── БЛОК 3: АКТИВНІ ОРДЕРИ (ACTIVE TRADES) ──
    # ==========================================

    async def create_active_trade(self, session_id: int, strategy: str, leg: str, route_type: str, 
                                  network: str, network_fee: float, owner_user_id: Optional[int], 
                                  exchange: str, order_id: str, ad_id: str, asset: str, fiat: str, 
                                  price: float, amount: float, fiat_amount: float, status: str) -> int:
        """Записує створений ордер (leg) у базу."""
        if not self._db:
            return 0
            
        cursor = await self._db.execute(
            """
            INSERT INTO active_trades (
                session_id, strategy, leg, route_type, network, network_fee, owner_user_id,
                exchange, order_id, ad_id, asset, fiat, price, amount, fiat_amount, status
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (session_id, strategy, leg, route_type, network, network_fee, owner_user_id,
             exchange, order_id, ad_id, asset, fiat, price, amount, fiat_amount, status)
        )
        await self._db.commit()
        return cursor.lastrowid

    async def update_active_trade_status(self, trade_id: int, new_status: str) -> None:
        """Оновлює статус конкретного ордера (FSM)."""
        if not self._db:
            return
            
        await self._db.execute(
            "UPDATE active_trades SET status = ?, updated_at = CURRENT_TIMESTAMP WHERE id = ?",
            (new_status, trade_id)
        )
        await self._db.commit()

    async def update_active_trade_order_id(self, trade_id: int, new_order_id: str) -> None:
        """Оновлює order_id після того як біржа його повернула."""
        if not self._db:
            return
            
        await self._db.execute(
            "UPDATE active_trades SET order_id = ?, updated_at = CURRENT_TIMESTAMP WHERE id = ?",
            (new_order_id, trade_id)
        )
        await self._db.commit()

    async def get_active_trades_by_status(self, *statuses: str) -> list[dict]:
        """
        Блок 6: Повертає усі активні угоди за списком статусів.
        Використовується при старті для відновлення незавершених торгів.
        Приклад: get_active_trades_by_status('PENDING_PAYMENT', 'PAID_PENDING_RELEASE', 'SELL_PENDING')
        """
        if not self._db or not statuses:
            return []

        placeholders = ", ".join("?" for _ in statuses)
        query = f"""
            SELECT at.*, ts.strategy as session_strategy, ts.route_type as session_route_type,
                   ts.network as session_network, ts.network_fee as session_network_fee,
                   ts.gross_profit as session_gross_profit
            FROM active_trades at
            LEFT JOIN trade_sessions ts ON at.session_id = ts.id
            WHERE at.status IN ({placeholders})
            ORDER BY at.created_at ASC
        """
        try:
            async with self._db.execute(query, tuple(statuses)) as cur:
                rows = await cur.fetchall()
            return [dict(row) for row in rows]
        except Exception as e:
            logger.error("get_active_trades_by_status: %s", e)
            return []

    async def get_active_repricer_sessions(self) -> list[dict]:
        """
        Блок 6: Повертає всі торгові сесії в статусі SELL_IN_PROGRESS.
        Використовується при старті для відновлення AdRepricer.
        """
        if not self._db:
            return []
        try:
            async with self._db.execute(
                """
                SELECT ts.*, at.exchange, at.ad_id, at.price as buy_price,
                       at.amount, at.network_fee
                FROM trade_sessions ts
                JOIN active_trades at ON ts.buy_leg_id = at.id
                WHERE ts.session_status = 'SELL_IN_PROGRESS'
                ORDER BY ts.created_at ASC
                """
            ) as cur:
                rows = await cur.fetchall()
            return [dict(row) for row in rows]
        except Exception as e:
            logger.error("get_active_repricer_sessions: %s", e)
            return []
