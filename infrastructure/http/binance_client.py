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
            "origin": "https://c2c.binance.com",
            "referer": "https://c2c.binance.com/",
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

    async def fetch_negative_reviews(self, merchant_id: str, rows: int = 10, session_headers: dict = None, session_cookies: dict = None) -> list[dict]:
        """
        Реальні тексти негативних відгуків (через ПЕРЕХОПЛЕНУ веб-сесію).
        Використовує новий шлях list-by-page.
        """
        if not session_headers or not session_cookies:
            # Fallback на старий API, якщо сесії немає
            if not self.is_authenticated: return []
            url = "https://p2p.binance.com/bapi/c2c/v2/friendly/c2c/user/feedback-list"
            payload = {"advertiserNo": merchant_id, "type": 2, "page": 1, "rows": rows}
        else:
            # Робота через вкрадену сесію (web-шлях)
            url = "https://p2p.binance.com/bapi/c2c/v1/friendly/c2c/review/list-by-page"
            payload = {
                "advertiserNo": merchant_id,
                "page": 1,
                "rows": rows,
                "reviewType": "NEGATIVE"
            }

        req_headers = dict(session_headers) if session_headers else {}
        if req_headers:
            req_headers.pop("Content-Length", None)
            req_headers.pop("Accept-Encoding", None)
            req_headers["Referer"] = f"https://c2c.binance.com/uk-UA/advertiserDetail?advertiserNo={merchant_id}"

        try:
            if self._session is None: await self.__aenter__()
            response = await self._session.request(
                "POST", url, json=payload,
                headers=req_headers if req_headers else self._session.headers,
                cookies=session_cookies
            )
            if response.status_code in (401, 403):
                raise RuntimeError(f"AuthError: HTTP {response.status_code}")
                
            data = response.json()
            
            # Перевірка на внутрішню помилку авторизації Binance (часто код 000004 або 000008)
            code = str(data.get("code", ""))
            if code in ("000004", "000008", "401") or "Unauthorized" in str(data):
                raise RuntimeError(f"AuthError: Token expired. {data}")
                
            return data.get("data", {}).get("list", []) or data.get("data", []) or []
        except Exception as e:
            if "AuthError" in str(e):
                raise  # Прокидаємо вище для перехоплення у ReviewFetcher
            logger.debug("Binance fetch_negative_reviews [%s] error: %s", merchant_id, e)
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