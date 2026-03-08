# core/review_fetcher.py

from __future__ import annotations

import asyncio
import logging
from contextlib import suppress
from typing import Optional

import aiohttp

from core.merchant_db import MerchantDB

logger = logging.getLogger("ReviewFetcher")

RATE_LIMITS = {
    "Binance": 2.0,
    "Bybit": 1.5,
    "OKX": 1.5,
}

BAD_REVIEW_THRESHOLD_PCT = 15.0

BAD_KEYWORDS = [
    "scam", "шахрай", "шахрайство", "обман", "не платить", "не платив",
    "кинув", "freeze", "blocked", "заморозив", "обманув", "fraud",
    "fake", "фейк", "розводить", "розвів",
]


class ReviewFetcher:
    def __init__(
        self,
        db: MerchantDB,
        max_queue: int = 500,
        review_ttl_hours: float = 24.0,
    ):
        self._db = db
        self._queue: asyncio.Queue[tuple[str, str]] = asyncio.Queue(maxsize=max_queue)
        self._review_ttl = review_ttl_hours
        self._worker_task: Optional[asyncio.Task] = None
        self._session: Optional[aiohttp.ClientSession] = None
        self._processed = 0
        self._errors = 0
        self._pending: set[tuple[str, str]] = set()

    async def start(self) -> None:
        if self._session and not self._session.closed:
            logger.debug("ReviewFetcher start skipped: session already active")
            return

        self._session = aiohttp.ClientSession(
            headers={
                "User-Agent": (
                    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) "
                    "Chrome/124.0.0.0 Safari/537.36"
                ),
                "Accept-Language": "uk-UA,uk;q=0.9,en-US;q=0.8",
            },
            timeout=aiohttp.ClientTimeout(total=10.0),
        )
        self._worker_task = asyncio.create_task(
            self._worker_loop(), name="review-fetcher"
        )
        logger.info("ReviewFetcher запущено | ttl=%.1fh | max_queue=%d", self._review_ttl, self._queue.maxsize)

    async def stop(self) -> None:
        if self._worker_task:
            self._worker_task.cancel()
            with suppress(asyncio.CancelledError):
                await self._worker_task
            self._worker_task = None

        if self._session:
            await self._session.close()
            self._session = None

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
                    logger.debug(
                        "ReviewFetcher dequeued %s [%s] | queue=%d pending=%d",
                        merchant_id, exchange, self._queue.qsize(), len(self._pending)
                    )

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
            bad_pct = (neg / total * 100.0) if total > 0 else 0.0

            logger.info(
                "ReviewFetcher fetched %s [%s] | pos=%d neg=%d neu=%d total=%d bad_pct=%.1f bad_texts=%d",
                merchant_id, exchange, pos, neg, neutral, total, bad_pct, len(bad_texts)
            )

            await self._db.save_reviews(
                exchange, merchant_id, pos, neg, neutral, bad_texts
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
            logger.exception(
                "Помилка фетчингу/збереження відгуків %s [%s]: %s",
                merchant_id, exchange, e
            )

    async def _fetch_binance(self, merchant_id: str) -> tuple[int, int, int, list[str]]:
        url = "https://p2p.binance.com/bapi/c2c/v2/friendly/c2c/user/profile-and-ads"
        payload = {
            "advertiserNo": merchant_id,
            "page": 1,
            "rows": 1,
        }

        async with self._session.post(
            url,
            json=payload,
            headers={
                "content-type": "application/json",
                "origin": "https://p2p.binance.com",
            },
        ) as resp:
            if resp.status == 429:
                logger.warning("ReviewFetcher Binance 429 for %s", merchant_id)
                await asyncio.sleep(10.0)
                raise RuntimeError("Binance 429")
            if resp.status != 200:
                logger.warning("ReviewFetcher Binance profile status=%s for %s", resp.status, merchant_id)
                return 0, 0, 0, []
            data = await resp.json()

        user = data.get("data", {}).get("advertiser", {})
        if not user:
            logger.debug("ReviewFetcher Binance empty profile %s", merchant_id)
            return 0, 0, 0, []

        total = int(user.get("monthOrderCount") or 0)
        pos_rate = float(user.get("positiveRate") or 0)
        neg_rate = float(user.get("negativeRate") or 0)

        pos = int(total * pos_rate)
        neg = int(total * neg_rate)
        neutral = total - pos - neg

        bad_texts = await self._fetch_binance_bad_texts(merchant_id)
        return max(pos, 0), max(neg, 0), max(neutral, 0), bad_texts

    async def _fetch_binance_bad_texts(self, merchant_id: str) -> list[str]:
        url = "https://p2p.binance.com/bapi/c2c/v2/friendly/c2c/user/feedback-list"
        payload = {
            "advertiserNo": merchant_id,
            "type": 2,
            "page": 1,
            "rows": 10,
        }

        try:
            async with self._session.post(
                url,
                json=payload,
                headers={
                    "content-type": "application/json",
                    "origin": "https://p2p.binance.com",
                },
            ) as resp:
                if resp.status != 200:
                    logger.debug("ReviewFetcher Binance bad_texts status=%s for %s", resp.status, merchant_id)
                    return []
                data = await resp.json()
        except Exception as e:
            logger.debug("ReviewFetcher Binance bad_texts error %s: %s", merchant_id, e)
            return []

        texts = []
        for item in data.get("data", []):
            text = (item.get("message") or "").strip()
            if text and _has_bad_keywords(text):
                texts.append(text[:200])
        return texts

    async def _fetch_bybit(self, merchant_id: str) -> tuple[int, int, int, list[str]]:
        url = "https://api2.bybit.com/fiat/otc/user/public/profile"
        params = {"userId": merchant_id}

        async with self._session.get(url, params=params) as resp:
            if resp.status != 200:
                logger.warning("ReviewFetcher Bybit profile status=%s for %s", resp.status, merchant_id)
                return 0, 0, 0, []
            data = await resp.json()

        info = data.get("result", {}).get("userInfo", {})
        if not info:
            logger.debug("ReviewFetcher Bybit empty profile %s", merchant_id)
            return 0, 0, 0, []

        pos = int(info.get("goodEvaluate") or 0)
        neg = int(info.get("badEvaluate") or 0)
        neutral = int(info.get("neutralEvaluate") or 0)

        bad_texts = await self._fetch_bybit_bad_texts(merchant_id)
        return max(pos, 0), max(neg, 0), max(neutral, 0), bad_texts

    async def _fetch_bybit_bad_texts(self, merchant_id: str) -> list[str]:
        url = "https://api2.bybit.com/fiat/otc/user/feedback/list"
        payload = {
            "userId": merchant_id,
            "evaluateType": "bad",
            "page": 1,
            "size": 10,
        }

        try:
            async with self._session.post(url, json=payload) as resp:
                if resp.status != 200:
                    logger.debug("ReviewFetcher Bybit bad_texts status=%s for %s", resp.status, merchant_id)
                    return []
                data = await resp.json()
        except Exception as e:
            logger.debug("ReviewFetcher Bybit bad_texts error %s: %s", merchant_id, e)
            return []

        texts = []
        for item in data.get("result", {}).get("items", []):
            text = (item.get("content") or item.get("feedback") or "").strip()
            if text and _has_bad_keywords(text):
                texts.append(text[:200])
        return texts

    async def _fetch_okx(self, merchant_id: str) -> tuple[int, int, int, list[str]]:
        profile_url = "https://www.okx.com/priapi/v1/otc/tradingOrders/ads-merchant-info"
        params = {"userId": merchant_id, "language": "uk_UA"}

        async with self._session.get(profile_url, params=params) as resp:
            if resp.status != 200:
                logger.warning("ReviewFetcher OKX profile status=%s for %s", resp.status, merchant_id)
                return 0, 0, 0, []
            data = await resp.json()

        info = data.get("data") or {}
        pos = int(info.get("positiveFeedbackCount") or 0)
        neg = int(info.get("negativeFeedbackCount") or 0)
        neutral = int(info.get("neutralFeedbackCount") or 0)

        return max(pos, 0), max(neg, 0), max(neutral, 0), []


def _has_bad_keywords(text: str) -> bool:
    t = text.lower()
    return any(kw in t for kw in BAD_KEYWORDS)
