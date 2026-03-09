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

import hashlib
import logging
import time
from pathlib import Path
from typing import Optional

import aiosqlite

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
    normalized = (trade_terms or "").lower().strip()
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
        await self._db.execute("PRAGMA synchronous=NORMAL")   # безпечно + швидше
        await self._db.execute("PRAGMA cache_size=-32000")     # 32 MB кеш
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
        CREATE TABLE IF NOT EXISTS merchant_verdict (
            exchange TEXT NOT NULL,
            merchant_id TEXT NOT NULL,
            merchant_name TEXT,
            terms_hash TEXT,
            verdict TEXT DEFAULT 'UNKNOWN',
            risk_type TEXT,
            reason TEXT,
            risk_score INTEGER DEFAULT 0,
            llm_calls_count INTEGER DEFAULT 0,
            save_count INTEGER DEFAULT 0,
            updated_at REAL DEFAULT 0,
            PRIMARY KEY (exchange, merchant_id)
        );

        CREATE TABLE IF NOT EXISTS global_blacklist (
            exchange TEXT NOT NULL,
            merchant_id TEXT NOT NULL,
            merchant_name TEXT,
            reason TEXT,
            source TEXT,
            added_at REAL DEFAULT 0,
            PRIMARY KEY (exchange, merchant_id)
        );

        CREATE TABLE IF NOT EXISTS block_log (
            exchange TEXT NOT NULL,
            merchant_id TEXT NOT NULL,
            merchant_name TEXT,
            verdict TEXT,
            risk_type TEXT,
            reason TEXT,
            source TEXT,
            logged_at REAL DEFAULT 0
        );

        CREATE TABLE IF NOT EXISTS merchant_reviews (
            exchange TEXT NOT NULL,
            merchant_id TEXT NOT NULL,
            positive_count INTEGER DEFAULT 0,
            negative_count INTEGER DEFAULT 0,
            neutral_count INTEGER DEFAULT 0,
            bad_texts_json TEXT DEFAULT '[]',
            updated_at REAL DEFAULT 0,
            PRIMARY KEY (exchange, merchant_id)
        );

        CREATE INDEX IF NOT EXISTS idx_verdict_lookup
        ON merchant_verdict (exchange, merchant_id);

        CREATE INDEX IF NOT EXISTS idx_reviews_lookup
        ON merchant_reviews (exchange, merchant_id);
        """)
        await self._db.commit()

        await self._ensure_column("merchant_verdict", "save_count", "INTEGER DEFAULT 0")

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
            WHERE exchange=? AND merchant_id=?
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
            if verdict == "BLOCK" or (verdict == "OK" and (time.time() - updated_at) < 3600):
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
            WHERE exchange=? AND merchant_id=?
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
            (
                exchange, merchant_id, merchant_name, terms_hash,
                verdict, risk_type, reason, risk_score,
                llm_calls_count, save_count, updated_at
            )
            VALUES (?,?,?,?,?,?,?,?,?,?,?)
            ON CONFLICT(exchange, merchant_id) DO UPDATE SET
                merchant_name   = excluded.merchant_name,
                terms_hash      = excluded.terms_hash,
                verdict         = excluded.verdict,
                risk_type       = excluded.risk_type,
                reason          = excluded.reason,
                risk_score      = MIN(merchant_verdict.risk_score + excluded.risk_score, 200),
                llm_calls_count = merchant_verdict.llm_calls_count + ?,
                save_count      = merchant_verdict.save_count + 1,
                updated_at      = excluded.updated_at
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
                VALUES (?,?,?,?,?,?,?,?)
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
            WHERE exchange=? AND merchant_id=?
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
        self,
        exchange: str,
        merchant_id: str,
        positive_count: int,
        negative_count: int,
        neutral_count: int,
        bad_texts: list[str],
    ) -> None:
        import json

        now = time.time()
        bad_texts_json = json.dumps(bad_texts[:20], ensure_ascii=False)

        await self._db.execute(
            """
            INSERT INTO merchant_reviews
            (exchange, merchant_id, positive_count, negative_count, neutral_count, bad_texts_json, updated_at)
            VALUES (?,?,?,?,?,?,?)
            ON CONFLICT(exchange, merchant_id) DO UPDATE SET
                positive_count = excluded.positive_count,
                negative_count = excluded.negative_count,
                neutral_count  = excluded.neutral_count,
                bad_texts_json = excluded.bad_texts_json,
                updated_at     = excluded.updated_at
            """,
            (
                exchange,
                merchant_id,
                max(int(positive_count), 0),
                max(int(negative_count), 0),
                max(int(neutral_count), 0),
                bad_texts_json,
                now,
            ),
        )
        await self._db.commit()

    async def get_reviews_summary(self, exchange: str, merchant_id: str) -> dict:
        import json

        async with self._db.execute(
            """
            SELECT positive_count, negative_count, neutral_count, bad_texts_json, updated_at
            FROM merchant_reviews
            WHERE exchange=? AND merchant_id=?
            """,
            (exchange, merchant_id),
        ) as cur:
            row = await cur.fetchone()

        if not row:
            return {
                "positive": 0,
                "negative": 0,
                "neutral": 0,
                "bad_texts": [],
                "updated_at": 0,
            }

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
        }