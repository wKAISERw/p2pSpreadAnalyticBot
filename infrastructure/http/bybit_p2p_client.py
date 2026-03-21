# infrastructure/http/bybit_p2p_client.py
"""
BybitP2PClient — P2P сканування (анонімний) + автентифіковані запити.

Автентифікація Bybit V5:
  - X-BAPI-API-KEY: api_key
  - X-BAPI-TIMESTAMP: unix ms
  - X-BAPI-SIGN: HMAC-SHA256(timestamp + api_key + recv_window + queryString)
  - X-BAPI-RECV-WINDOW: 5000
"""
from __future__ import annotations

import hashlib
import hmac
import logging
import os
import time
from typing import Any, Optional

from .base_client import BaseHttpClient

logger = logging.getLogger(__name__)


class BybitP2PClient(BaseHttpClient):
    RECV_WINDOW = "5000"

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
        self._api_key: str = ""
        self._api_secret: str = ""

    def set_credentials(self, api_key: str, api_secret: str) -> None:
        """Встановлює API ключі для автентифікованих запитів."""
        self._api_key = api_key
        self._api_secret = api_secret
        logger.debug("BybitP2PClient: credentials set")

    @property
    def is_authenticated(self) -> bool:
        return bool(self._api_key and self._api_secret)

    def _sign_headers(self, query_string: str = "") -> dict:
        """Генерує заголовки автентифікації для Bybit V5."""
        ts = str(int(time.time() * 1000))
        sign_str = ts + self._api_key + self.RECV_WINDOW + (query_string or "")
        signature = hmac.new(
            self._api_secret.encode("utf-8"),
            sign_str.encode("utf-8"),
            hashlib.sha256,
        ).hexdigest()
        return {
            "X-BAPI-API-KEY":     self._api_key,
            "X-BAPI-TIMESTAMP":   ts,
            "X-BAPI-SIGN":        signature,
            "X-BAPI-RECV-WINDOW": self.RECV_WINDOW,
        }

    def _build_dynamic_headers(self) -> dict:
        """Генерація унікальних W3C traceparent для анонімних запитів."""
        trace_id = os.urandom(16).hex()
        span_id = os.urandom(8).hex()
        return {
            "x-trace-id": trace_id,
            "traceparent": f"00-{trace_id}-{span_id}-01",
        }

    async def fetch(self, url: str, payload: dict) -> Any:
        """Сканування P2P ринку (анонімний)."""
        headers = self._build_dynamic_headers()
        data = await self._post(url, json=payload, headers=headers)

        if not data:
            return {}
        if data.get("ret_code", -1) != 0:
            logger.error("Помилка API Bybit: %s", data.get("ret_msg"))
            return {}
        return data

    async def fetch_merchant_profile(self, merchant_id: str) -> dict:
        """
        Повний профіль P2P мерчанта через Bybit V5 API.
        Потребує API ключів.
        """
        if not self.is_authenticated:
            return {}

        url = "https://api.bybit.com/v5/user/query-api"
        query = f"userId={merchant_id}"
        headers = self._sign_headers(query)

        try:
            data = await self._get(
                f"https://api2.bybit.com/fiat/otc/user/public/profile?userId={merchant_id}",
                headers=headers,
            )
            return data.get("result", {}) or {}
        except Exception as e:
            logger.debug("Bybit fetch_merchant_profile [%s]: %s", merchant_id, e)
            return {}

    async def fetch_merchant_feedback(self, merchant_id: str) -> list[dict]:
        """Негативні відгуки про мерчанта."""
        if not self.is_authenticated:
            return []

        url = "https://api2.bybit.com/fiat/otc/user/feedback/list"
        payload = {
            "userId": merchant_id,
            "evaluateType": "bad",
            "page": 1,
            "size": 10,
        }
        query = "&".join(f"{k}={v}" for k, v in payload.items())
        headers = {**self._sign_headers(query), **self._build_dynamic_headers()}

        try:
            data = await self._post(url, json=payload, headers=headers)
            return data.get("result", {}).get("items", []) or []
        except Exception as e:
            logger.debug("Bybit fetch_merchant_feedback [%s]: %s", merchant_id, e)
            return []

    async def fetch_account_balance(self) -> list[dict]:
        """Баланс Bybit unified account."""
        if not self.is_authenticated:
            return []

        query = "accountType=UNIFIED"
        url = f"https://api.bybit.com/v5/account/wallet-balance?{query}"
        headers = self._sign_headers(query)

        try:
            data = await self._get(url, headers=headers)
            coins = data.get("result", {}).get("list", [{}])[0].get("coin", [])
            return [
                {"coin": c["coin"], "free": c["availableToWithdraw"], "locked": c["locked"]}
                for c in coins
                if c.get("coin") in ("USDT", "UAH")
            ]
        except Exception as e:
            logger.debug("Bybit fetch_account_balance: %s", e)
            return []