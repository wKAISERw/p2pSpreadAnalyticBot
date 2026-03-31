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
        "text": text[:200],
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
            "ReviewFetcher clients bound: Binance=%s(session) Bybit=%s(session) OKX=%s(api) MEXC=%s(public)",
            "✅" if self._binance else "❌",
            "✅" if self._bybit else "❌",
            "✅" if self._okx and getattr(self._okx, "is_authenticated", False) else "❌",
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
            "ReviewFetcher запущено | ttl=%.1fh | urgent_q=%d | normal_q=%d | auth: B=%s By=%s OKX=%s",
            self._review_ttl,
            self._urgent_queue.maxsize,
            self._queue.maxsize,
            "✅" if self._binance and getattr(self._binance, "is_authenticated", False) else "❌",
            "✅" if self._bybit and getattr(self._bybit, "is_authenticated", False) else "❌",
            "✅" if self._okx and getattr(self._okx, "is_authenticated", False) else "❌",
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
            return {"positive": 0, "negative": 0, "neutral": 0, "bad_texts": [], "status": "UNKNOWN"}

        if exchange not in RATE_LIMITS:
            # Біржа не підтримує API відгуків (Wallet, CryptoBot)
            logger.debug("fetch_now: %s не підтримує API відгуків [%s]", exchange, merchant_id[:12])
            try:
                await self._db.save_reviews(exchange, merchant_id, 0, 0, 0, [], status="NOT_SUPPORTED")
            except Exception:
                pass
            return {"positive": 0, "negative": 0, "neutral": 0, "bad_texts": [], "status": "NOT_SUPPORTED"}

        # ── Перевірка доступності ПЕРЕД запитом (per-exchange логіка) ──────
        _client_map = {"Binance": self._binance, "Bybit": self._bybit, "OKX": self._okx, "MEXC": self._mexc}
        client = _client_map.get(exchange)

        if exchange == "MEXC":
            # MEXC: публічне API — тільки перевіряємо що клієнт є
            if not client:
                return {"positive": 0, "negative": 0, "neutral": 0, "bad_texts": [], "status": "NO_AUTH"}

        elif exchange in ("Bybit", "Binance"):
            # Bybit/Binance: профіль API мертвий (404).
            # Тексти відгуків → тільки через перехоплену браузерну сесію.
            # Клієнт потрібен для HTTP запитів, але API-ключі не обов'язкові.
            if not client:
                return {"positive": 0, "negative": 0, "neutral": 0, "bad_texts": [], "status": "NO_AUTH"}
            session_h, _, _ = await self._db.get_auth_session(exchange)
            if not session_h:
                logger.debug("fetch_now: %s [%s] — немає перехопленої сесії", exchange, merchant_id[:12])
                # Зберігаємо NO_SESSION щоб needs_review_fetch перевіряв кожну годину
                try:
                    await self._db.save_reviews(exchange, merchant_id, 0, 0, 0, [], status="NO_SESSION")
                except Exception:
                    pass
                return {"positive": 0, "negative": 0, "neutral": 0, "bad_texts": [], "status": "NO_SESSION"}

        else:
            # OKX: класична API-автентифікація
            if not client or not getattr(client, "is_authenticated", False):
                logger.debug("fetch_now: %s [%s] — клієнт не автентифікований, skip", exchange, merchant_id[:12])
                return {"positive": 0, "negative": 0, "neutral": 0, "bad_texts": [], "status": "NO_AUTH"}

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

            # Bybit/Binance: без сесії → NO_SESSION (не OK!) щоб needs_review_fetch
            # повернув True через 1h — як тільки сесія з'явиться, всі перефетчаться
            if exchange in ("Bybit", "Binance"):
                session_h, _, _ = await self._db.get_auth_session(exchange)
                save_status = "OK" if session_h else "NO_SESSION"
            else:
                save_status = "OK"

            await self._db.save_reviews(exchange, merchant_id, pos, neg, neutral, bad_texts, status=save_status)
            self._known_merchants.add((exchange, merchant_id))

            return {
                "positive": pos,
                "negative": neg,
                "neutral": neutral,
                "bad_texts": bad_texts,
                "status": save_status,
            }
        except Exception as e:
            logger.warning(f"fetch_now помилка для {merchant_id}: {e}")
            return {"positive": 0, "negative": 0, "neutral": 0, "bad_texts": [], "status": "UNAVAILABLE"}

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
                        await self._db.save_reviews(exchange, merchant_id, 0, 0, 0, [], status="UNAVAILABLE")
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
                    return

            elif exchange in ("Bybit", "Binance"):
                # Bybit/Binance: профіль API мертвий (404).
                # Тексти відгуків → тільки через браузерну сесію.
                _client_map = {"Bybit": self._bybit, "Binance": self._binance}
                client = _client_map.get(exchange)
                if not client:
                    logger.debug("_fetch_and_save: %s [%s] — клієнт відсутній", exchange, merchant_id[:12])
                    return
                session_h, _, _ = await self._db.get_auth_session(exchange)
                if not session_h:
                    logger.debug("_fetch_and_save: %s [%s] — немає перехопленої сесії", exchange, merchant_id[:12])
                    await self._db.save_reviews(exchange, merchant_id, 0, 0, 0, [], status="NO_SESSION")
                    return

            else:
                # OKX: класична API-автентифікація
                _client_map = {"OKX": self._okx}
                client = _client_map.get(exchange)
                if not client or not getattr(client, "is_authenticated", False):
                    logger.debug("_fetch_and_save: %s [%s] — no auth, skip", exchange, merchant_id[:12])
                    await self._db.save_reviews(exchange, merchant_id, 0, 0, 0, [], status="NO_AUTH")
                    return

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

            # Bybit/Binance: NO_SESSION якщо сесія не захоплена — перефетч через 1h
            if exchange in ("Bybit", "Binance"):
                session_h, _, _ = await self._db.get_auth_session(exchange)
                save_status = "OK" if session_h else "NO_SESSION"
            else:
                save_status = "OK"

            await self._db.save_reviews(
                exchange, merchant_id, pos, neg, neutral, bad_texts, status=save_status
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

            await self._db.save_reviews(exchange, merchant_id, 0, 0, 0, [], status="UNAVAILABLE")

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
            else:
                logger.debug("Binance review texts error %s: %s", merchant_id, fe)
            raw_neg = []

        bad_texts: list[dict] = []
        for item in raw_neg:
            content = str(item.get("content") or item.get("message") or "").strip()
            if content and _has_bad_keywords(content):
                bad_texts.append(_enrich_bad_text(content))

        neg = len(bad_texts) if bad_texts else 0

        logger.debug("Binance %s: session bad_texts=%d", merchant_id, len(bad_texts))
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
            else:
                logger.debug("Bybit feedback error %s: %s", merchant_id, fe)
            raw_neg = []

        bad_texts: list[dict] = []
        for item in raw_neg:
            content = str(item.get("remark") or item.get("content") or "").strip()
            if content and _has_bad_keywords(content):
                bad_texts.append(_enrich_bad_text(content))

        # Якщо є погані тексти — беремо neg з них (pos/neg з профілю недоступний)
        neg = len(bad_texts) if bad_texts else 0

        logger.debug("Bybit %s: session bad_texts=%d", merchant_id, len(bad_texts))
        return 0, neg, 0, bad_texts


    async def _fetch_okx(self, merchant_id: str) -> tuple[int, int, int, list[dict]]:
        """
        OKX: два окремих виклики — type=1 (positive) і type=2 (negative).
        v2.1: додана пагінація щоб обійти ліміт в 20 відгуків за запит.
        Без пагінації neg_pct рахувався відносно max(20+20=40) відгуків,
        що давало хибно завищений % для мерчантів з сотнями угод.
        """
        client = self._okx
        if not client or not getattr(client, "is_authenticated", False):
            return 0, 0, 0, []

        try:
            # type=1 → позитивні, type=2 → негативні; пагінація до 3 сторінок
            pos_raw = await self._fetch_okx_paginated(client, merchant_id, feedback_type=1, max_pages=3)
            neg_raw = await self._fetch_okx_paginated(client, merchant_id, feedback_type=2, max_pages=3)

            pos = len(pos_raw)
            neg = len(neg_raw)
            neutral = 0

            bad_texts: list[dict] = []
            for item in neg_raw:
                content = str(item.get("content") or item.get("feedback") or "").strip()
                if content and _has_bad_keywords(content):
                    bad_texts.append(_enrich_bad_text(content))

            logger.debug("OKX [auth] %s: pos=%d neg=%d (paginated) | bad_texts=%d", merchant_id, pos, neg,
                         len(bad_texts))
            return pos, neg, neutral, bad_texts

        except Exception as e:
            logger.debug("OKX fetch error %s: %s", merchant_id, e)
            return 0, 0, 0, []

    async def _fetch_okx_paginated(self, client, merchant_id: str, feedback_type: int, max_pages: int = 3) -> list[
        dict]:
        """Завантажує OKX відгуки з пагінацією (cursor-based)."""
        all_items: list[dict] = []
        cursor = ""
        for _ in range(max_pages):
            try:
                cursor_param = f"&cursor={cursor}" if cursor else ""
                path = f"/api/v5/c2c/order/user-feedback?userId={merchant_id}&type={feedback_type}&limit=20{cursor_param}"
                url = f"https://www.okx.com{path}"
                headers = client._sign_headers("GET", path)
                data = await client._get(url, headers=headers)
                items = data.get("data", []) or []
                all_items.extend(items)
                # OKX повертає nextCursor якщо є ще сторінки
                next_cursor = data.get("nextCursor", "")
                if not next_cursor or len(items) < 20:
                    break
                cursor = next_cursor
            except Exception:
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
                    bad_texts.append(_enrich_bad_text(content))
                elif content and _has_bad_keywords(content):
                    bad_texts.append(_enrich_bad_text(content))

            logger.debug("MEXC %s: pos=%d neg=%d bad_texts=%d", merchant_id, pos, neg, len(bad_texts))
            return pos, neg, 0, bad_texts

        except Exception as e:
            logger.debug("MEXC fetch error %s: %s", merchant_id, e)
            return 0, 0, 0, []