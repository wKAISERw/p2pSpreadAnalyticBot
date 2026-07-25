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
            "referer": "https://www.bybit.com/uk-UA/p2p/",
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

    async def fetch(self, url: str, payload: dict, headers: dict = None, cookies: dict = None) -> Any:
        """Сканування P2P ринку."""
        req_headers = self._build_dynamic_headers()
        if headers:
            req_headers.update(headers)
            
        kwargs = {}
        if cookies:
            kwargs["cookies"] = cookies
            
        data = await self._post(url, json=payload, headers=req_headers, **kwargs)

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

    async def fetch_merchant_feedback(self, merchant_id: str, session_headers: dict = None,
                                      session_cookies: dict = None) -> list[dict]:
        """
        Негативні відгуки про мерчанта (через ПЕРЕХОПЛЕНУ веб-сесію).
        Тепер ми стукаємо на реальний веб-ендпоінт appraiseList.
        """
        if not session_headers or not session_cookies:
            logger.debug("Bybit fetch_merchant_feedback [%s]: Немає перехопленої сесії в БД!", merchant_id)
            return []

        url = "https://www.bybit.com/x-api/fiat/otc/order/appraiseList"
        payload = {
            "userId": merchant_id,
            "page": "1",
            "size": "10",
            "appraiseType": "2"  # 2 = Bad (Негативні відгуки)
        }

        user_agent = (session_headers or {}).get("User-Agent") or (session_headers or {}).get("user-agent") or "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
        clean_headers = {
            "content-type": "application/json;charset=UTF-8",
            "accept": "application/json",
            "User-Agent": user_agent,
            "Referer": f"https://www.bybit.com/uk-UA/p2p/profile/{merchant_id}/USDT/UAH/item"
        }

        try:
            # Робимо POST запит напряму через curl_cffi сесію, передаючи вкрадені заголовки та кукіси
            if self._session is None:
                await self.__aenter__()

            # Очищаємо дефолтні заголовки сесії
            self._session.headers.clear()
            self._session.headers.update(clean_headers)

            response = await self._session.request(
                "POST",
                url,
                json=payload,
                cookies=session_cookies
            )
            
            if response.status_code in (401, 403):
                raise RuntimeError(f"AuthError: HTTP {response.status_code}")
                
            data = response.json()

            # Bybit auth error codes (10001-10005, 33004) or string match
            ret_code = data.get("ret_code", data.get("retCode", 0))
            if ret_code in (10001, 10002, 10003, 10004, 10005, 33004) or "unauthorized" in str(data).lower() or "not login" in str(data).lower():
                raise RuntimeError(f"AuthError: Token expired. {data}")
            if str(ret_code) not in ("0", ""):
                raise RuntimeError(f"ApiError: ret_code={ret_code}, ret_msg={data.get('ret_msg', data.get('retMsg', ''))}")

            # Повертаємо масив відгуків
            return data.get("result", {}).get("items", []) or []
        except Exception as e:
            if "AuthError" in str(e):
                raise  # Прокидаємо вище для перехоплення у ReviewFetcher
            raise RuntimeError(f"ApiError: {e}")

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

    async def get_order_info(self, order_id: str) -> dict:
        """
        Отримує інформацію про конкретний P2P ордер за ID.
        Endpoint: GET /fiat/otc/order/openapi/info
        Статуси: CREATED, PENDING, PAID, COMPLETED, CANCELLED, APPEAL
        """
        if not self.is_authenticated:
            return {}
        try:
            query = f"orderId={order_id}"
            url = f"https://api2.bybit.com/fiat/otc/order/openapi/info?{query}"
            headers = self._sign_headers(query)

            async with self:
                data = await self._get(url, headers=headers)

            if data.get("ret_code") == 0:
                return data.get("result") or {}
            logger.warning("[Bybit] get_order_info error: %s", data.get("ret_msg"))
            return {}
        except Exception as e:
            logger.debug("Bybit get_order_info [%s]: %s", order_id, e)
            return {}

    async def get_pending_orders(self) -> list[dict]:
        """
        Список активних P2P ордерів (не завершені).
        Endpoint: GET /fiat/otc/order/openapi/pending
        """
        if not self.is_authenticated:
            return []
        try:
            query = "page=1&size=20"
            url = f"https://api2.bybit.com/fiat/otc/order/openapi/pending?{query}"
            headers = self._sign_headers(query)

            async with self:
                data = await self._get(url, headers=headers)

            if data.get("ret_code") == 0:
                return data.get("result", {}).get("items") or []
            return []
        except Exception as e:
            logger.debug("Bybit get_pending_orders: %s", e)
            return []

    async def fetch_p2p_book_top(
        self,
        fiat: str = "UAH",
        asset: str = "USDT",
        side: int = 1,         # 1=BUY (продавці USDT), 0=SELL (покупці USDT)
        exclude_ad_id: str = "",
    ) -> float | None:
        """
        Повертає найкращу ціну конкурента у стакані.
        side=1: шукаємо серед продавців (для Maker-Sell нам важливо бути першими серед продавців).
        exclude_ad_id: виключаємо власне оголошення щоб не порівнювати самих із собою.
        """
        url = "https://api2.bybit.com/fiat/otc/item/online"
        payload = {
            "tokenId":    asset,
            "currencyId": fiat,
            "side":       side,
            "page":       1,
            "size":       5,
            "payment":    [],
        }
        headers = self._build_dynamic_headers()
        try:
            data = await self._post(url, json=payload, headers=headers)
            items = data.get("result", {}).get("items") or []
            for item in items:
                item_id = str(item.get("id", ""))
                if item_id == str(exclude_ad_id):
                    continue
                price_str = item.get("price") or item.get("unitPrice")
                if price_str:
                    return float(price_str)
            return None
        except Exception as e:
            logger.debug("Bybit fetch_p2p_book_top: %s", e)
            return None
