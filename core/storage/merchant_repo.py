# core/storage/merchant_repo.py
# Merchant verdicts, blacklist, reviews, snapshots
from __future__ import annotations
import logging, time
from typing import Optional
import aiosqlite
from pathlib import Path
from core.storage.base_db import hash_terms, TTL, DEFAULT_TTL, VERDICT_SCORE, LLM_SOURCES
logger = logging.getLogger(__name__)

class MerchantRepo:
    """Verdicts, blacklist, reviews, snapshots."""

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
            # BLOCK — жорсткий вердикт, зберігаємо незалежно від зміни умов.
            # OK / SUSPICIOUS — умови змінились → негайна перепровірка LLM.
            # (hash_terms стріпає цифри — зміна лише лімітів НЕ змінює хеш)
            if verdict == "BLOCK":
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
                "SELECT trade_recommendation, verdict FROM merchant_verdict "
                "WHERE exchange = ? AND merchant_id = ?",
                (exchange, merchant_id),
        ) as cur:
            row = await cur.fetchone()
        if row is None:
            return "PENDING"
        rec = (row["trade_recommendation"] or "PENDING").strip().upper()
        # Derive from verdict if trade_recommendation was never set
        if rec == "PENDING":
            verdict = (row["verdict"] or "").strip().upper()
            if verdict and verdict != "UNKNOWN":
                _derive = {"OK": "APPROVE", "SUSPICIOUS": "CONDITIONAL", "BLOCK": "REJECT"}
                rec = _derive.get(verdict, "PENDING")
        return rec if rec in ("APPROVE", "CONDITIONAL", "REJECT", "PENDING") else "PENDING"

    async def get_trade_recommendation_full(
            self, exchange: str, merchant_id: str
    ) -> tuple[str, str, str, str, str]:
        """
        Повертає (recommendation, verdict, reason, terms_summary, reviews_analysis) — повну інфу від LLM.
        Якщо trade_recommendation ще PENDING, але verdict вже є —
        автоматично виводимо рекомендацію з verdict.
        """
        if not self._db:
            return "PENDING", "", "", "", ""
        async with self._db.execute(
                "SELECT trade_recommendation, verdict, reason, COALESCE(terms_summary, '') as terms_summary, COALESCE(reviews_analysis, '') as reviews_analysis FROM merchant_verdict "
                "WHERE exchange = ? AND merchant_id = ?",
                (exchange, merchant_id),
        ) as cur:
            row = await cur.fetchone()
        if row is None:
            return "PENDING", "", "", "", ""
        rec = (row["trade_recommendation"] or "PENDING").strip().upper()
        verdict = (row["verdict"] or "").strip().upper()
        reason = (row["reason"] or "").strip()
        terms_summary = (row["terms_summary"] or "").strip()
        reviews_analysis = (row["reviews_analysis"] or "").strip()

        # Якщо trade_recommendation ще PENDING але verdict вже є — derive
        if rec == "PENDING" and verdict and verdict != "UNKNOWN":
            _derive = {"OK": "APPROVE", "SUSPICIOUS": "CONDITIONAL", "BLOCK": "REJECT"}
            rec = _derive.get(verdict, "PENDING")

        if rec not in ("APPROVE", "CONDITIONAL", "REJECT", "PENDING", "RECHECKING"):
            rec = "PENDING"
        return rec, verdict, reason, terms_summary, reviews_analysis

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
            terms_summary: str = "",  # 🔘 AI вижимка умов
            reviews_analysis: str = "",  # 📝 AI вижимка відгуків
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
             llm_calls_count, save_count, updated_at, trade_recommendation, terms_summary, reviews_analysis)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?) ON CONFLICT(exchange, merchant_id) DO
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
                trade_recommendation = excluded.trade_recommendation,
                terms_summary = excluded.terms_summary,
                reviews_analysis = excluded.reviews_analysis
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
                trade_recommendation,
                terms_summary,
                reviews_analysis,
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

    async def mark_rechecking(self, exchange: str, merchant_id: str) -> None:
        """Позначає мерчанта як 'AI перепровіряє' — вердикт інвалідовано, LLM перезапущено."""
        if not self._db:
            return
        await self._db.execute(
            "UPDATE merchant_verdict SET trade_recommendation = 'RECHECKING' "
            "WHERE exchange = ? AND merchant_id = ?",
            (exchange, merchant_id),
        )
        await self._db.commit()

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

        # NO_SESSION: re-check кожні 10 хвилин — як тільки сесія з'явиться,
        # всі мерчанти підтягнуть відгуки протягом ~10m без ручних дій
        if status == "NO_SESSION":
            return (time.time() - updated_at) > 600.0

        # PENDING / технічні збої: завжди потребує перефетч
        if status in ("PENDING", "UNAVAILABLE", "API_ERROR", "SESSION_EXPIRED"):
            return True

        ttl_sec = review_ttl_hours * 3600.0
        return (time.time() - updated_at) > ttl_sec

    async def save_reviews(
            self, exchange: str, merchant_id: str,
            positive_count: int, negative_count: int, neutral_count: int,
            bad_texts: list[str], status: str = "OK", error_reason: str = ""
    ) -> None:
        import json
        now = time.time()
        bad_texts_json = json.dumps(bad_texts[:20], ensure_ascii=False)
        err = (error_reason or "")[:500]

        await self._db.execute(
            """
            INSERT INTO merchant_reviews
            (exchange, merchant_id, positive_count, negative_count, neutral_count, bad_texts_json, updated_at, status,
             error_reason)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?) ON CONFLICT(exchange, merchant_id) DO
            UPDATE SET
                positive_count = excluded.positive_count,
                negative_count = excluded.negative_count,
                neutral_count = excluded.neutral_count,
                bad_texts_json = excluded.bad_texts_json,
                updated_at = excluded.updated_at,
                status = excluded.status,
                error_reason = excluded.error_reason
            """,
            (exchange, merchant_id, max(int(positive_count), 0), max(int(negative_count), 0),
             max(int(neutral_count), 0), bad_texts_json, now, status, err),
        )
        await self._db.commit()

    async def get_reviews_summary(self, exchange: str, merchant_id: str) -> dict:
        import json
        async with self._db.execute(
                "SELECT positive_count, negative_count, neutral_count, bad_texts_json, updated_at, status, COALESCE(error_reason, '') as error_reason "
                "FROM merchant_reviews WHERE exchange=? AND merchant_id=?",
                (exchange, merchant_id),
        ) as cur:
            row = await cur.fetchone()

        if not row:
            return {"positive": 0, "negative": 0, "neutral": 0, "bad_texts": [], "updated_at": 0, "status": "UNKNOWN",
                    "error_reason": ""}

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
            "status": row["status"] or "OK",
            "error_reason": row["error_reason"] or "",
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

    async def get_best_sell_price(self, exchange: str = "", minutes: int = 5) -> float:
        """
        Повертає найнижчу ціну sell-ордера (хто продає USDT) з останніх снапшотів.
        Це sell_book_top — найвигідніша для покупця ціна в стакані.

        Фільтрує по side='sell'. Якщо записів з side немає (старі снапшоти) —
        повертає 0.0, що тригерить fallback у PriceAdvisor.
        """
        if not self._db:
            return 0.0
        try:
            since = time.time() - (minutes * 60)
            if exchange:
                async with self._db.execute(
                        """SELECT MIN(price) as best_price
                           FROM merchant_snapshots
                           WHERE recorded_at > ?
                             AND exchange = ?
                             AND price > 0
                             AND side = 'sell'""",
                        (since, exchange),
                ) as cur:
                    row = await cur.fetchone()
            else:
                async with self._db.execute(
                        """SELECT MIN(price) as best_price
                           FROM merchant_snapshots
                           WHERE recorded_at > ?
                             AND price > 0
                             AND side = 'sell'""",
                        (since,),
                ) as cur:
                    row = await cur.fetchone()
            return float(row["best_price"]) if row and row["best_price"] else 0.0
        except Exception as e:
            logger.error("get_best_sell_price: %s", e)
            return 0.0

    # ═══════════════════════════════════════════════════════════════════════
    # API Credentials (encrypted storage)
    # ═══════════════════════════════════════════════════════════════════════

