# infrastructure/http/okx_client.py
"""
OkxClient — P2P сканування (анонімний) + автентифіковані запити.

Автентифікація OKX:
  - OK-ACCESS-KEY: api_key
  - OK-ACCESS-SIGN: Base64(HMAC-SHA256(timestamp+method+path+body, secret))
  - OK-ACCESS-TIMESTAMP: ISO 8601
  - OK-ACCESS-PASSPHRASE: passphrase (обов'язковий для OKX)
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import logging
from datetime import datetime, timezone
from typing import Any, Optional

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
        self._api_key: str = ""
        self._api_secret: str = ""
        self._passphrase: str = ""

    def set_credentials(self, api_key: str, api_secret: str, passphrase: str = "") -> None:
        """Встановлює API ключі для автентифікованих запитів."""
        self._api_key = api_key
        self._api_secret = api_secret
        self._passphrase = passphrase
        logger.debug("OkxClient: credentials set")

    @property
    def is_authenticated(self) -> bool:
        return bool(self._api_key and self._api_secret and self._passphrase)

    def _sign_headers(self, method: str, path: str, body: str = "") -> dict:
        """Генерує заголовки автентифікації для OKX API."""
        timestamp = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"
        message = timestamp + method.upper() + path + (body or "")
        signature = base64.b64encode(
            hmac.new(
                self._api_secret.encode("utf-8"),
                message.encode("utf-8"),
                hashlib.sha256,
            ).digest()
        ).decode()
        return {
            "OK-ACCESS-KEY":        self._api_key,
            "OK-ACCESS-SIGN":       signature,
            "OK-ACCESS-TIMESTAMP":  timestamp,
            "OK-ACCESS-PASSPHRASE": self._passphrase,
        }

    async def fetch(self, url: str, payload: dict = None, method: str = "GET", headers: dict = None, cookies: dict = None) -> Any:
        """Сканування P2P ринку."""
        merged_headers = {
            "accept": "application/json",
            "content-type": "application/json",
            "origin": "https://www.okx.com",
            "referer": "https://www.okx.com/p2p-markets/uah/buy-usdt",
            "x-p2p-client": "web",
            "sec-ch-ua": '"Chromium";v="124", "Google Chrome";v="124", "Not-A.Brand";v="99"',
            "sec-ch-ua-platform": '"Windows"',
        }
        if headers:
            for k, v in headers.items():
                if v:
                    merged_headers[k] = v

        kwargs = {"headers": merged_headers}
        if cookies:
            kwargs["cookies"] = cookies
            
        if method.upper() == "GET":
            return await self._get(url, params=payload, **kwargs)
        return await self._post(url, json=payload, **kwargs)

    async def fetch_merchant_profile(self, merchant_id: str) -> dict:
        """
        Повний профіль мерчанта через автентифікований endpoint.
        Потребує API ключів + passphrase.
        """
        if not self.is_authenticated:
            return {}

        path = f"/api/v5/c2c/order/user-info?userId={merchant_id}"
        url = f"https://www.okx.com{path}"
        headers = self._sign_headers("GET", path)

        try:
            data = await self._get(url, headers=headers)
            return data.get("data", {}) or {}
        except Exception as e:
            logger.debug("OKX fetch_merchant_profile [%s]: %s", merchant_id, e)
            return {}

    async def fetch_merchant_feedback(self, merchant_id: str) -> list[dict]:
        """
        Відгуки про мерчанта через автентифікований endpoint.
        """
        if not self.is_authenticated:
            return []

        path = f"/api/v5/c2c/order/user-feedback?userId={merchant_id}&type=2&limit=10"
        url = f"https://www.okx.com{path}"
        headers = self._sign_headers("GET", path)

        try:
            data = await self._get(url, headers=headers)
            return data.get("data", []) or []
        except Exception as e:
            logger.debug("OKX fetch_merchant_feedback [%s]: %s", merchant_id, e)
            return []

    async def fetch_account_balance(self) -> list[dict]:
        """Баланс акаунта."""
        if not self.is_authenticated:
            return []

        path = "/api/v5/account/balance?ccy=USDT,UAH"
        url = f"https://www.okx.com{path}"
        headers = self._sign_headers("GET", path)

        try:
            data = await self._get(url, headers=headers)
            details = data.get("data", [{}])[0].get("details", [])
            return [
                {"coin": d["ccy"], "free": d["availBal"], "locked": d["frozenBal"]}
                for d in details
            ]
        except Exception as e:
            logger.debug("OKX fetch_account_balance: %s", e)
            return []