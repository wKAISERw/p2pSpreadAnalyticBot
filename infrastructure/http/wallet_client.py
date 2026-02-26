# infrastructure/http/wallet_client.py
import logging
from typing import Optional, Any
from curl_cffi.requests import AsyncSession
from config import settings

logger = logging.getLogger(__name__)

class WalletClient:
    def __init__(self, proxy: Optional[str] = None):
        self.proxy = proxy
        self.session: Optional[AsyncSession] = None

        self.headers = {
            "accept": "application/json",
            "content-type": "application/json",
            "X-API-Key": settings.wallet_token,  # <--- Офіційний API ключ
        }

    async def __aenter__(self):
        proxies = {"http": self.proxy, "https": self.proxy} if self.proxy else None
        self.session = AsyncSession(impersonate="chrome124", proxies=proxies, timeout=10.0)
        self.session.headers.update(self.headers)
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        if self.session:
            await self.session.close()

    async def fetch(self, url: str, payload: dict) -> Any:
        if not self.session:
            raise RuntimeError("Session is not initialized.")
        try:
            response = await self.session.post(url, json=payload)
            if response.status_code != 200:
                logger.error("Wallet Status %d: %s", response.status_code, response.text)
                return {}
            return response.json()
        except Exception as e:
            logger.error("Помилка запиту Wallet: %s", e)
            return {}