"""
infrastructure/http/base_client.py — Спільна логіка HTTP-клієнтів.
Всі клієнти успадковують або використовують цей клас.
"""
from __future__ import annotations
import asyncio
import logging
from typing import Any, Optional
import aiohttp

logger = logging.getLogger("BaseHttpClient")

DEFAULT_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept-Language": "uk-UA,uk;q=0.9,en-US;q=0.8",
}

DEFAULT_TIMEOUT = aiohttp.ClientTimeout(total=10.0, connect=5.0, sock_read=8.0)


class BaseHttpClient:
    """
    Базовий клас для всіх P2P HTTP-клієнтів.
    Надає: retry з backoff, спільні заголовки, централізоване логування.
    """
    MAX_RETRIES = 3
    RETRY_BACKOFF = [1.0, 2.0, 4.0]

    def __init__(
        self,
        base_url: str = "",
        extra_headers: Optional[dict] = None,
        timeout: Optional[aiohttp.ClientTimeout] = None,
    ):
        self._base_url = base_url
        self._headers = {**DEFAULT_HEADERS, **(extra_headers or {})}
        self._timeout = timeout or DEFAULT_TIMEOUT
        self._session: Optional[aiohttp.ClientSession] = None

    async def __aenter__(self) -> "BaseHttpClient":
        self._session = aiohttp.ClientSession(
            headers=self._headers,
            timeout=self._timeout,
        )
        return self

    async def __aexit__(self, *_) -> None:
        if self._session:
            await self._session.close()
            self._session = None

    async def _get(self, url: str, **kwargs) -> Any:
        return await self._request("GET", url, **kwargs)

    async def _post(self, url: str, **kwargs) -> Any:
        return await self._request("POST", url, **kwargs)

    async def _request(self, method: str, url: str, **kwargs) -> Any:
        last_exc: Optional[Exception] = None
        for attempt, backoff in enumerate(self.RETRY_BACKOFF, 1):
            try:
                async with self._session.request(method, url, **kwargs) as resp:
                    if resp.status == 429:
                        logger.warning(
                            "%s 429 RateLimit on %s (attempt %d)",
                            self.__class__.__name__, url, attempt,
                        )
                        await asyncio.sleep(backoff * 3)
                        continue
                    resp.raise_for_status()
                    return await resp.json(content_type=None)
            except (aiohttp.ClientError, asyncio.TimeoutError) as e:
                last_exc = e
                logger.debug(
                    "%s request error (attempt %d/%d): %s",
                    self.__class__.__name__, attempt, self.MAX_RETRIES, e,
                )
                if attempt < self.MAX_RETRIES:
                    await asyncio.sleep(backoff)
        raise RuntimeError(
            f"{self.__class__.__name__} failed after {self.MAX_RETRIES} retries: {last_exc}"
        )
