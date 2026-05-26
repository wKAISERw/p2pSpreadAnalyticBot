# core/workers/review_fetcher.py
"""
ReviewFetcher v2.0 — повний рефакторинг.

Ключові виправлення:
  1. pos/neg/neutral більше не нулі — беремо з профілю мерчанта
  2. Пріоритетна черга (urgent) для нових мерчантів
  3. Окремий аналіз тексту відгуків по review_only правилах
  4. Degraded mode залишено без змін
"""

from __future__ import annotations

import asyncio
import logging
from contextlib import suppress
from typing import Optional, TYPE_CHECKING

from core.storage.merchant_db import MerchantDB
from core.analysis.rules import ALL_RULES

if TYPE_CHECKING:
    from infrastructure.http.binance_client import BinanceClient
    from infrastructure.http.bybit_p2p_client import BybitP2PClient
    from infrastructure.http.okx_client import OkxClient
    from infrastructure.http.mexc_client import MexcClient

logger = logging.getLogger("ReviewFetcher")

# Затримки між запитами на біржу (rate limiting)
RATE_LIMITS = {
    "Binance": 2.0,
    "Bybit": 1.5,
    "OKX": 1.5,
    "MEXC": 1.5,  # 🚀 ДОДАНО
}

# Пороги для автоматичного флагування
BAD_REVIEW_THRESHOLD_PCT = 15.0

# Базові ключові слова для швидкого pre-фільтру
_BASIC_BAD = [
    "scam", "шахрай", "кидало", "кинув", "розвів", "fraud",
    "fake", "не платить", "обманув", "обдурив", "кинули",
]

# Тільки review_only правила для аналізу текстів
_REVIEW_ONLY_RULES = [r for r in ALL_RULES if getattr(r, "review_only", False)]

# Усі правила для цільових категорій (для швидкого фільтру)
_TARGET_CATEGORIES = {"TRIANGLE", "CASINO", "FINCRIME", "CHARGEBACK", "APPEAL_PRESSURE"}


# ─────────────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────────────

def _has_bad_keywords(text: str) -> bool:
    """
    Дворівневий фільтр:
    1. Швидкий пошук базових слів (str.contains)
    2. Regex по цільових категоріях
    """
    if not text:
        return False
    t = text.lower()
    if any(w in t for w in _BASIC_BAD):
        return True
    for rule in ALL_RULES:
        if rule.category in _TARGET_CATEGORIES:
            if rule.pattern.search(t):
                return True
    return False


def _analyze_review_text(text: str) -> dict:
    """
    Повноцінний аналіз тексту відгуку по review_only правилах.
    Повертає: {score, categories, top_excerpt}
    """
    if not text or not _REVIEW_ONLY_RULES:
        return {"score": 0, "categories": [], "top_excerpt": ""}

    t = text.lower()
    score = 0
    categories: list[str] = []
    top_excerpt = ""

    for rule in _REVIEW_ONLY_RULES:
        m = rule.pattern.search(t)
        if m:
            score += rule.weight
            if rule.category not in categories:
                categories.append(rule.category)
            if not top_excerpt:
                start = max(0, m.start() - 20)
                end = min(len(t), m.end() + 20)
                top_excerpt = t[start:end].strip()

    return {
        "score": min(100, max(0, score)),
        "categories": categories,
        "top_excerpt": top_excerpt[:100],
    }


def _enrich_bad_text(text: str) -> dict:
    """Збагачує текст відгуку аналітикою для збереження в БД."""
    analysis = _analyze_review_text(text)
    return {
        "text": text[:300],
        "score": analysis["score"],
        "categories": analysis["categories"],
        "excerpt": analysis["top_excerpt"],
    }


# ─────────────────────────────────────────────────────────────────────────────
# ReviewFetcher
# ─────────────────────────────────────────────────────────────────────────────

