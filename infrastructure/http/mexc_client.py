# infrastructure/http/mexc_client.py
import logging
import random
from typing import Optional, Any
from curl_cffi.requests import AsyncSession

logger = logging.getLogger(__name__)

USER_AGENTS = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/123.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
]

class MexcClient:
    # НОВА АДРЕСА API
    BASE_URL = "https://www.mexc.com/api/platform/p2p/api/market"

    def __init__(self, proxy: Optional[str] = None):
        self.proxy = proxy
        self.session: Optional[AsyncSession] = None

    async def __aenter__(self):
        proxies = {"http": self.proxy, "https": self.proxy} if self.proxy else None
        self.session = AsyncSession(impersonate="chrome124", proxies=proxies, timeout=10.0)
        self.session.headers.update({
            "accept": "application/json, text/plain, */*",
            "accept-language": "uk-UA,uk;q=0.9,en-US;q=0.8",
            "referer": "https://www.mexc.com/p2p",
            "user-agent": random.choice(USER_AGENTS),
        })
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        if self.session:
            await self.session.close()

    async def fetch(self, payload: dict) -> Any:
        if not self.session:
            raise RuntimeError("Session not initialized")
        try:
            # ТЕПЕР GET ЗАПИТ (params замість json)
            response = await self.session.get(self.BASE_URL, params=payload)
            if response.status_code != 200:
                logger.error("MEXC status %d", response.status_code)
                return {}
            return response.json()
        except Exception as e:
            logger.error("MEXC помилка: %s", e)
            return {}