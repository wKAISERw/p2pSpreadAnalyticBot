# infrastructure/http/binance_client.py
import logging
import os
from typing import Optional, Any
from .base_client import BaseHttpClient

logger = logging.getLogger(__name__)

class BinanceClient(BaseHttpClient):
    def __init__(self, proxy: Optional[str] = None):
        extra_headers = {
            "accept": "*/*",
            "content-type": "application/json",
            "origin": "https://p2p.binance.com",
            "referer": "https://p2p.binance.com/",
            "x-trace-id": os.urandom(16).hex(),
        }
        super().__init__(proxy=proxy, extra_headers=extra_headers)

    async def fetch(self, payload: dict) -> Any:
        url = "https://p2p.binance.com/bapi/c2c/v2/friendly/c2c/adv/search"
        return await self._post(url, json=payload)