class ReviewFetcher:
    def __init__(
            self,
            db: MerchantDB,
            max_queue: int = 500,
            review_ttl_hours: float = 24.0,
            binance_client=None,
            bybit_client=None,
            okx_client=None,
            mexc_client=None,  # 🚀 ДОДАНО
    ):
        self._db = db
        self._review_ttl = review_ttl_hours

        self._urgent_queue: asyncio.Queue[tuple[str, str]] = asyncio.Queue(maxsize=100)
        self._queue: asyncio.Queue[tuple[str, str]] = asyncio.Queue(maxsize=max_queue)

        self._worker_task: Optional[asyncio.Task] = None
        self._processed = 0
        self._errors = 0
        self._pending: set[tuple[str, str]] = set()

        # In-memory кеш для швидкого визначення нових мерчантів (замість повільних запитів до БД)
        self._known_merchants: set[tuple[str, str]] = set()

        self._binance: Optional["BinanceClient"] = binance_client
        self._bybit: Optional["BybitP2PClient"] = bybit_client
        self._okx: Optional["OkxClient"] = okx_client
        self._mexc: Optional["MexcClient"] = None  # 🚀 ДОДАНО
        self._exchange_fails: dict[str, int] = {"Binance": 0, "Bybit": 0, "OKX": 0, "MEXC": 0}
        self._exchange_cooldown: dict[str, float] = {"Binance": 0.0, "Bybit": 0.0, "OKX": 0.0, "MEXC": 0.0}

    def bind_clients(self, binance=None, bybit=None, okx=None, mexc=None) -> None:
        if binance is not None: self._binance = binance
        if bybit is not None: self._bybit = bybit
        if okx is not None: self._okx = okx
        if mexc is not None: self._mexc = mexc # 🚀 ДОДАНО
        logger.info(
            "ReviewFetcher clients bound: Binance=%s(session) Bybit=%s(session) OKX=%s(session) MEXC=%s(public)",
            "✅" if self._binance else "❌",
            "✅" if self._bybit else "❌",
            "✅" if self._okx else "❌",
            "✅" if self._mexc else "❌",
        )

    async def start(self) -> None:
        if self._worker_task and not self._worker_task.done():
            logger.debug("ReviewFetcher start skipped: already running")
            return
        self._worker_task = asyncio.create_task(
            self._worker_loop(), name="review-fetcher"
        )
        logger.info(
            "ReviewFetcher запущено | ttl=%.1fh | urgent_q=%d | normal_q=%d | clients: B=%s By=%s OKX=%s MEXC=%s",
            self._review_ttl,
            self._urgent_queue.maxsize,
            self._queue.maxsize,
            "✅" if self._binance else "❌",
            "✅" if self._bybit else "❌",
            "✅" if self._okx else "❌",
            "✅" if self._mexc else "❌",
        )

    async def stop(self) -> None:
        if self._worker_task:
            self._worker_task.cancel()
            with suppress(asyncio.CancelledError):
                await self._worker_task
            self._worker_task = None
        logger.info(
            "ReviewFetcher зупинено. Оброблено: %d, помилок: %d, pending: %d",
            self._processed, self._errors, len(self._pending),
        )

    def schedule(self, exchange: str, merchant_id: str) -> bool:
        """
        Ставить мерчанта в чергу на завантаження відгуків.
        Fast in-memory перевірка: якщо мерчанта ще немає в кеші, він йде в urgent_queue.
        """
        if exchange not in RATE_LIMITS or not merchant_id:
            return False

        key = (exchange, merchant_id)
        if key in self._pending:
            return False

        # Магія швидкості: визначаємо urgent без запиту до БД!
        urgent = key not in self._known_merchants
        target = self._urgent_queue if urgent else self._queue

        try:
            target.put_nowait(key)
            self._pending.add(key)
            return True
        except asyncio.QueueFull:
            return False

    async def fetch_now(self, exchange: str, merchant_id: str) -> dict:
        """
        🚀 СИНХРОННИЙ ФЕТЧ: Викликається напряму з RiskEngine для нових мерчантів,
        щоб уникнути 'стану перегонів' (Race Condition), коли ордер аналізується швидше,
        ніж завантажаться його відгуки.
        """
        if not merchant_id:
            return {"positive": 0, "negative": 0, "neutral": 0, "bad_texts": [], "status": "UNKNOWN", "error_reason": "empty merchant_id"}

        if exchange not in RATE_LIMITS:
            # Біржа не підтримує API відгуків (Wallet, CryptoBot)
            logger.debug("fetch_now: %s не підтримує API відгуків [%s]", exchange, merchant_id[:12])
            try:
                await self._db.save_reviews(exchange, merchant_id, 0, 0, 0, [], status="NOT_SUPPORTED")
            except Exception:
                pass
            return {
                "positive": 0, "negative": 0, "neutral": 0, "bad_texts": [],
                "status": "NOT_SUPPORTED", "error_reason": f"{exchange}: reviews API not supported"
            }

        # ── Перевірка доступності ПЕРЕД запитом (per-exchange логіка) ──────
        _client_map = {"Binance": self._binance, "Bybit": self._bybit, "OKX": self._okx, "MEXC": self._mexc}
        client = _client_map.get(exchange)

        if exchange == "MEXC":
            # MEXC: публічне API — тільки перевіряємо що клієнт є
            if not client:
                return {
                    "positive": 0, "negative": 0, "neutral": 0, "bad_texts": [],
                    "status": "NO_AUTH", "error_reason": "MEXC client is not initialized"
                }

        elif exchange in ("Bybit", "Binance", "OKX"):
            # Bybit/Binance/OKX: всі три потребують браузерну сесію.
            # OKX використовує POST /v3/c2c/review/history (аналогічно Bybit/Binance).
            if not client:
                return {
                    "positive": 0, "negative": 0, "neutral": 0, "bad_texts": [],
                    "status": "NO_AUTH", "error_reason": f"{exchange} client is not initialized"
                }
            session_h, _, _ = await self._db.get_auth_session(exchange)
            if not session_h:
                logger.debug("fetch_now: %s [%s] — немає перехопленої сесії", exchange, merchant_id[:12])
                try:
                    await self._db.save_reviews(
                        exchange, merchant_id, 0, 0, 0, [],
                        status="NO_SESSION",
                        error_reason=f"{exchange} browser session not captured"
                    )
                except Exception:
                    pass
                return {
                    "positive": 0, "negative": 0, "neutral": 0, "bad_texts": [],
                    "status": "NO_SESSION", "error_reason": f"{exchange} browser session not captured"
                }

        else:
            # Невідома біржа з клієнтом — без перевірки
            pass

        try:
            if exchange == "Binance":
                pos, neg, neutral, bad_texts = await self._fetch_binance(merchant_id)
            elif exchange == "Bybit":
                pos, neg, neutral, bad_texts = await self._fetch_bybit(merchant_id)
            elif exchange == "OKX":
                pos, neg, neutral, bad_texts = await self._fetch_okx(merchant_id)
            elif exchange == "MEXC":
                pos, neg, neutral, bad_texts = await self._fetch_mexc(merchant_id)
            else:
                return {"positive": 0, "negative": 0, "neutral": 0, "bad_texts": [], "status": "UNKNOWN"}

            # Bybit/Binance/OKX: без сесії → NO_SESSION щоб needs_review_fetch
            # повернув True через 10 хв — як тільки сесія з'явиться, всі перефетчаться
            if exchange in ("Bybit", "Binance", "OKX"):
                session_h, _, _ = await self._db.get_auth_session(exchange)
                save_status = "OK" if session_h else "NO_SESSION"
            else:
                save_status = "OK"

            save_reason = ""
            if save_status == "OK" and (pos + neg + neutral) == 0 and not bad_texts:
                if exchange in ("Binance", "Bybit"):
                    pass  # For Binance/Bybit, 0 negative reviews is a normal successful result, NOT "no feedback"
                else:
                    save_status = "NO_FEEDBACK"
                    save_reason = f"{exchange} API returned 0 feedback entries"

            await self._db.save_reviews(
                exchange, merchant_id, pos, neg, neutral, bad_texts,
                status=save_status, error_reason=save_reason
            )
            self._known_merchants.add((exchange, merchant_id))

            return {
                "positive": pos,
                "negative": neg,
                "neutral": neutral,
                "bad_texts": bad_texts,
                "status": save_status,
                "error_reason": save_reason,
            }
        except Exception as e:
            logger.warning(f"fetch_now помилка для {merchant_id}: {e}")
            emsg = str(e)
            status = "UNAVAILABLE"
            if "AuthError" in emsg:
                status = "SESSION_EXPIRED"
            elif "API_ERROR" in emsg:
                status = "API_ERROR"
            try:
                await self._db.save_reviews(
                    exchange, merchant_id, 0, 0, 0, [],
                    status=status, error_reason=emsg[:500]
                )
            except Exception:
                pass
            return {
                "positive": 0, "negative": 0, "neutral": 0, "bad_texts": [],
                "status": status, "error_reason": emsg[:500]
            }

    # ─── Worker loop ────────────────────────────────────────────────────────

    async def _worker_loop(self) -> None:
        while True:
            try:
                # Пріоритет: спочатку urgent, потім normal
                try:
                    exchange, merchant_id = self._urgent_queue.get_nowait()
                    from_urgent = True
                except asyncio.QueueEmpty:
                    exchange, merchant_id = await self._queue.get()
                    from_urgent = False

                key = (exchange, merchant_id)
                try:
                    now = asyncio.get_event_loop().time()
                    if now < self._exchange_cooldown.get(exchange, 0):
                        logger.debug("ReviewFetcher Degraded Mode для %s, пропускаємо", exchange)
                        await self._db.save_reviews(
                            exchange, merchant_id, 0, 0, 0, [],
                            status="UNAVAILABLE",
                            error_reason=f"{exchange} degraded mode (temporary cooldown)"
                        )
                        continue

                    needs_fetch = await self._db.needs_review_fetch(
                        exchange, merchant_id, self._review_ttl
                    )
                    if not needs_fetch and not from_urgent:
                        logger.debug("ReviewFetcher TTL skip %s [%s]", merchant_id, exchange)
                        continue

                    logger.debug("ReviewFetcher fetch [%s] %s urgent=%s", exchange, merchant_id, from_urgent)
                    await self._fetch_and_save(exchange, merchant_id)
                    await asyncio.sleep(RATE_LIMITS.get(exchange, 2.0))

                finally:
                    self._pending.discard(key)
                    # task_done тільки для нормальної черги (urgent — get_nowait)
                    if not from_urgent:
                        self._queue.task_done()

            except asyncio.CancelledError:
                break
            except Exception as e:
                self._errors += 1
                logger.error("ReviewFetcher worker помилка: %s", e, exc_info=True)
                await asyncio.sleep(3.0)

    async def _fetch_and_save(self, exchange: str, merchant_id: str) -> None:
        try:
            # ── Перевірка доступності ПЕРЕД запитом (per-exchange логіка) ──
            if exchange == "MEXC":
                if not self._mexc:
                    logger.debug("_fetch_and_save: MEXC клієнт не підключений, skip %s", merchant_id[:12])
                    await self._db.save_reviews(
                        exchange, merchant_id, 0, 0, 0, [],
                        status="NO_AUTH", error_reason="MEXC client is not initialized"
                    )
                    return

            elif exchange in ("Bybit", "Binance", "OKX"):
                # Bybit/Binance/OKX: всі потребують браузерну сесію.
                # OKX використовує POST /v3/c2c/review/history з reviewScoreType="negative".
                _client_map = {"Bybit": self._bybit, "Binance": self._binance, "OKX": self._okx}
                client = _client_map.get(exchange)
                if not client:
                    logger.debug("_fetch_and_save: %s [%s] — клієнт відсутній", exchange, merchant_id[:12])
                    await self._db.save_reviews(
                        exchange, merchant_id, 0, 0, 0, [],
                        status="NO_AUTH", error_reason=f"{exchange} client is not initialized"
                    )
                    return
                session_h, _, _ = await self._db.get_auth_session(exchange)
                if not session_h:
                    logger.debug("_fetch_and_save: %s [%s] — немає перехопленої сесії", exchange, merchant_id[:12])
                    await self._db.save_reviews(
                        exchange, merchant_id, 0, 0, 0, [],
                        status="NO_SESSION", error_reason=f"{exchange} browser session not captured"
                    )
                    return

            else:
                # Невідома біржа — пропускаємо
                pass

            if exchange == "Binance":
                pos, neg, neutral, bad_texts = await self._fetch_binance(merchant_id)
            elif exchange == "Bybit":
                pos, neg, neutral, bad_texts = await self._fetch_bybit(merchant_id)
            elif exchange == "OKX":
                pos, neg, neutral, bad_texts = await self._fetch_okx(merchant_id)
            elif exchange == "MEXC":
                pos, neg, neutral, bad_texts = await self._fetch_mexc(merchant_id)
            else:
                return

            self._exchange_fails[exchange] = 0
            total = pos + neg + neutral
            bad_pct = (neg / total * 100.0) if total > 0 else 0.0

            self._known_merchants.add((exchange, merchant_id))

            # Bybit/Binance/OKX: NO_SESSION якщо сесія не захоплена — перефетч через 10 хв
            if exchange in ("Bybit", "Binance", "OKX"):
                session_h, _, _ = await self._db.get_auth_session(exchange)
                save_status = "OK" if session_h else "NO_SESSION"
            else:
                save_status = "OK"

            save_reason = ""
            if save_status == "OK" and total == 0 and not bad_texts:
                if exchange in ("Binance", "Bybit"):
                    pass  # For Binance/Bybit, 0 negative reviews is a normal successful result, NOT "no feedback"
                else:
                    save_status = "NO_FEEDBACK"
                    save_reason = f"{exchange} API returned 0 feedback entries"

            await self._db.save_reviews(
                exchange, merchant_id, pos, neg, neutral, bad_texts,
                status=save_status, error_reason=save_reason
            )
            self._processed += 1

            if bad_pct >= BAD_REVIEW_THRESHOLD_PCT and neg >= 3:
                logger.warning(
                    "🚨 Поганий мерчант %s [%s]: %.0f%% негативних (%d/%d)",
                    merchant_id, exchange, bad_pct, neg, total,
                )

        except Exception as e:
            self._errors += 1
            self._exchange_fails[exchange] = self._exchange_fails.get(exchange, 0) + 1
            if self._exchange_fails[exchange] >= 3:
                cooldown_sec = {"Binance": 300.0, "OKX": 600.0}.get(exchange, 7200.0)
                self._exchange_cooldown[exchange] = asyncio.get_event_loop().time() + cooldown_sec
                logger.error("🚨 %s API впало 3 рази! Degraded Mode на %.0f хв.", exchange, cooldown_sec / 60)

            emsg = str(e)
            err_status = "UNAVAILABLE"
            if "AuthError" in emsg:
                err_status = "SESSION_EXPIRED"
            elif "API_ERROR" in emsg:
                err_status = "API_ERROR"
            await self._db.save_reviews(
                exchange, merchant_id, 0, 0, 0, [],
                status=err_status, error_reason=emsg[:500]
            )

    def _send_burnout_alert(self, exchange: str):
        """Надсилає миттєве Telegram-сповіщення (і пише в лог) про згоряння сесії."""
        msg = f"❌ Ваша сесія <b>{exchange}</b> для парсингу відгуків згоріла.\n👉 Будь ласка, залогіньтесь знову (відскануйте QR-код)."
        logger.error(f"SESSION_BURNOUT:{exchange}: {msg}")
        
        # Відправляємо напряму через Telegram API, щоб не створювати циклічних імпортів з notifier
        try:
            from config import settings
            import aiohttp
            if settings.telegram_bot_token and settings.telegram_chat_id:
                async def _push():
                    url = f"https://api.telegram.org/bot{settings.telegram_bot_token}/sendMessage"
                    payload = {"chat_id": settings.telegram_chat_id, "text": msg, "parse_mode": "HTML"}
                    try:
                        async with aiohttp.ClientSession() as s:
                            await s.post(url, json=payload, timeout=5)
                    except Exception as e:
                        logger.debug("burnout_alert push failed: %s", e)
                asyncio.create_task(_push())
        except Exception:
            pass

    # ─── Exchange fetchers ───────────────────────────────────────────────────

    async def _fetch_binance(self, merchant_id: str) -> tuple[int, int, int, list[dict]]:
        """
        Binance: тексти негативних відгуків через перехоплену браузерну сесію.

        ПРИМІТКА: /bapi/c2c/v2/.../profile-and-ads і feedback-list повертають 404.
        Pos/neg COUNT розраховується в risk_engine через Order.positive_rate
        (доступний прямо в search response).

        Тексти відгуків (/v1/.../review/list-by-page) потребують повноцінну
        браузерну сесію (Csrftoken, BNC-Uuid, Device-Info, Fvideo).
        """
        client = self._binance
        if not client:
            return 0, 0, 0, []

        # Тексти відгуків — через перехоплену браузерну сесію
        headers, cookies, _ = await self._db.get_auth_session("Binance")
        if not headers:
            logger.debug("Binance [no session] %s: пропускаємо review texts", merchant_id)
            return 0, 0, 0, []

        try:
            raw_neg = await client.fetch_negative_reviews(
                merchant_id, rows=10,
                session_headers=headers,
                session_cookies=cookies,
            )
        except Exception as fe:
            if "AuthError" in str(fe):
                logger.error("🚨 Binance session burnout detected! %s", fe)
                asyncio.create_task(self._db.invalidate_auth_session("Binance", user_id=0))
                self._send_burnout_alert("Binance")
                raise RuntimeError(f"AuthError: Binance session expired: {fe}")
            else:
                logger.debug("Binance review texts error %s: %s", merchant_id, fe)
                raise RuntimeError(f"API_ERROR: Binance review API failed: {fe}")

        bad_texts: list[dict] = []
        for item in raw_neg:
            content = str(item.get("comments") or item.get("content") or item.get("message") or "").strip()
            if content:
                enriched = _enrich_bad_text(content)
                enriched["keyword_flagged"] = _has_bad_keywords(content)
                bad_texts.append(enriched)

        neg = len(raw_neg)

        logger.debug("Binance %s: session bad_texts=%d (keyword_flagged=%d)", merchant_id, len(bad_texts),
                      sum(1 for t in bad_texts if t.get("keyword_flagged")))
        return 0, neg, 0, bad_texts

    async def _fetch_bybit(self, merchant_id: str) -> tuple[int, int, int, list[dict]]:
        """
        Bybit: тексти відгуків через перехоплену браузерну сесію.

        ПРИМІТКА: api2.bybit.com/fiat/otc/user/public/profile повертає 404.
        Pos/neg COUNT більше не доступний через API → підрахунок відбувається
        у risk_engine._async_analyze_inner через fallback з Order.finish_rate_pct.

        Цей метод отримує ТІЛЬКИ тексти негативних відгуків (bad_texts).
        """
        client = self._bybit
        if not client:
            return 0, 0, 0, []

        # Тексти відгуків — через перехоплену браузерну сесію
        headers, cookies, _ = await self._db.get_auth_session("Bybit")
        if not headers:
            logger.debug("Bybit [no session] %s: пропускаємо feedback", merchant_id)
            return 0, 0, 0, []

        try:
            raw_neg = await client.fetch_merchant_feedback(
                merchant_id,
                session_headers=headers,
                session_cookies=cookies
            )
        except Exception as fe:
            if "AuthError" in str(fe):
                logger.error("🚨 Bybit session burnout detected! %s", fe)
                asyncio.create_task(self._db.invalidate_auth_session("Bybit", user_id=0))
                self._send_burnout_alert("Bybit")
                raise RuntimeError(f"AuthError: Bybit session expired: {fe}")
            else:
                logger.debug("Bybit feedback error %s: %s", merchant_id, fe)
                raise RuntimeError(f"API_ERROR: Bybit feedback API failed: {fe}")

        bad_texts: list[dict] = []
        for item in raw_neg:
            content = str(item.get("remark") or item.get("content") or "").strip()
            if content:
                enriched = _enrich_bad_text(content)
                enriched["keyword_flagged"] = _has_bad_keywords(content)
                bad_texts.append(enriched)

        # Якщо є погані тексти — беремо neg з них (pos/neg з профілю недоступний)
        neg = len(raw_neg) if raw_neg else 0

        logger.debug("Bybit %s: session bad_texts=%d (keyword_flagged=%d)", merchant_id, len(bad_texts),
                      sum(1 for t in bad_texts if t.get("keyword_flagged")))
        return 0, neg, 0, bad_texts


    async def _fetch_okx(self, merchant_id: str) -> tuple[int, int, int, list[dict]]:
        """
        OKX v3: браузерна сесія + POST /v3/c2c/review/history.

        Раніше використовувався /api/v5/c2c/order/user-feedback з API-ключами,
        але цей ендпоінт повертає 404 для P2P відгуків (доступний лише через браузер).

        Новий підхід (перехоплено через Playwright):
          - Endpoint: POST https://www.okx.com/v3/c2c/review/history
          - Payload: {currentPage, hasComment, pageSize, reviewFromBuyer,
                      reviewScoreType: "" | "negative" | "positive", pubUserId}
          - Auth: authorization JWT + cookies з браузерної сесії
        """
        import time
        from curl_cffi.requests import AsyncSession as CurlSession

        headers_dict, cookies_dict, _ = await self._db.get_auth_session("OKX")
        if not headers_dict:
            raise RuntimeError("AuthError: OKX browser session not captured")

        # Беремо лише потрібні заголовки з перехопленої сесії
        req_headers: dict[str, str] = {
            "accept": "application/json",
            "content-type": "application/json",
            "app-type": "web",
            "x-locale": "ru_RU",
        }
        for key in ("authorization", "devid", "x-id-group", "x-site-info",
                    "user-agent", "x-client-signature", "x-client-signature-version"):
            val = headers_dict.get(key)
            if val:
                req_headers[key] = val

        if "authorization" not in req_headers:
            raise RuntimeError("AuthError: OKX authorization header missing in session")

        try:
            async with CurlSession(impersonate="chrome124") as session:
                # 1) Загальна статистика: від покупців та від продавців
                ts = int(time.time() * 1000)
                url_all = f"https://www.okx.com/v3/c2c/review/history?t={ts}"
                
                payload_buyer = {
                    "currentPage": 1,
                    "hasComment": False,
                    "pageSize": 1,
                    "reviewFromBuyer": True,
                    "reviewScoreType": "",
                    "pubUserId": merchant_id,
                }
                payload_seller = {
                    "currentPage": 1,
                    "hasComment": False,
                    "pageSize": 1,
                    "reviewFromBuyer": False,
                    "reviewScoreType": "",
                    "pubUserId": merchant_id,
                }
                
                resp_buyer, resp_seller = await asyncio.gather(
                    session.post(url_all, json=payload_buyer, headers=req_headers, cookies=cookies_dict, timeout=10),
                    session.post(url_all, json=payload_seller, headers=req_headers, cookies=cookies_dict, timeout=10),
                    return_exceptions=True
                )

                if isinstance(resp_buyer, Exception):
                    raise resp_buyer
                if isinstance(resp_seller, Exception):
                    raise resp_seller

                if resp_buyer.status_code == 401 or resp_seller.status_code == 401:
                    raise RuntimeError("AuthError: OKX session expired (HTTP 401)")

                pos_buyer, neg_buyer = 0, 0
                pos_seller, neg_seller = 0, 0

                if resp_buyer.status_code == 200:
                    data_buyer = resp_buyer.json()
                    if data_buyer.get("code") == 0:
                        item_stats = data_buyer.get("data", {}).get("item", {})
                        pos_buyer = int(item_stats.get("positiveCount") or 0)
                        neg_buyer = int(item_stats.get("negativeCount") or 0)
                    else:
                        raise RuntimeError(f"API_ERROR: OKX buyer code={data_buyer.get('code')}, msg={data_buyer.get('msg', '')}")
                else:
                    raise RuntimeError(f"API_ERROR: OKX buyer history returned {resp_buyer.status_code}")

                if resp_seller.status_code == 200:
                    data_seller = resp_seller.json()
                    if data_seller.get("code") == 0:
                        item_stats = data_seller.get("data", {}).get("item", {})
                        pos_seller = int(item_stats.get("positiveCount") or 0)
                        neg_seller = int(item_stats.get("negativeCount") or 0)
                    else:
                        raise RuntimeError(f"API_ERROR: OKX seller code={data_seller.get('code')}, msg={data_seller.get('msg', '')}")
                else:
                    raise RuntimeError(f"API_ERROR: OKX seller history returned {resp_seller.status_code}")

                pos = pos_buyer + pos_seller
                neg = neg_buyer + neg_seller

                # 2) Тексти негативних відгуків (reviewScoreType="negative")
                bad_texts: list[dict] = []
                
                # Завантаження негативних відгуків покупців
                if neg_buyer > 0:
                    neg_buyer_items = await self._fetch_okx_review_pages(
                        session, merchant_id, req_headers, cookies_dict,
                        score_type="negative", from_buyer=True, max_pages=3
                    )
                    for rev in neg_buyer_items:
                        comment_str = str(rev.get("comment") or "").strip()
                        reply_dict = rev.get("reviewReply") or {}
                        reply_str = str(reply_dict.get("comment") or "").strip() if isinstance(reply_dict, dict) else ""
                        
                        parts = []
                        if comment_str:
                            parts.append(comment_str)
                        else:
                            parts.append("Покупець не залишив коментаря")
                            
                        if reply_str:
                            parts.append(f"Відповідь мейкера: {reply_str}")
                            
                        content = " | ".join(parts)
                        if comment_str or reply_str:
                            enriched = _enrich_bad_text(content)
                            enriched["keyword_flagged"] = _has_bad_keywords(content)
                            bad_texts.append(enriched)

                # Завантаження негативних відгуків продавців
                if neg_seller > 0:
                    neg_seller_items = await self._fetch_okx_review_pages(
                        session, merchant_id, req_headers, cookies_dict,
                        score_type="negative", from_buyer=False, max_pages=3
                    )
                    for rev in neg_seller_items:
                        comment_str = str(rev.get("comment") or "").strip()
                        reply_dict = rev.get("reviewReply") or {}
                        reply_str = str(reply_dict.get("comment") or "").strip() if isinstance(reply_dict, dict) else ""
                        
                        parts = []
                        if comment_str:
                            parts.append(comment_str)
                        else:
                            parts.append("Продавець не залишив коментаря")
                            
                        if reply_str:
                            parts.append(f"Відповідь мейкера: {reply_str}")
                            
                        content = " | ".join(parts)
                        if comment_str or reply_str:
                            enriched = _enrich_bad_text(content)
                            enriched["keyword_flagged"] = _has_bad_keywords(content)
                            bad_texts.append(enriched)

                logger.debug(
                    "OKX [session] %s: pos=%d neg=%d | bad_texts=%d (keyword_flagged=%d)",
                    merchant_id, pos, neg, len(bad_texts),
                    sum(1 for t in bad_texts if t.get("keyword_flagged"))
                )
                return pos, neg, 0, bad_texts

        except RuntimeError:
            raise
        except Exception as e:
            logger.debug("OKX fetch error %s: %s", merchant_id, e)
            raise RuntimeError(f"API_ERROR: OKX review history failed: {e}")

    async def _fetch_okx_review_pages(
        self,
        session,
        merchant_id: str,
        req_headers: dict,
        cookies_dict: dict,
        score_type: str,
        from_buyer: bool,
        max_pages: int = 3,
        page_size: int = 10,
    ) -> list[dict]:
        """
        Завантажує відгуки OKX з пагінацією через POST /v3/c2c/review/history.
        score_type: "" (всі) | "positive" | "negative"
        """
        import time
        all_items: list[dict] = []
        for page in range(1, max_pages + 1):
            ts = int(time.time() * 1000)
            url = f"https://www.okx.com/v3/c2c/review/history?t={ts}"
            payload = {
                "currentPage": page,
                "hasComment": False,
                "pageSize": page_size,
                "reviewFromBuyer": from_buyer,
                "reviewScoreType": score_type,
                "pubUserId": merchant_id,
            }
            try:
                resp = await session.post(
                    url, json=payload,
                    headers=req_headers, cookies=cookies_dict, timeout=10
                )
                if resp.status_code == 401:
                    raise RuntimeError("AuthError: OKX session expired (HTTP 401)")
                if resp.status_code != 200:
                    logger.debug("OKX review pages %s page=%d: status %d", merchant_id, page, resp.status_code)
                    break

                data = resp.json()
                if data.get("code") != 0:
                    logger.debug("OKX review pages %s: code=%s", merchant_id, data.get("code"))
                    break

                items = data.get("data", {}).get("item", {}).get("reviewHistoryDetail", []) or []
                all_items.extend(items)

                if len(items) < page_size:
                    break  # Остання сторінка

            except RuntimeError:
                raise
            except Exception as e:
                logger.debug("OKX review pages partial %s page=%d: %s", merchant_id, page, e)
                break

        return all_items

    async def _fetch_mexc(self, merchant_id: str) -> tuple[int, int, int, list[dict]]:
        client = self._mexc
        if not client:
            return 0, 0, 0, []

        try:
            # Один запит → і статистика, і тексти
            data = await client.fetch_merchant_reviews(merchant_id)
            pos = data.get("good", 0)
            neg = data.get("bad", 0)

            bad_texts: list[dict] = []
            for item in data.get("reviews", []):
                # MEXC поле: "comment"; rating=false → негативний відгук
                is_bad = item.get("rating") is False
                content = str(item.get("comment") or "").strip()
                if is_bad and content:
                    enriched = _enrich_bad_text(content)
                    enriched["keyword_flagged"] = _has_bad_keywords(content)
                    bad_texts.append(enriched)

            logger.debug("MEXC %s: pos=%d neg=%d bad_texts=%d (keyword_flagged=%d)", merchant_id, pos, neg,
                         len(bad_texts), sum(1 for t in bad_texts if t.get("keyword_flagged")))
            return pos, neg, 0, bad_texts

        except Exception as e:
            logger.debug("MEXC fetch error %s: %s", merchant_id, e)
            return 0, 0, 0, []
