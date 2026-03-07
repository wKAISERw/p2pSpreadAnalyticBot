# infrastructure/http/binance_client.py
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


class BinanceClient:
    def __init__(self, proxy: Optional[str] = None):
        self.proxy = proxy
        self.session: Optional[AsyncSession] = None

    async def __aenter__(self):
        proxies = {"http": self.proxy, "https": self.proxy} if self.proxy else None
        self.session = AsyncSession(impersonate="chrome124", proxies=proxies, timeout=10.0)
        self.session.headers.update({
            "accept": "*/*",
            "accept-language": "uk-UA,uk;q=0.9,en-US;q=0.8,en;q=0.7",
            "content-type": "application/json",
            "origin": "https://p2p.binance.com",
            "referer": "https://p2p.binance.com/",
            "user-agent": random.choice(USER_AGENTS),
            "x-trace-id": __import__("os").urandom(16).hex(),
        })
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        if self.session:
            await self.session.close()

    async def fetch(self, payload: dict) -> Any:
        if not self.session:
            raise RuntimeError("Session not initialized")
        url = "https://p2p.binance.com/bapi/c2c/v2/friendly/c2c/adv/search"
        try:
            response = await self.session.post(url, json=payload)
            if response.status_code != 200:
                logger.error("Binance P2P status %d", response.status_code)
                return {}
            return response.json()
        except Exception as e:
            logger.error("Binance P2P помилка: %s", e)
            return {}