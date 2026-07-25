# infrastructure/http/base_client.py
from __future__ import annotations
import asyncio
import logging
import random
from typing import Any, Optional
from curl_cffi.requests import AsyncSession, errors

logger = logging.getLogger("BaseHttpClient")

USER_AGENTS = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/123.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
]


class BaseHttpClient:
    """
    Базовий клієнт на основі curl_cffi.
    Імітує Chrome 124 для обходу Cloudflare, має вбудовані ретраї.
    """
    MAX_RETRIES = 3
    RETRY_BACKOFF = [1.0, 2.0, 4.0]

    def __init__(
            self,
            proxy: Optional[str] = None,
            extra_headers: Optional[dict] = None,
            timeout: float = 10.0,
    ):
        self.proxy = proxy
        self._timeout = timeout
        self._extra_headers = extra_headers or {}
        self._session: Optional[AsyncSession] = None

    async def __aenter__(self) -> "BaseHttpClient":
        proxies = {"http": self.proxy, "https": self.proxy} if self.proxy else None

        self._session = AsyncSession(
            impersonate="chrome124",
            proxies=proxies,
            timeout=self._timeout
        )

        self._session.headers.update({
            "Accept-Language": "uk-UA,uk;q=0.9,en-US;q=0.8",
        })
        self._session.headers.update(self._extra_headers)
        
        # Видаляємо дефолтний User-Agent з сесії, щоб він задавався тільки на рівні запиту
        self._session.headers.pop("User-Agent", None)
        self._session.headers.pop("user-agent", None)

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
        # Авто-ініціалізація сесії якщо клієнт використовується без async with
        # (наприклад account clients що живуть весь час, не як context manager)
        if self._session is None:
            await self.__aenter__()

        # Нормалізуємо заголовки запиту, щоб уникнути дублів User-Agent
        headers = kwargs.get("headers")
        if headers is None:
            headers = {}
        else:
            headers = dict(headers)
            
        has_ua = False
        ua_key = "User-Agent"
        for k, v in list(headers.items()):
            if k.lower() == "user-agent":
                has_ua = True
                ua_key = k
                break
                
        if not has_ua:
            headers["User-Agent"] = random.choice(USER_AGENTS)
        else:
            ua_val = headers.pop(ua_key)
            headers["User-Agent"] = ua_val

        kwargs["headers"] = headers

        last_exc: Optional[Exception] = None

        for attempt, backoff in enumerate(self.RETRY_BACKOFF, 1):
            try:
                response = await self._session.request(method, url, **kwargs)

                if response.status_code == 429:
                    logger.warning("%s 429 RateLimit on %s (attempt %d)", self.__class__.__name__, url, attempt)
                    await asyncio.sleep(backoff * 3)
                    continue

                if response.status_code in (502, 503, 504):
                    logger.warning("%s HTTP %d on %s (attempt %d)", self.__class__.__name__, response.status_code, url,
                                   attempt)
                    await asyncio.sleep(backoff)
                    continue

                if response.status_code != 200:
                    # 🚀 ХОТФІКС: Викидаємо помилку, але обрізаємо HTML, щоб не забивати логи
                    error_text = response.text.strip()
                    if len(error_text) > 250:
                        error_text = error_text[:250] + "... [TRUNCATED HTML]"
                    raise RuntimeError(f"Unexpected Status {response.status_code}: {error_text}")

                return response.json()


            except errors.RequestsError as e:
                last_exc = e
                logger.debug("%s request error (attempt %d/%d): %s", self.__class__.__name__, attempt, self.MAX_RETRIES,
                             e)
                if attempt < self.MAX_RETRIES:
                    await asyncio.sleep(backoff)
        # 🚀 ХОТФІКС: Викидаємо помилку, коли вичерпано ретраї
        raise RuntimeError(f"{self.__class__.__name__} failed after {self.MAX_RETRIES} retries: {last_exc}")