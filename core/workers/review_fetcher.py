# core/review_fetcher.py

from __future__ import annotations

import asyncio
import logging
from contextlib import suppress
from typing import Optional, TYPE_CHECKING

from core.storage.merchant_db import MerchantDB
from core.analysis.rules import ALL_RULES

if TYPE_CHECKING:
    from infrastructure.api.binance_account import BinanceAccountClient
    from infrastructure.api.bybit_account import BybitAccountClient
    from infrastructure.api.okx_account import OkxAccountClient

logger = logging.getLogger("ReviewFetcher")

RATE_LIMITS = {
    "Binance": 2.0,
    "Bybit": 1.5,
    "OKX": 1.5,
}

BAD_REVIEW_THRESHOLD_PCT = 15.0

# Базові слова для швидкого фільтру (без regex)
_BASIC_BAD = [
    "scam", "шахрай", "кидало", "кинув", "розвів", "fraud", "fake", "не платить",
]

# Категорії з rules.py що свідчать про схеми
_TARGET_CATEGORIES = {"TRIANGLE", "CASINO", "FINCRIME", "CHARGEBACK", "APPEAL_PRESSURE"}



def _has_bad_keywords(text: str) -> bool:
    """
    Фільтрує відгуки по двох рівнях:
    1. Швидкий фільтр по базових словах
    2. Regex по цільових категоріях з rules.py
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



class ReviewFetcher:
    def __init__(
        self,
        db: MerchantDB,
        max_queue: int = 500,
        review_ttl_hours: float = 24.0,
        # HTTP клієнти передаються з scanner.py (вже мають session)
        binance_client=None,
        bybit_client=None,
        okx_client=None,
    ):
        self._db = db
        self._queue: asyncio.Queue[tuple[str, str]] = asyncio.Queue(maxsize=max_queue)
        self._review_ttl = review_ttl_hours
        self._worker_task: Optional[asyncio.Task] = None
        self._processed = 0
        self._errors = 0
        self._pending: set[tuple[str, str]] = set()

        # Account клієнти (infrastructure/api/) — підключаються з scanner.py
        # Якщо є API ключ → повні профілі мерчантів + реальні відгуки
        # Якщо немає → анонімний fallback (може давати 404)
        self._binance: Optional["BinanceAccountClient"] = binance_client
        self._bybit:   Optional["BybitAccountClient"]   = bybit_client
        self._okx:     Optional["OkxAccountClient"]     = okx_client

        self._exchange_fails: dict[str, int] = {"Binance": 0, "Bybit": 0, "OKX": 0}
        self._exchange_cooldown: dict[str, float] = {"Binance": 0.0, "Bybit": 0.0, "OKX": 0.0}

    def bind_clients(self, binance=None, bybit=None, okx=None) -> None:
        """
        Прив'язує HTTP клієнти після ініціалізації.
        Викликається з scanner.py після старту клієнтів.
        В майбутньому — викликається з /connect команди бота.
        """
        if binance is not None:
            self._binance = binance
        if bybit is not None:
            self._bybit = bybit
        if okx is not None:
            self._okx = okx
        logger.info(
            "ReviewFetcher clients bound: Binance=%s Bybit=%s OKX=%s",
            "✅" if self._binance and self._binance.is_authenticated else "❌",
            "✅" if self._bybit  and self._bybit.is_authenticated  else "❌",
            "✅" if self._okx   and self._okx.is_authenticated    else "❌",
        )
    async def start(self) -> None:
        # Сесія більше не потрібна — використовуємо клієнти від scanner.py
        if self._worker_task and not self._worker_task.done():
            logger.debug("ReviewFetcher start skipped: already running")
            return
        self._worker_task = asyncio.create_task(
            self._worker_loop(), name="review-fetcher"
        )
        logger.info(
            "ReviewFetcher запущено | ttl=%.1fh | max_queue=%d | auth: Binance=%s Bybit=%s OKX=%s",
            self._review_ttl, self._queue.maxsize,
            "✅" if self._binance and getattr(self._binance, "is_authenticated", False) else "❌",
            "✅" if self._bybit  and getattr(self._bybit,   "is_authenticated", False) else "❌",
            "✅" if self._okx   and getattr(self._okx,    "is_authenticated", False) else "❌",
        )

    async def stop(self) -> None:
        if self._worker_task:
            self._worker_task.cancel()
            with suppress(asyncio.CancelledError):
                await self._worker_task
            self._worker_task = None
        logger.info(
            "ReviewFetcher зупинено. Оброблено: %d, помилок: %d, pending: %d, queue: %d",
            self._processed, self._errors, len(self._pending), self._queue.qsize()
        )

    def schedule(self, exchange: str, merchant_id: str) -> bool:
        if exchange not in RATE_LIMITS:
            logger.debug("ReviewFetcher skip unsupported exchange %s [%s]", merchant_id, exchange)
            return False

        if not merchant_id:
            logger.debug("ReviewFetcher skip empty merchant_id [%s]", exchange)
            return False

        key = (exchange, merchant_id)
        if key in self._pending:
            logger.debug("ReviewFetcher skip duplicate in pending %s [%s]", merchant_id, exchange)
            return False

        try:
            self._queue.put_nowait(key)
            self._pending.add(key)
            logger.debug(
                "ReviewFetcher queued %s [%s] | queue=%d pending=%d",
                merchant_id, exchange, self._queue.qsize(), len(self._pending)
            )
            return True
        except asyncio.QueueFull:
            logger.warning(
                "ReviewFetcher queue full, skip %s [%s] | pending=%d",
                merchant_id, exchange, len(self._pending)
            )
            return False

    async def _worker_loop(self) -> None:
        while True:
            try:
                exchange, merchant_id = await self._queue.get()
                key = (exchange, merchant_id)

                try:
                    # 🚀 ПЕРЕВІРКА НА DEGRADED MODE (Якщо кулдаун ще діє - пропускаємо)
                    now = asyncio.get_event_loop().time()
                    if now < self._exchange_cooldown.get(exchange, 0):
                        logger.debug("ReviewFetcher в Degraded Mode для %s, пропускаємо", exchange)
                        await self._db.save_reviews(exchange, merchant_id, 0, 0, 0, [], status="UNAVAILABLE")
                        continue

                    needs_fetch = await self._db.needs_review_fetch(
                        exchange, merchant_id, self._review_ttl
                    )
                    if not needs_fetch:
                        logger.debug(
                            "ReviewFetcher TTL skip %s [%s]",
                            merchant_id, exchange
                        )
                        continue

                    logger.info("ReviewFetcher fetch start %s [%s]", merchant_id, exchange)
                    await self._fetch_and_save(exchange, merchant_id)
                    await asyncio.sleep(RATE_LIMITS.get(exchange, 2.0))

                finally:
                    self._pending.discard(key)
                    self._queue.task_done()

            except asyncio.CancelledError:
                break
            except Exception as e:
                self._errors += 1
                logger.error("ReviewFetcher worker помилка: %s", e, exc_info=True)
                await asyncio.sleep(3.0)

    async def _fetch_and_save(self, exchange: str, merchant_id: str) -> None:
        try:
            if exchange == "Binance":
                pos, neg, neutral, bad_texts = await self._fetch_binance(merchant_id)
            elif exchange == "Bybit":
                pos, neg, neutral, bad_texts = await self._fetch_bybit(merchant_id)
            elif exchange == "OKX":
                pos, neg, neutral, bad_texts = await self._fetch_okx(merchant_id)
            else:
                logger.debug("ReviewFetcher unexpected exchange skip %s [%s]", merchant_id, exchange)
                return

            total = pos + neg + neutral
            # 🚀 ЯКЩО УСПІШНО — СКИДАЄМО ЛІЧИЛЬНИК
            self._exchange_fails[exchange] = 0
            bad_pct = (neg / total * 100.0) if total > 0 else 0.0

            logger.info(
                "ReviewFetcher fetched %s [%s] | pos=%d neg=%d neu=%d total=%d bad_pct=%.1f bad_texts=%d",
                merchant_id, exchange, pos, neg, neutral, total, bad_pct, len(bad_texts)
            )

            await self._db.save_reviews(
                exchange, merchant_id, pos, neg, neutral, bad_texts, status="OK"
            )
            self._processed += 1

            logger.info(
                "ReviewFetcher saved %s [%s] | processed=%d",
                merchant_id, exchange, self._processed
            )

            if bad_pct >= BAD_REVIEW_THRESHOLD_PCT and neg >= 3:
                logger.warning(
                    "🚨 Поганий мерчант %s [%s]: %.0f%% негативних (%d/%d)",
                    merchant_id, exchange, bad_pct, neg, total
                )

        except Exception as e:
            self._errors += 1
            # 🚀 НАКОПИЧУЄМО ПОМИЛКИ І ВМИКАЄМО DEGRADED MODE
            self._exchange_fails[exchange] = self._exchange_fails.get(exchange, 0) + 1
            if self._exchange_fails[exchange] >= 3:
                # Таймаут залежить від типу біржі:
                # Bybit/OKX можуть впасти на хвилини, Binance — рідко
                cooldown_sec = {"Binance": 300.0, "OKX": 600.0}.get(exchange, 7200.0)
                self._exchange_cooldown[exchange] = asyncio.get_event_loop().time() + cooldown_sec
                logger.error(
                    "🚨 %s API впало 3 рази підряд! Degraded Mode на %.0f хв.",
                    exchange, cooldown_sec / 60,
                )

            await self._db.save_reviews(exchange, merchant_id, 0, 0, 0, [], status="UNAVAILABLE")
            logger.warning("Помилка відгуків %s [%s]: %s", merchant_id, exchange, e)


    async def _fetch_binance(self, merchant_id: str) -> tuple[int, int, int, list[str]]:
        """Binance: тільки через API ключ. Без ключа — пропускаємо."""
        client = self._binance
        if not client or not getattr(client, "is_authenticated", False):
            return 0, 0, 0, []
        try:
            raw = await client.fetch_negative_reviews(merchant_id, rows=10)
            bad_texts = [
                str(item.get("message") or item.get("content") or "")[:200]
                for item in raw
                if _has_bad_keywords(str(item.get("message") or item.get("content") or ""))
            ]
            logger.debug("Binance [auth] %s: bad_texts=%d", merchant_id, len(bad_texts))
            return 0, 0, 0, bad_texts
        except Exception as e:
            logger.debug("Binance fetch error %s: %s", merchant_id, e)
            return 0, 0, 0, []

    async def _fetch_bybit(self, merchant_id: str) -> tuple[int, int, int, list[str]]:
        """Bybit: тільки через API ключ."""
        client = self._bybit
        if not client or not getattr(client, "is_authenticated", False):
            return 0, 0, 0, []
        try:
            raw = await client.fetch_merchant_feedback(merchant_id)
            bad_texts = [
                str(item.get("content") or "")[:200]
                for item in raw
                if _has_bad_keywords(str(item.get("content") or ""))
            ]
            logger.debug("Bybit [auth] %s: bad_texts=%d", merchant_id, len(bad_texts))
            return 0, 0, 0, bad_texts
        except Exception as e:
            logger.debug("Bybit fetch error %s: %s", merchant_id, e)
            return 0, 0, 0, []

    async def _fetch_okx(self, merchant_id: str) -> tuple[int, int, int, list[str]]:
        """OKX: тільки через API ключ + passphrase."""
        client = self._okx
        if not client or not getattr(client, "is_authenticated", False):
            return 0, 0, 0, []
        try:
            raw = await client.fetch_merchant_feedback(merchant_id)
            bad_texts = [
                str(item.get("content") or item.get("feedback") or "")[:200]
                for item in raw
                if _has_bad_keywords(str(item.get("content") or item.get("feedback") or ""))
            ]
            logger.debug("OKX [auth] %s: bad_texts=%d", merchant_id, len(bad_texts))
            return 0, 0, 0, bad_texts
        except Exception as e:
            logger.debug("OKX fetch error %s: %s", merchant_id, e)
            return 0, 0, 0, []