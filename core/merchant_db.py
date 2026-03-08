# core/merchant_db.py
"""
Персистентна SQLite база мерчантів.
Зберігає репутацію між перезапусками сканера.
"""
import sqlite3
import logging
import time
from pathlib import Path
from typing import Optional

logger = logging.getLogger("MerchantDB")

DB_PATH = Path("data/merchants.db")


class MerchantDB:
    def __init__(self, db_path: Path = DB_PATH):
        db_path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(str(db_path), check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._init_schema()
        logger.info("MerchantDB ініціалізовано: %s", db_path)

    def _init_schema(self):
        self._conn.executescript("""
            CREATE TABLE IF NOT EXISTS merchants (
                exchange        TEXT NOT NULL,
                merchant_id     TEXT NOT NULL,
                merchant_name   TEXT,
                risk_flag       TEXT DEFAULT 'OK',
                -- Статистика
                total_orders    INTEGER DEFAULT 0,
                completion_pct  REAL DEFAULT 0.0,
                -- Відгуки
                reviews_pos     INTEGER DEFAULT 0,
                reviews_neg     INTEGER DEFAULT 0,
                reviews_neutral INTEGER DEFAULT 0,
                bad_review_pct  REAL DEFAULT 0.0,
                -- Службове
                last_seen       REAL DEFAULT 0,
                reviews_fetched REAL DEFAULT 0,
                first_seen      REAL DEFAULT 0,
                PRIMARY KEY (exchange, merchant_id)
            );

            CREATE TABLE IF NOT EXISTS bad_review_texts (
                exchange        TEXT NOT NULL,
                merchant_id     TEXT NOT NULL,
                review_text     TEXT NOT NULL,
                created_at      REAL DEFAULT 0
            );

            CREATE INDEX IF NOT EXISTS idx_merchant_lookup
                ON merchants (exchange, merchant_id);
        """)
        self._conn.commit()

    # ── Читання ───────────────────────────────────────────────────────────────
    def get(self, exchange: str, merchant_id: str) -> Optional[sqlite3.Row]:
        cur = self._conn.execute(
            "SELECT * FROM merchants WHERE exchange=? AND merchant_id=?",
            (exchange, merchant_id)
        )
        return cur.fetchone()

    def get_bad_reviews(self, exchange: str, merchant_id: str) -> list[str]:
        cur = self._conn.execute(
            "SELECT review_text FROM bad_review_texts WHERE exchange=? AND merchant_id=? LIMIT 5",
            (exchange, merchant_id)
        )
        return [r["review_text"] for r in cur.fetchall()]

    def needs_review_fetch(self, exchange: str, merchant_id: str,
                           ttl_hours: float = 24.0) -> bool:
        """True якщо відгуки ще не тягнули або вже протухли."""
        row = self.get(exchange, merchant_id)
        if row is None:
            return True
        elapsed = time.time() - (row["reviews_fetched"] or 0)
        return elapsed > ttl_hours * 3600

    # ── Запис ────────────────────────────────────────────────────────────────
    def upsert_merchant(self, exchange: str, merchant_id: str,
                        merchant_name: str, total_orders: int,
                        completion_pct: float) -> None:
        now = time.time()
        self._conn.execute("""
            INSERT INTO merchants
                (exchange, merchant_id, merchant_name, total_orders, completion_pct,
                 last_seen, first_seen)
            VALUES (?,?,?,?,?,?,?)
            ON CONFLICT(exchange, merchant_id) DO UPDATE SET
                merchant_name  = excluded.merchant_name,
                total_orders   = excluded.total_orders,
                completion_pct = excluded.completion_pct,
                last_seen      = excluded.last_seen
        """, (exchange, merchant_id, merchant_name, total_orders, completion_pct,
              now, now))
        self._conn.commit()

    def save_reviews(self, exchange: str, merchant_id: str,
                     pos: int, neg: int, neutral: int,
                     bad_texts: list[str]) -> None:
        total = pos + neg + neutral
        bad_pct = (neg / total * 100) if total > 0 else 0.0
        now = time.time()

        self._conn.execute("""
            UPDATE merchants SET
                reviews_pos     = ?,
                reviews_neg     = ?,
                reviews_neutral = ?,
                bad_review_pct  = ?,
                reviews_fetched = ?
            WHERE exchange=? AND merchant_id=?
        """, (pos, neg, neutral, bad_pct, now, exchange, merchant_id))

        # Зберігаємо тексти поганих відгуків
        if bad_texts:
            self._conn.execute(
                "DELETE FROM bad_review_texts WHERE exchange=? AND merchant_id=?",
                (exchange, merchant_id)
            )
            self._conn.executemany(
                "INSERT INTO bad_review_texts VALUES (?,?,?,?)",
                [(exchange, merchant_id, t, now) for t in bad_texts[:10]]
            )

        self._conn.commit()

    def set_risk_flag(self, exchange: str, merchant_id: str, flag: str) -> None:
        self._conn.execute(
            "UPDATE merchants SET risk_flag=? WHERE exchange=? AND merchant_id=?",
            (flag, exchange, merchant_id)
        )
        self._conn.commit()

    def close(self):
        self._conn.close()