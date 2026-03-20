# infrastructure/http/wallet_client.py
import logging
from typing import Optional, Any
from config import settings
from .base_client import BaseHttpClient

logger = logging.getLogger(__name__)

class WalletClient(BaseHttpClient):
    # Wallet 429 при агресивному polling — не ретраємо, одразу здаємось.
    # CircuitBreaker відключить Wallet після 3 провалів і відновить через 60s.
    # Краще ніж блокувати цикл на 3+6s кожного разу.
    MAX_RETRIES = 1
    RETRY_BACKOFF = [0.0]

    def __init__(self, proxy: Optional[str] = None):
        extra_headers = {
            "accept": "application/json",
            "content-type": "application/json",
            "X-API-Key": settings.wallet_token,
        }
        super().__init__(proxy=proxy, extra_headers=extra_headers, timeout=4.0)

    async def fetch(self, url: str, payload: dict) -> Any:
        return await self._post(url, json=payload)