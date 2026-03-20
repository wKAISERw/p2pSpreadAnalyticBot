# infrastructure/http/bybit_p2p_client.py
import logging
import os
from typing import Optional, Any
from .base_client import BaseHttpClient

logger = logging.getLogger(__name__)


class BybitP2PClient(BaseHttpClient):
    def __init__(self, proxy: Optional[str] = None):
        extra_headers = {
            "accept": "application/json",
            "origin": "https://www.bybit.com",
            "referer": "https://www.bybit.com/fiat/trade/otc/",
            "sec-ch-ua": '"Chromium";v="124", "Google Chrome";v="124", "Not-A.Brand";v="99"',
            "sec-ch-ua-mobile": "?0",
            "sec-ch-ua-platform": '"Windows"',
            "sec-fetch-dest": "empty",
            "sec-fetch-mode": "cors",
            "sec-fetch-site": "same-site",
        }
        super().__init__(proxy=proxy, extra_headers=extra_headers)

    def _build_dynamic_headers(self) -> dict:
        """Генерація унікальних W3C traceparent для кожного запиту"""
        trace_id = os.urandom(16).hex()
        span_id = os.urandom(8).hex()
        return {
            "x-trace-id": trace_id,
            "traceparent": f"00-{trace_id}-{span_id}-01"
        }

    async def fetch(self, url: str, payload: dict) -> Any:
        # Додаємо динамічні заголовки до конкретного запиту
        headers = self._build_dynamic_headers()
        data = await self._post(url, json=payload, headers=headers)

        if not data:
            return {}

        if data.get("ret_code", -1) != 0:
            logger.error("Помилка API Bybit: %s", data.get("ret_msg"))
            return {}

        return data