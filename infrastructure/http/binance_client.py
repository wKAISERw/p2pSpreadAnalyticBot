# infrastructure/http/binance_client.py
"""
BinanceClient — P2P сканування (анонімний) + автентифіковані запити.

Анонімний режим: сканування ринку (вже працює).
Автентифікований режим: повні профілі мерчантів, реальні відгуки.

Автентифікація Binance P2P (bapi/c2c/):
  - Header: X-MBX-APIKEY = api_key
  - Param:  signature = HMAC-SHA256(query_string, api_secret)
  - Param:  timestamp = unix ms
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

    async def _get(self, url: str, **kwargs) -> Any:
        """🚀 ВЛАСНА БРОНЯ BINANCE: Захист від зміни форматів та перевірка токенів для GET."""
        try:
            data = await super()._get(url, **kwargs)

            # Якщо повернув голий масив
            if isinstance(data, list):
                return data
            if not isinstance(data, dict):
                return {}

            # Перевірка на помилки сесії Binance
            code = str(data.get("code", ""))
            if code in ("000004", "000008", "401") or "Unauthorized" in str(data):
                raise RuntimeError(f"AuthError: Token expired. {data}")
            if code and code not in ("000000", "0"):
                raise RuntimeError(f"ApiError: code={code}, message={data.get('message', '')}")

            # Розумне розгортання "data"
            result = data.get("data")
            if isinstance(result, dict):
                return result
            if isinstance(result, list):
                return result
            return {}

        except Exception as e:
            if "AuthError" in str(e):
                raise  # Прокидаємо вище для SessionManager/ReviewFetcher
            raise RuntimeError(f"ApiError: {e}")

    async def _post(self, url: str, **kwargs) -> Any:
        """🚀 ВЛАСНА БРОНЯ BINANCE: Захист від зміни форматів та перевірка токенів."""
        try:
            data = await super()._post(url, **kwargs)

            # Якщо повернув голий масив
            if isinstance(data, list):
                return data
            if not isinstance(data, dict):
                return {}

            # Перевірка на помилки сесії Binance
            code = str(data.get("code", ""))
            if code in ("000004", "000008", "401") or "Unauthorized" in str(data):
                raise RuntimeError(f"AuthError: Token expired. {data}")
            if code and code not in ("000000", "0"):
                raise RuntimeError(f"ApiError: code={code}, message={data.get('message', '')}")

            # Розумне розгортання "data"
            result = data.get("data")
            if isinstance(result, dict):
                if "list" in result and isinstance(result["list"], list):
                    return result["list"]
                return result
            if isinstance(result, list):
                return result
            return []

        except Exception as e:
            if "AuthError" in str(e):
                raise  # Прокидаємо вище для SessionManager/ReviewFetcher
            raise RuntimeError(f"ApiError: {e}")

    async def fetch(self, payload: dict, headers: dict = None, cookies: dict = None) -> Any:
        """Сканування P2P ринку."""
        # Для пошуку ордерів v1 вже не існує, залишаємо тільки v2
        endpoints = [
            "https://p2p.binance.com/bapi/c2c/v2/friendly/c2c/adv/search"
        ]

        kwargs = {}
        if headers:
            kwargs["headers"] = headers
        if cookies:
            kwargs["cookies"] = cookies

        last_exc = None
        for url in endpoints:
            try:
                data = await self._post(url, json=payload, **kwargs)

                # 🚀 ФІКС: _post тепер віддає чистий list, але твій
                # файл exchanges/binance.py очікує формат {"data": [...]}.
                # Загортаємо дані назад у словник, щоб система не сварилась!
                if isinstance(data, list):
                    return {"code": "000000", "data": data}

                if isinstance(data, dict):
                    # Якщо це вже словник і в ньому є "data"
                    if "data" in data:
                        return data
                    # Якщо немає, віддаємо порожній безпечний формат
                    return {"code": "000000", "data": []}

                return {"code": "000000", "data": []}

            except Exception as e:
                logger.debug("Binance fetch URL %s failed: %s", url, e)
                last_exc = e

        # Якщо Binance дійсно впав — мовчки повертаємо порожній стакан, щоб сканер не зупинявся
        logger.debug("All Binance fetch endpoints failed. Last error: %s", last_exc)
        return {"code": "000000", "data": []}

    async def fetch_merchant_profile(self, merchant_id: str, session_headers: dict = None,
                                     session_cookies: dict = None) -> dict:
        """Повний профіль мерчанта (ads list)."""
        url = f"https://p2p.binance.com/bapi/c2c/v2/friendly/c2c/user/profile-and-ads-list?userNo={merchant_id}"
        req_headers = dict(session_headers) if session_headers else {}
        if req_headers:
            req_headers.pop("Content-Length", None)
            req_headers.pop("Accept-Encoding", None)
        try:
            data = await self._get(url, headers=req_headers if req_headers else None, cookies=session_cookies)
            if isinstance(data, dict) and data:
                return data
        except Exception as e:
            logger.debug("fetch_merchant_profile URL %s [%s]: %s", url, merchant_id, e)
        return {}

    async def fetch_negative_reviews(self, merchant_id: str, rows: int = 10, session_headers: dict = None,
                                     session_cookies: dict = None) -> list[dict]:
        """Реальні тексти негативних відгуків + 🚀 ФОЛБЕК."""
        req_headers = dict(session_headers) if session_headers else {}
        if req_headers:
            req_headers.pop("Content-Length", None)
            req_headers.pop("Accept-Encoding", None)
            req_headers["Referer"] = f"https://c2c.binance.com/uk-UA/advertiserDetail?advertiserNo={merchant_id}"

        # Формуємо payload і список URL залежно від наявності сесії
        if not session_headers or not session_cookies:
            if not self.is_authenticated:
                return []
            payload = {"userNo": merchant_id, "rating": 3, "page": 1, "rows": rows}
            endpoints = [
                "https://c2c.binance.com/bapi/c2c/v1/friendly/c2c/review/list-by-page",
                "https://p2p.binance.com/bapi/c2c/v1/friendly/c2c/review/list-by-page"
            ]
        else:
            payload = {"userNo": merchant_id, "rating": 3, "page": 1, "rows": rows}
            endpoints = [
                "https://c2c.binance.com/bapi/c2c/v1/friendly/c2c/review/list-by-page",
                "https://p2p.binance.com/bapi/c2c/v1/friendly/c2c/review/list-by-page"
            ]

        last_exc = None
        for url in endpoints:
            try:
                data = await self._post(url, json=payload, headers=req_headers if req_headers else None,
                                         cookies=session_cookies)
                if isinstance(data, list):
                    return data
            except Exception as e:
                if "AuthError" in str(e):
                    raise
                logger.debug("Binance fetch_negative_reviews URL %s failed: %s", url, e)
                last_exc = e

        logger.debug("All Binance review endpoints failed for %s. Last error: %s", merchant_id, last_exc)
        return []

    async def fetch_account_balance(self) -> list[dict]:
        """Баланс акаунта (USDT, UAH та ін.)."""
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