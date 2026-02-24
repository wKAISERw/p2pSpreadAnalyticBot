import asyncio
import logging
import os
import random
import time
from collections import deque
from typing import Dict, Any

from curl_cffi.requests import AsyncSession
from tenacity import retry, stop_after_attempt, wait_exponential, retry_if_exception_type

from core.circuit_breaker import CircuitBreaker

logger = logging.getLogger(__name__)

USER_AGENTS = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/123.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
]


class Http403Error(Exception): pass


class Http429Error(Exception): pass


class BybitApiError(Exception): pass


class BybitP2PClient:
    def __init__(self, proxy: str | None = None):
        self.proxy = proxy
        self._cb = CircuitBreaker(failure_threshold=3, recovery_timeout=60.0)
        self._consecutive_403 = 0
        self._consecutive_429 = 0
        self._server_times = deque(maxlen=3)
        self.session: AsyncSession | None = None

    async def __aenter__(self):
        proxies = {"http": self.proxy, "https": self.proxy} if self.proxy else None
        # Імітація Chrome 124 для обходу Cloudflare
        self.session = AsyncSession(impersonate="chrome124", proxies=proxies, timeout=5.0)
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        if self.session:
            await self.session.close()  # <-- Додали await


    def _build_headers(self) -> Dict[str, str]:
        """Генерація заголовків з правильним порядком та W3C traceparent."""
        trace_id = os.urandom(16).hex()
        span_id = os.urandom(8).hex()

        return {
            "accept": "application/json",
            "accept-language": "en-US,en;q=0.9",
            "content-type": "application/json",
            "origin": "https://www.bybit.com",
            "referer": "https://www.bybit.com/fiat/trade/otc/",
            "sec-ch-ua": '"Chromium";v="124", "Google Chrome";v="124", "Not-A.Brand";v="99"',
            "sec-ch-ua-mobile": "?0",
            "sec-ch-ua-platform": '"Windows"',
            "sec-fetch-dest": "empty",
            "sec-fetch-mode": "cors",
            "sec-fetch-site": "same-site",
            "user-agent": random.choice(USER_AGENTS),
            "x-trace-id": trace_id,
            "traceparent": f"00-{trace_id}-{span_id}-01"
        }

    @retry(
        stop=stop_after_attempt(4),
        wait=wait_exponential(multiplier=1, min=1, max=4),
        retry=retry_if_exception_type((ConnectionError, TimeoutError))
    )
    async def _post_with_retry(self, url: str, payload: dict) -> Any:
        response = await self.session.post(url, json=payload, headers=self._build_headers())

        if response.status_code == 403:
            self._consecutive_403 += 1
            sleep_time = random.uniform(30.0, 60.0)
            logger.warning(f"⚠️ 403 Forbidden. Охолодження {sleep_time:.0f}s...")
            await asyncio.sleep(sleep_time)
            raise Http403Error("Отримано 403 від Bybit")

        if response.status_code == 429:
            self._consecutive_429 += 1
            sleep_time = random.uniform(12.0, 18.0)
            logger.warning(f"⚠️ 429 Too Many Requests. Охолодження {sleep_time:.0f}s...")
            await asyncio.sleep(sleep_time)
            raise Http429Error("Отримано 429 від Bybit")

        self._consecutive_403 = 0
        self._consecutive_429 = 0

        if response.status_code in (502, 504):
            raise ConnectionError(f"Bad Gateway/Timeout: {response.status_code}")

        response.raise_for_status()
        data = response.json()

        if data.get("ret_code", -1) != 0:
            raise BybitApiError(f"Помилка API Bybit: {data.get('ret_msg')}")

        self._check_cache(data)
        return data

    def _check_cache(self, data: dict):
        server_time = data.get("time")
        if server_time:
            self._server_times.append(server_time)
            if len(self._server_times) == 3 and len(set(self._server_times)) == 1:
                logger.warning("🟡 УВАГА: Bybit віддає закешовані дані (timestamp не змінюється)")

    async def fetch(self, url: str, payload: dict) -> Any:
        """Публічний метод. Завжди проходить через Circuit Breaker."""
        return await self._cb.call(self._post_with_retry(url, payload))