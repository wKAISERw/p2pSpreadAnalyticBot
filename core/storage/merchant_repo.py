# core/storage/merchant_repo.py
# Merchant verdicts, blacklist, reviews, snapshots
from __future__ import annotations
import logging, time
from typing import Optional
import aiosqlite
from pathlib import Path
from core.storage.base_db import hash_terms, TTL, DEFAULT_TTL, VERDICT_SCORE, LLM_SOURCES
logger = logging.getLogger(__name__)

# Скільки чекати перед повтором після технічного збою збору відгуків.
# П'ять хвилин — компроміс: сесія чи API встигають ожити, але кожен цикл
# сканера (секунди) вже не б'ється в те саме місце.
RETRY_AFTER_FAILURE_SEC = 300.0

# «Відгуків немає» — не збій, але й не вічна істина: у мерчанта вони
# з'являються. Перепитуємо частіше за звичайний TTL, та не щоцикла.
NO_FEEDBACK_RECHECK_SEC = 3600.0

class MerchantRepo:
    """Verdicts, blacklist, reviews, snapshots."""

    # owner_id → (момент читання, індекс за id, індекс за іменем).
    # Оголошено на класі, бо MerchantRepo — міксин без власного __init__:
    # доступ іде через _user_bl_cache, який лениво створює словник на
    # інстансі, тож різні MerchantDB не ділять кеш між собою.
    _user_bl_cache_attr = "__user_bl_cache"

    @property
    def _user_bl_cache(self) -> dict:
        cache = self.__dict__.get(self._user_bl_cache_attr)
        if cache is None:
            cache = {}
            self.__dict__[self._user_bl_cache_attr] = cache
        return cache

    async def get_risk_score(self, exchange: str, merchant_id: str) -> int:
        async with self._db.execute(
                "SELECT risk_score FROM merchant_verdict WHERE exchange=? AND merchant_id=?",
                (exchange, merchant_id),
        ) as cur:
            row = await cur.fetchone()
        return row["risk_score"] if row else 0

    async def is_blacklisted(self, exchange: str, merchant_id: str, merchant_name: str = "") -> tuple[bool, str]:
        # 1. Спершу шукаємо точний збіг по ID на даній біржі
        async with self._db.execute(
                "SELECT reason, source FROM global_blacklist WHERE exchange=? AND merchant_id=?",
                (exchange, merchant_id),
        ) as cur:
            row = await cur.fetchone()
        if row:
            return True, f"[{row['source']}] {row['reason']}"

        # 2. Якщо не знайдено, шукаємо збіг по імені (case-insensitive) на будь-якій біржі
        if merchant_name:
            async with self._db.execute(
                    "SELECT exchange, reason, source FROM global_blacklist WHERE LOWER(merchant_name) = LOWER(?)",
                    (merchant_name.strip(),),
            ) as cur:
                rows = await cur.fetchall()
            
            for r in rows:
                if r["exchange"].lower() == exchange.lower():
                    return True, f"[{r['source']}] {r['reason']}"
                else:
                    return True, f"[{r['source']}] {r['reason']} (blacklist на {r['exchange']})"

            # 3. Якщо все одно не знайдено, робимо розумне очищення від спецсимволів/емодзі (Fuzzy match)
            async with self._db.execute(
                    "SELECT exchange, merchant_name, reason, source FROM global_blacklist"
            ) as cur:
                all_rows = await cur.fetchall()
            
            def clean_name(s: str) -> str:
                if not s:
                    return ""
                return "".join(c for c in s.lower() if c.isalnum())
            
            target_cleaned = clean_name(merchant_name)
            if target_cleaned:
                for r in all_rows:
                    db_name = r["merchant_name"]
                    if db_name and clean_name(db_name) == target_cleaned:
                        if r["exchange"].lower() == exchange.lower():
                            return True, f"[{r['source']}] {r['reason']}"
                        else:
                            return True, f"[{r['source']}] {r['reason']} (blacklist на {r['exchange']})"

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

    # ── Персональний чорний список ────────────────────────────────────────
    #
    # Спільний global_blacklist вище діє на всіх і наповнюється ризик-движком
    # та адміністратором. Цей — особистий: кожен банить сам собі, і чужі
    # алерти від цього не змінюються.
    #
    # Читається він не там, де рахується скоринг, а там, де вже відомий
    # user_id (alert_dispatcher, taker_scanner). Щоб не робити запит на
    # кожен ордер, індекс власника кешується на кілька секунд — за цикл
    # сканера він однаково не встигає застаріти.

    _USER_BL_TTL = 10.0

    @staticmethod
    def _clean_merchant_name(value: str) -> str:
        """Ім'я без емодзі й розділових — мерчанти люблять їх міняти."""
        if not value:
            return ""
        return "".join(c for c in value.lower() if c.isalnum())

    async def add_user_blacklist(
            self,
            owner_id: int,
            exchange: str,
            merchant_id: str,
            merchant_name: str = "",
            reason: str = "",
    ) -> None:
        await self._db.execute(
            """INSERT OR REPLACE INTO user_blacklist
               (owner_id, exchange, merchant_id, merchant_name, reason, added_at)
               VALUES (?,?,?,?,?,?)""",
            (int(owner_id), exchange, str(merchant_id), merchant_name, reason, time.time()),
        )
        await self._db.commit()
        self._user_bl_cache.pop(int(owner_id), None)
        logger.info("🚫 Особистий blacklist %s: %s [%s]", owner_id, merchant_name or merchant_id, exchange)

    async def remove_user_blacklist(self, owner_id: int, exchange: str, merchant_id: str) -> bool:
        cursor = await self._db.execute(
            "DELETE FROM user_blacklist WHERE owner_id=? AND exchange=? AND merchant_id=?",
            (int(owner_id), exchange, str(merchant_id)),
        )
        await self._db.commit()
        self._user_bl_cache.pop(int(owner_id), None)
        return cursor.rowcount > 0

    async def get_user_blacklist(self, owner_id: int) -> list[dict]:
        async with self._db.execute(
            """SELECT exchange, merchant_id, merchant_name, reason, added_at
               FROM user_blacklist WHERE owner_id=? ORDER BY added_at DESC""",
            (int(owner_id),),
        ) as cur:
            rows = await cur.fetchall()
        return [dict(r) for r in rows]

    async def _user_blacklist_index(self, owner_id: int) -> tuple[dict, dict]:
        """
        (за id, за очищеним іменем) → reason. Обидва ключі вміщують біржу,
        крім імені: мерчанта, поміченого на одній біржі, ховаємо всюди —
        так само, як це робить спільний список.
        """
        owner_id = int(owner_id)
        cached = self._user_bl_cache.get(owner_id)
        now = time.monotonic()
        if cached and now - cached[0] < self._USER_BL_TTL:
            return cached[1], cached[2]

        by_id: dict[tuple[str, str], str] = {}
        by_name: dict[str, str] = {}
        for row in await self.get_user_blacklist(owner_id):
            reason = row.get("reason") or "особистий чорний список"
            by_id[(str(row["exchange"]).lower(), str(row["merchant_id"]))] = reason
            cleaned = self._clean_merchant_name(row.get("merchant_name") or "")
            if cleaned:
                by_name[cleaned] = reason

        self._user_bl_cache[owner_id] = (now, by_id, by_name)
        return by_id, by_name

    async def is_user_blacklisted(
            self, owner_id: int, exchange: str, merchant_id: str, merchant_name: str = ""
    ) -> tuple[bool, str]:
        if not owner_id:
            return False, ""
        by_id, by_name = await self._user_blacklist_index(owner_id)

        reason = by_id.get((str(exchange).lower(), str(merchant_id)))
        if reason:
            return True, reason

        cleaned = self._clean_merchant_name(merchant_name)
        if cleaned and cleaned in by_name:
            return True, by_name[cleaned]

        return False, ""

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
        return rec if rec in ("APPROVE", "CONDITIONAL", "REJECT", "PENDING", "RECHECKING") else "PENDING"

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
        # Скор ПОТОЧНОГО вердикту, а не приріст. Раніше тут був delta, який
        # додавався до попереднього значення — через це risk_score ріс від
        # самого факту перевірки (кожен UNKNOWN +10, кожен SUSPICIOUS +30)
        # і будь-який мерчант рано чи пізно доповзав до 200.
        verdict_score = VERDICT_SCORE.get(verdict, 0)
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
             llm_calls_count, save_count, updated_at, trade_recommendation, terms_summary, reviews_analysis,
             llm_decision)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?) ON CONFLICT(exchange, merchant_id) DO
            UPDATE SET
                merchant_name = excluded.merchant_name,
                terms_hash = excluded.terms_hash,
                verdict = excluded.verdict,
                risk_type = excluded.risk_type,
                reason = excluded.reason,
                /* Скор поточного вердикту як підлога + згасання історії на 30%
                   за кожну перевірку. Мерчант, який щойно був BLOCK, не стає
                   "чистим" з першої ж OK-перевірки (100 -> 70 -> 49 -> 34...),
                   але й не інфлюється від повторних перевірок: стабільний
                   SUSPICIOUS назавжди лишається 30, а не росте до 200. */
                risk_score = MAX(excluded.risk_score,
                                 CAST(merchant_verdict.risk_score * 0.7 AS INTEGER)),
                llm_calls_count = merchant_verdict.llm_calls_count + ?,
                save_count = merchant_verdict.save_count + 1,
                updated_at = excluded.updated_at,
                trade_recommendation = excluded.trade_recommendation,
                terms_summary = excluded.terms_summary,
                reviews_analysis = excluded.reviews_analysis,
                /* Хто саме ухвалив рішення. Колонка існувала з самого
                   початку, але в INSERT її не було жодного разу — у базі
                   всі 1290 рядків мали 'UNKNOWN', і дізнатись, яка модель
                   винесла вердикт, можна було лише з logs/llm_decisions. */
                llm_decision = excluded.llm_decision
            """,
            (
                exchange,
                merchant_id,
                merchant_name,
                t_hash,
                verdict,
                risk_type,
                reason,
                verdict_score,
                llm_inc,
                1,
                now,
                trade_recommendation,
                terms_summary,
                reviews_analysis,
                (source or "unknown")[:64],
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
        """Позначає мерчанта як 'AI перепровіряє' — вердикт інвалідовано, LLM перезапущено.

        Також оновлює updated_at щоб verdict_ts відображав момент початку перепровірки.
        Це запобігає повторному спрацьовуванню умови rev_updated > verdict_ts на
        наступному циклі сканера (бо після save_verdict updated_at знову оновиться).
        """
        if not self._db:
            return
        now = time.time()
        await self._db.execute(
            "UPDATE merchant_verdict SET trade_recommendation = 'RECHECKING', updated_at = ? "
            "WHERE exchange = ? AND merchant_id = ?",
            (now, exchange, merchant_id),
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

        age = time.time() - updated_at

        # NO_SESSION: re-check кожні 10 хвилин — як тільки сесія з'явиться,
        # всі мерчанти підтягнуть відгуки протягом ~10m без ручних дій
        if status == "NO_SESSION":
            return age > 600.0

        # Технічні збої: повторюємо, але не щоцикла.
        #
        # Раніше тут стояло беззастережне `return True`, без огляду на
        # updated_at. Тобто мерчант зі статусом API_ERROR перезапитувався
        # КОЖЕН цикл сканера, нескінченно. Degraded-mode cooldown у
        # `fetch_now` рятував лише частково: він вмикається після трьох
        # відмов поспіль і діє на всю біржу, а не на конкретного мерчанта.
        if status in ("PENDING", "UNAVAILABLE", "API_ERROR", "SESSION_EXPIRED"):
            return age > RETRY_AFTER_FAILURE_SEC

        # NO_FEEDBACK — не збій, а успішна відповідь: відгуків справді немає.
        # Тримати його в списку «пробувати без упину» означало довбати API
        # заради нуля, який ми вже знаємо. Перепитуємо за звичайним TTL, але
        # частіше, ніж успішні: у мерчанта могли з'явитись перші відгуки.
        ttl_sec = review_ttl_hours * 3600.0
        if status == "NO_FEEDBACK":
            return age > min(ttl_sec, NO_FEEDBACK_RECHECK_SEC)

        return age > ttl_sec

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
             error_reason, data_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?) ON CONFLICT(exchange, merchant_id) DO
            UPDATE SET
                positive_count = excluded.positive_count,
                negative_count = excluded.negative_count,
                neutral_count = excluded.neutral_count,
                bad_texts_json = excluded.bad_texts_json,
                updated_at = excluded.updated_at,
                status = excluded.status,
                error_reason = excluded.error_reason,
                data_at = excluded.data_at
            """,
            (exchange, merchant_id, max(int(positive_count), 0), max(int(negative_count), 0),
             max(int(neutral_count), 0), bad_texts_json, now, status, err, now),
        )
        await self._db.commit()

    async def mark_reviews_unavailable(
            self, exchange: str, merchant_id: str,
            status: str, error_reason: str = "",
    ) -> None:
        """
        Позначає, що відгуки зараз недоступні — НЕ чіпаючи вже відомі.

        Раніше кожен технічний збій ішов через `save_reviews(..., 0, 0, 0, [])`,
        а це upsert, який перезаписує всі колонки. Мерчант, чиї 500 відгуків і
        тексти скарг ми успішно зібрали вчора, після однієї невдалої спроби
        лишався з нулями: ми не просто не дізнались нового — ми стирали те,
        що знали. У базі це видно як 2257 рядків NO_SESSION, усі з нульовими
        лічильниками й без текстів.

        Тут міняються тільки `status`, `error_reason` і `updated_at`. Лічильники
        й тексти лишаються останніми відомими, і вердикт може чесно сказати
        «свіжіших дістати не вдалось» замість «відгуків немає».

        Рядок створюється, лише якщо його не було зовсім — тоді нулі чесні:
        ми справді нічого не знаємо про цього мерчанта.
        """
        now = time.time()
        err = (error_reason or "")[:500]

        await self._db.execute(
            """
            INSERT INTO merchant_reviews
            (exchange, merchant_id, positive_count, negative_count, neutral_count,
             bad_texts_json, updated_at, status, error_reason)
            VALUES (?, ?, 0, 0, 0, '[]', ?, ?, ?)
            ON CONFLICT(exchange, merchant_id) DO UPDATE SET
                updated_at   = excluded.updated_at,
                status       = excluded.status,
                error_reason = excluded.error_reason
            """,
            (exchange, merchant_id, now, status, err),
        )
        await self._db.commit()

    async def get_reviews_summary(self, exchange: str, merchant_id: str) -> dict:
        import json
        async with self._db.execute(
                "SELECT positive_count, negative_count, neutral_count, bad_texts_json, updated_at, status, "
                "COALESCE(error_reason, '') as error_reason, COALESCE(data_at, 0) as data_at "
                "FROM merchant_reviews WHERE exchange=? AND merchant_id=?",
                (exchange, merchant_id),
        ) as cur:
            row = await cur.fetchone()

        if not row:
            return {"positive": 0, "negative": 0, "neutral": 0, "bad_texts": [], "updated_at": 0, "status": "UNKNOWN",
                    "error_reason": "", "data_at": 0}

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
            # Коли зібрано самі відгуки. Нуль означає, що успішного збору не
            # було жодного разу — тоді нулі в лічильниках чесні.
            "data_at": row["data_at"] or 0,
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

    async def save_sent_alert(
        self,
        exchange: str,
        merchant_id: str,
        chat_id: int,
        message_ids: list[int],
        alert_dict: dict,
        display_settings: dict,
        is_sniper: bool,
    ) -> None:
        import json
        if not self._db:
            return
        now = time.time()
        msg_ids_json = json.dumps(message_ids)
        alert_json = json.dumps(alert_dict, default=str)
        display_json = json.dumps(display_settings)
        is_sniper_int = 1 if is_sniper else 0
        
        await self._db.execute(
            """
            INSERT OR REPLACE INTO sent_alerts
            (exchange, merchant_id, chat_id, message_ids_json, sent_at, alert_json, display_settings_json, is_sniper_match)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (exchange, merchant_id, chat_id, msg_ids_json, now, alert_json, display_json, is_sniper_int)
        )
        await self._db.commit()
        await self.prune_sent_alerts()

    async def get_recent_sent_alerts(self, exchange: str, merchant_id: str, max_age_seconds: int = 1800) -> list[dict]:
        import json
        if not self._db:
            return []
        since = time.time() - max_age_seconds
        async with self._db.execute(
            """
            SELECT chat_id, message_ids_json, alert_json, display_settings_json, is_sniper_match, sent_at
            FROM sent_alerts
            WHERE exchange = ? AND merchant_id = ? AND sent_at > ?
            ORDER BY sent_at DESC
            """,
            (exchange, merchant_id, since)
        ) as cur:
            rows = await cur.fetchall()
            
        out = []
        for r in rows:
            try:
                msg_ids = json.loads(r["message_ids_json"])
                alert_dict = json.loads(r["alert_json"])
                display_settings = json.loads(r["display_settings_json"])
                out.append({
                    "chat_id": r["chat_id"],
                    "message_ids": msg_ids,
                    "alert_dict": alert_dict,
                    "display_settings": display_settings,
                    "is_sniper_match": bool(r["is_sniper_match"]),
                    "sent_at": r["sent_at"],
                })
            except Exception as e:
                logger.error("Error parsing sent alert row: %s", e)
        return out

    async def prune_sent_alerts(self, max_age_seconds: int = 1800) -> None:
        if not self._db:
            return
        limit = time.time() - max_age_seconds
        await self._db.execute("DELETE FROM sent_alerts WHERE sent_at < ?", (limit,))
        await self._db.commit()

    async def update_sent_alert_dict(self, chat_id: int, message_ids: list[int], alert_dict: dict) -> None:
        import json
        if not self._db:
            return
        msg_ids_json = json.dumps(message_ids)
        alert_json = json.dumps(alert_dict, default=str)
        await self._db.execute(
            "UPDATE sent_alerts SET alert_json = ? WHERE chat_id = ? AND message_ids_json = ?",
            (alert_json, chat_id, msg_ids_json)
        )
        await self._db.commit()

    # ═══════════════════════════════════════════════════════════════════════
    # API Credentials (encrypted storage)
    # ═══════════════════════════════════════════════════════════════════════

