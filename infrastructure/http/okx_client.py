import asyncio
import logging
import random
from typing import Optional, Any
from curl_cffi.requests import AsyncSession

logger = logging.getLogger(__name__)

class OkxClient:
    def __init__(self, proxy: Optional[str] = None):
        self.proxy = proxy
        self.session: Optional[AsyncSession] = None
        self.headers = {
            "accept": "application/json",
            "accept-language": "uk-UA,uk;q=0.9,en-US;q=0.8,en;q=0.7", # Додано UA локу
            "content-type": "application/json",
            "origin": "https://www.okx.com",
            "referer": "https://www.okx.com/p2p-markets/uah/buy-usdt", # Більш точний реферер
            "x-p2p-client": "web",
            "sec-ch-ua": '"Chromium";v="124", "Google Chrome";v="124", "Not-A.Brand";v="99"',
            "sec-ch-ua-platform": '"Windows"',
        }

    async def __aenter__(self):
        proxies = {"http": self.proxy, "https": self.proxy} if self.proxy else None
        # Імітація Chrome 124, як у Bybit клієнті
        self.session = AsyncSession(impersonate="chrome124", proxies=proxies, timeout=10.0)
        self.session.headers.update(self.headers)
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        if self.session:
            await self.session.close()

    async def fetch(self, url: str, payload: dict = None, method: str = "GET") -> Any:
        if not self.session:
            raise RuntimeError("Session is not initialized.")

        try:
            if method.upper() == "GET":
                # curl_cffi використовує params для додавання в URL
                response = await self.session.get(url, params=payload)
            else:
                response = await self.session.post(url, json=payload)

            if response.status_code != 200:
                logger.error(f"🌐 OKX Status {response.status_code}")
                return {}

            return response.json()
        except Exception as e:
            logger.error("🌐 Помилка запиту OKX: %s", e)
            return {}