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
        await self._db.execute("PRAGMA cache_size=-32000")  # 32 MB кеш
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

                                     CREATE INDEX IF NOT EXISTS idx_verdict_lookup
                                         ON merchant_verdict (exchange, merchant_id);

                                     CREATE INDEX IF NOT EXISTS idx_reviews_lookup
                                         ON merchant_reviews (exchange, merchant_id);

                                     /* 🚀 ІНДЕКСИ ДЛЯ СНАПШОТІВ */
                                     CREATE INDEX IF NOT EXISTS idx_snap_lookup
                                         ON merchant_snapshots (exchange, merchant_id, recorded_at);

                                     CREATE INDEX IF NOT EXISTS idx_snap_ts
                                         ON merchant_snapshots (recorded_at);

                                     /* 🚀 Для find_digital_twins — без цього індексу full table scan */
                                     CREATE INDEX IF NOT EXISTS idx_snap_name
                                         ON merchant_snapshots (merchant_name, exchange, recorded_at);

                                     -- API credentials (зашифровані Fernet)
                                     -- Одна строка на біржу, ключі зберігаються encrypted
                                     CREATE TABLE IF NOT EXISTS user_credentials (
                                         exchange    TEXT NOT NULL PRIMARY KEY,
                                         api_key     TEXT NOT NULL,
                                         api_secret  TEXT NOT NULL,
                                         passphrase  TEXT DEFAULT '',
                                         label       TEXT DEFAULT '',
                                         created_at  REAL DEFAULT 0,
                                         updated_at  REAL DEFAULT 0
                                     );

                                     -- Runtime overrides від UI бота
                                     CREATE TABLE IF NOT EXISTS bot_settings (
                                         key        TEXT NOT NULL PRIMARY KEY,
                                         value      TEXT NOT NULL,
                                         updated_at REAL DEFAULT 0
                                     );
                                     """)
        await self._db.commit()

        await self._ensure_column("merchant_verdict", "save_count", "INTEGER DEFAULT 0")
        # 🚀 ДОДАЄМО СТАТУС ВІДГУКІВ
        await self._ensure_column("merchant_reviews", "status", "TEXT DEFAULT 'OK'")

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
    ) -> None:
        now = time.time()
        t_hash = hash_terms(trade_terms)
        score_delta = VERDICT_SCORE.get(verdict, 0)
        llm_inc = 1 if (source or "").lower() in LLM_SOURCES else 0

        await self._db.execute(
            """
            INSERT INTO merchant_verdict
            (exchange, merchant_id, merchant_name, terms_hash,
             verdict, risk_type, reason, risk_score,
             llm_calls_count, save_count, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?) ON CONFLICT(exchange, merchant_id) DO
            UPDATE SET
                merchant_name = excluded.merchant_name,
                terms_hash = excluded.terms_hash,
                verdict = excluded.verdict,
                risk_type = excluded.risk_type,
                reason = excluded.reason,
                risk_score = MIN (merchant_verdict.risk_score + excluded.risk_score, 200),
                llm_calls_count = merchant_verdict.llm_calls_count + ?,
                save_count = merchant_verdict.save_count + 1,
                updated_at = excluded.updated_at
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
                SELECT updated_at
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
    ) -> bool:
        """
        Зберігає API ключі для біржі (зашифровано Fernet).
        Викликається з bot/commands.py при /connect.
        """
        if not self._db:
            return False
        import time
        try:
            now = time.time()
            await self._db.execute(
                """INSERT INTO user_credentials
                       (exchange, api_key, api_secret, passphrase, label, created_at, updated_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?)
                   ON CONFLICT(exchange) DO UPDATE SET
                       api_key    = excluded.api_key,
                       api_secret = excluded.api_secret,
                       passphrase = excluded.passphrase,
                       label      = excluded.label,
                       updated_at = excluded.updated_at""",
                (
                    exchange,
                    encrypt(api_key),
                    encrypt(api_secret),
                    encrypt(passphrase) if passphrase else "",
                    label,
                    now, now,
                ),
            )
            await self._db.commit()
            logger.info("Credentials saved for %s", exchange)
            return True
        except Exception as e:
            logger.error("save_credentials [%s]: %s", exchange, e)
            return False

    async def get_credentials(self, exchange: str) -> dict | None:
        """
        Повертає розшифровані credentials для біржі або None.
        Повертає: {"api_key": str, "api_secret": str, "passphrase": str}
        """
        if not self._db:
            return None
        try:
            async with self._db.execute(
                "SELECT api_key, api_secret, passphrase, label FROM user_credentials WHERE exchange = ?",
                (exchange,),
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

    async def get_all_credentials(self) -> dict[str, dict]:
        """
        Повертає всі збережені credentials як {exchange: {api_key, api_secret, passphrase}}.
        Для ініціалізації ReviewFetcher і account clients при старті.
        """
        if not self._db:
            return {}
        try:
            async with self._db.execute(
                "SELECT exchange, api_key, api_secret, passphrase, label FROM user_credentials"
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

    async def delete_credentials(self, exchange: str) -> bool:
        """Видаляє credentials для біржі. Викликається з /disconnect."""
        if not self._db:
            return False
        try:
            await self._db.execute(
                "DELETE FROM user_credentials WHERE exchange = ?", (exchange,)
            )
            await self._db.commit()
            logger.info("Credentials deleted for %s", exchange)
            return True
        except Exception as e:
            logger.error("delete_credentials [%s]: %s", exchange, e)
            return False

    async def has_credentials(self, exchange: str) -> bool:
        """Швидка перевірка чи є ключі для біржі."""
        if not self._db:
            return False
        try:
            async with self._db.execute(
                "SELECT 1 FROM user_credentials WHERE exchange = ? LIMIT 1", (exchange,)
            ) as cur:
                return await cur.fetchone() is not None
        except Exception:
            return False

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