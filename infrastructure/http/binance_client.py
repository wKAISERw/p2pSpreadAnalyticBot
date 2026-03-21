# infrastructure/http/binance_client.py
"""
BinanceClient — P2P сканування (анонімний) + автентифіковані запити.

Анонімний режим: сканування ринку (вже працює).
Автентифікований режим: повні профілі мерчантів, реальні відгуки.

Автентифікація Binance P2P (bapi/c2c/):
  - Header: X-MBX-APIKEY = api_key
  - Param:  signature = HMAC-SHA256(query_string, api_secret)
  - Param:  timestamp = unix ms

Примітка: /bapi/c2c/ endpoints використовують той самий HMAC що і
основний REST API, але окремий домен p2p.binance.com.
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
        self._api_key: str = ""
        self._api_secret: str = ""

    def set_credentials(self, api_key: str, api_secret: str) -> None:
        """Встановлює API ключі для автентифікованих запитів."""
        self._api_key = api_key
        self._api_secret = api_secret
        if api_key and self._session:
            self._session.headers.update({"X-MBX-APIKEY": api_key})
        logger.debug("BinanceClient: credentials set (auth=%s)", bool(api_key))

    @property
    def is_authenticated(self) -> bool:
        return bool(self._api_key and self._api_secret)

    def _sign(self, params: dict) -> dict:
        """Додає timestamp і HMAC-SHA256 підпис до params."""
        params["timestamp"] = int(time.time() * 1000)
        query = "&".join(f"{k}={v}" for k, v in params.items())
        signature = hmac.new(
            self._api_secret.encode("utf-8"),
            query.encode("utf-8"),
            hashlib.sha256,
        ).hexdigest()
        params["signature"] = signature
        return params

    async def fetch(self, payload: dict) -> Any:
        """Сканування P2P ринку (анонімний)."""
        url = "https://p2p.binance.com/bapi/c2c/v2/friendly/c2c/adv/search"
        return await self._post(url, json=payload)

    async def fetch_merchant_profile(self, merchant_id: str) -> dict:
        """
        Повний профіль мерчанта з відгуками.
        Потребує API ключів — без них повертає порожній dict.
        """
        if not self.is_authenticated:
            return {}

        url = "https://p2p.binance.com/bapi/c2c/v2/friendly/c2c/user/profile-and-ads"
        payload = {"advertiserNo": merchant_id, "page": 1, "rows": 1}

        try:
            data = await self._post(url, json=payload)
            return data.get("data", {}) or {}
        except Exception as e:
            logger.debug("fetch_merchant_profile [%s]: %s", merchant_id, e)
            return {}

    async def fetch_negative_reviews(self, merchant_id: str, rows: int = 10) -> list[dict]:
        """
        Реальні тексти негативних відгуків.
        Потребує API ключів — без них повертає [].
        """
        if not self.is_authenticated:
            return []

        url = "https://p2p.binance.com/bapi/c2c/v2/friendly/c2c/user/feedback-list"
        payload = {
            "advertiserNo": merchant_id,
            "type": 2,  # 2 = negative
            "page": 1,
            "rows": rows,
        }

        try:
            data = await self._post(url, json=payload)
            return data.get("data", []) or []
        except Exception as e:
            logger.debug("fetch_negative_reviews [%s]: %s", merchant_id, e)
            return []

    async def fetch_account_balance(self) -> list[dict]:
        """
        Баланс акаунта (USDT, UAH та ін.).
        Використовує офіційний підписаний endpoint.
        """
        if not self.is_authenticated:
            return []

        url = "https://api.binance.com/sapi/v1/capital/config/getall"
        params = self._sign({})
        try:
            data = await self._get(url, params=params)
            if isinstance(data, list):
                return [
                    {"coin": item["coin"], "free": item["free"], "locked": item["locked"]}
                    for item in data
                    if item.get("coin") in ("USDT", "UAH", "BNB")
                ]
            return []
        except Exception as e:
            logger.debug("fetch_account_balance: %s", e)
            return []