# infrastructure/http/mexc_client.py
import logging
from typing import Optional, Any
from .base_client import BaseHttpClient

logger = logging.getLogger(__name__)

BASE_URL = "https://www.mexc.com/api/platform/p2p/api/market"


class MexcClient(BaseHttpClient):
    def __init__(self, proxy: Optional[str] = None):
        extra_headers = {
            "accept": "application/json, text/plain, */*",
            "referer": "https://www.mexc.com/p2p",
        }
        super().__init__(proxy=proxy, extra_headers=extra_headers, timeout=8.0)

    async def fetch(self, payload: dict) -> Any:
        return await self._get(BASE_URL, params=payload)