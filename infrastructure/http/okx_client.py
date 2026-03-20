# infrastructure/http/okx_client.py
import logging
from typing import Optional, Any
from .base_client import BaseHttpClient

logger = logging.getLogger(__name__)

class OkxClient(BaseHttpClient):
    def __init__(self, proxy: Optional[str] = None):
        extra_headers = {
            "accept": "application/json",
            "content-type": "application/json",
            "origin": "https://www.okx.com",
            "referer": "https://www.okx.com/p2p-markets/uah/buy-usdt",
            "x-p2p-client": "web",
            "sec-ch-ua": '"Chromium";v="124", "Google Chrome";v="124", "Not-A.Brand";v="99"',
            "sec-ch-ua-platform": '"Windows"',
        }
        super().__init__(proxy=proxy, extra_headers=extra_headers)

    async def fetch(self, url: str, payload: dict = None, method: str = "GET") -> Any:
        if method.upper() == "GET":
            return await self._get(url, params=payload)
        return await self._post(url, json=payload)