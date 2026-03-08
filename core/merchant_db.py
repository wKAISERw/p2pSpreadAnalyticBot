# core/merchant_db.py
"""
Асинхронна SQLite база мерчантів (aiosqlite).
Таблиця: merchant_verdict
"""
import asyncio
import hashlib
import logging
import time
from pathlib import Path
from typing import Optional

import aiosqlite

logger = logging.getLogger("MerchantDB")

DB_PATH = Path("data/merchants.db")

# TTL по статусу
TTL = {
    "OK":          43_200,   # 12 год
    "SUSPICIOUS":  43_200,   # 12 год
    "BLOCK":       259_200,  # 72 год — заблокований довше сидить
    "UNKNOWN":     1_800,    # 30 хв — скоро перепробуємо
}
DEFAULT_TTL = 43_200

# Risk score по вердикту
VERDICT_SCORE = {
    "OK":          0,
    "SUSPICIOUS":  30,
    "BLOCK":       100,
    "UNKNOWN":     10,
}


def hash_terms(trade_terms: str) -> str:
    """MD5 після нормалізації — пробіли і регістр не впливають."""
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
        await self._init_schema()
        logger.info("MerchantDB запущено: %s", self._path)

    async def get_risk_score(self, exchange: str, merchant_id: str) -> int:
        """Повертає накопичений risk_score (0-200)."""
        async with self._db.execute(
            "SELECT risk_score FROM merchant_verdict WHERE exchange=? AND merchant_id=?",
            (exchange, merchant_id)
        ) as cur:
            row = await cur.fetchone()
        return row["risk_score"] if row else 0

    # ── Blacklist ─────────────────────────────────────────────────────────────
    async def is_blacklisted(self, exchange: str, merchant_id: str) -> tuple[bool, str]:
        """Перевірка глобального чорного списку. Повертає (True, reason) або (False, '')."""
        async with self._db.execute(
            "SELECT reason, source FROM global_blacklist "
            "WHERE exchange=? AND merchant_id=?",
            (exchange, merchant_id)
        ) as cur:
            row = await cur.fetchone()
        if row:
            return True, f"[{row['source']}] {row['reason']}"
        return False, ""

    async def add_to_blacklist(self, exchange: str, merchant_id: str,
                               merchant_name: str, reason: str,
                               source: str = "manual") -> None:
        import time as _time
        await self._db.execute("""
            INSERT OR REPLACE INTO global_blacklist
                (exchange, merchant_id, merchant_name, reason, source, added_at)
            VALUES (?,?,?,?,?,?)
        """, (exchange, merchant_id, merchant_name, reason, source, _time.time()))
        await self._db.commit()
        logger.warning("🚫 Blacklist додано: %s [%s] — %s", merchant_name, exchange, reason)

    async def load_blacklist_from_file(self, path: str = "data/blacklist.json") -> int:
        """Завантажує blacklist з JSON файлу при старті."""
        import json, time as _time
        from pathlib import Path as _Path
        p = _Path(path)
        if not p.exists():
            return 0
        entries = json.loads(p.read_text(encoding="utf-8"))
        count = 0
        for e in entries:
            await self.add_to_blacklist(
                e["exchange"], e["merchant_id"],
                e.get("merchant_name", ""),
                e.get("reason", ""),
                e.get("source", "file"),
            )
            count += 1
        logger.info("Blacklist завантажено: %d записів з %s", count, path)
        return count

    async def stop(self) -> None:
        if self._db:
            await self._db.close()
        logger.info("MerchantDB зупинено")

    async def _init_schema(self) -> None:
        await self._db.executescript("""
            CREATE TABLE IF NOT EXISTS merchant_verdict (
                exchange        TEXT NOT NULL,
                merchant_id     TEXT NOT NULL,
                merchant_name   TEXT,
                terms_hash      TEXT,
                verdict         TEXT DEFAULT 'UNKNOWN',
                risk_type       TEXT,
                reason          TEXT,
                risk_score      INTEGER DEFAULT 0,
                llm_calls_count INTEGER DEFAULT 0,
                updated_at      REAL DEFAULT 0,
                PRIMARY KEY (exchange, merchant_id)
            );

            CREATE TABLE IF NOT EXISTS global_blacklist (
                exchange        TEXT NOT NULL,
                merchant_id     TEXT NOT NULL,
                merchant_name   TEXT,
                reason          TEXT,
                source          TEXT,
                added_at        REAL DEFAULT 0,
                PRIMARY KEY (exchange, merchant_id)
            );

            CREATE TABLE IF NOT EXISTS block_log (
                exchange        TEXT NOT NULL,
                merchant_id     TEXT NOT NULL,
                merchant_name   TEXT,
                verdict         TEXT,
                risk_type       TEXT,
                reason          TEXT,
                source          TEXT,
                logged_at       REAL DEFAULT 0
            );

            CREATE INDEX IF NOT EXISTS idx_verdict_lookup
                ON merchant_verdict (exchange, merchant_id);
        """)
        await self._db.commit()

    # ── Читання ───────────────────────────────────────────────────────────────
    async def get_verdict(self, exchange: str, merchant_id: str,
                          current_terms: str) -> Optional[str]:
        async with self._db.execute(
            "SELECT verdict, terms_hash, updated_at, llm_calls_count "
            "FROM merchant_verdict WHERE exchange=? AND merchant_id=?",
            (exchange, merchant_id)
        ) as cur:
            row = await cur.fetchone()

        if row is None:
            return None

        verdict    = row["verdict"]
        terms_hash = row["terms_hash"]
        updated_at = row["updated_at"] or 0
        llm_calls  = row["llm_calls_count"] or 0

        # ✅ ВИПРАВЛЕНО: Захист від перетирання кешу (Cache Thrashing)
        if terms_hash != hash_terms(current_terms):
             # Якщо це заблокований скамер — нам байдуже на зміну тексту.
             # Якщо це свіжий OK (менше години тому) — теж не перевіряємо знову.
             if verdict == "BLOCK" or (verdict == "OK" and (time.time() - updated_at) < 3600):
                 pass
             else:
                 # Тільки в інших випадках скидаємо кеш для перевірки
                 return None

        # Перевірка TTL (час життя кешу)
        ttl = TTL.get(verdict, DEFAULT_TTL)
        if time.time() - updated_at > ttl:
            return None

        if verdict == "UNKNOWN" and llm_calls >= 3:
            return "UNKNOWN"

        return verdict

    async def get_reason(self, exchange: str, merchant_id: str) -> tuple[str, str]:
        """Повертає (risk_type, reason) для відображення в алерті."""
        async with self._db.execute(
            "SELECT risk_type, reason FROM merchant_verdict "
            "WHERE exchange=? AND merchant_id=?",
            (exchange, merchant_id)
        ) as cur:
            row = await cur.fetchone()
        if row:
            return row["risk_type"] or "", row["reason"] or ""
        return "", ""

    # ── Запис ────────────────────────────────────────────────────────────────
    async def save_verdict(self, exchange: str, merchant_id: str,
                           merchant_name: str, trade_terms: str,
                           verdict: str, risk_type: str = "",
                           reason: str = "", source: str = "") -> None:
        now = time.time()
        t_hash = hash_terms(trade_terms)

        score_delta = VERDICT_SCORE.get(verdict, 0)

        await self._db.execute("""
            INSERT INTO merchant_verdict
                (exchange, merchant_id, merchant_name, terms_hash,
                 verdict, risk_type, reason, risk_score, llm_calls_count, updated_at)
            VALUES (?,?,?,?,?,?,?,?,1,?)
            ON CONFLICT(exchange, merchant_id) DO UPDATE SET
                merchant_name   = excluded.merchant_name,
                terms_hash      = excluded.terms_hash,
                verdict         = excluded.verdict,
                risk_type       = excluded.risk_type,
                reason          = excluded.reason,
                risk_score      = MIN(risk_score + ?, 200),
                llm_calls_count = llm_calls_count + 1,
                updated_at      = excluded.updated_at
        """, (exchange, merchant_id, merchant_name, t_hash,
              verdict, risk_type, reason, score_delta, now, score_delta))

        # Логуємо блокування окремо
        if verdict == "BLOCK":
            await self._db.execute("""
                INSERT INTO block_log
                    (exchange, merchant_id, merchant_name, verdict,
                     risk_type, reason, source, logged_at)
                VALUES (?,?,?,?,?,?,?,?)
            """, (exchange, merchant_id, merchant_name, verdict,
                  risk_type, reason, source, now))

        await self._db.commit()
        logger.debug("Збережено вердикт %s для %s [%s]", verdict, merchant_name, exchange)