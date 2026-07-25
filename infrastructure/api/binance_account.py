# infrastructure/api/binance_account.py
"""
Binance офіційний API — акаунт.
Документація: https://binance-docs.github.io/apidocs/spot/en/

Відповідальність: баланс, P2P угоди, статистика.
НЕ відповідає за: сканування ринку → infrastructure/http/binance_client.py
"""
from __future__ import annotations
import hashlib, hmac, logging, time, urllib.parse
from typing import Optional
from infrastructure.http.base_client import BaseHttpClient

logger = logging.getLogger("BinanceAccount")
BASE_URL = "https://api.binance.com"
P2P_URL  = "https://p2p.binance.com"

class BinanceAccountClient(BaseHttpClient):
    def __init__(self, api_key: str = "", api_secret: str = "", proxy: Optional[str] = None):
        super().__init__(proxy=proxy, extra_headers={
            "content-type": "application/json",
            "origin": "https://p2p.binance.com",
        })
        self._api_key = api_key
        self._api_secret = api_secret

    def set_credentials(self, api_key: str, api_secret: str) -> None:
        self._api_key = api_key
        self._api_secret = api_secret
        self._session.headers.update({"X-MBX-APIKEY": api_key}) if self._session else None

    @property
    def is_authenticated(self) -> bool:
        return bool(self._api_key and self._api_secret)

    def _sign(self, params: dict) -> dict:
        params["timestamp"] = int(time.time() * 1000)
        qs = urllib.parse.urlencode(params)
        params["signature"] = hmac.new(self._api_secret.encode(), qs.encode(), hashlib.sha256).hexdigest()
        return params

    async def _signed_get(self, url: str, params: dict = None) -> dict:
        headers = {"X-MBX-APIKEY": self._api_key}
        return await self._get(url, params=self._sign(params or {}), headers=headers)

    async def _signed_post(self, url: str, params: dict = None) -> dict:
        headers = {"X-MBX-APIKEY": self._api_key, "content-type": "application/json"}
        return await self._post(url, params=self._sign(params or {}), headers=headers)

    async def get_funding_balance(self) -> list[dict]:
        """Баланс Funding акаунта (для P2P)."""
        if not self.is_authenticated: return []
        try:
            data = await self._signed_post(f"{BASE_URL}/sapi/v1/asset/get-funding-asset")
            return [{"coin": d["asset"], "free": float(d["free"]), "locked": float(d["locked"]),
                     "total": float(d["free"]) + float(d["locked"])}
                    for d in (data if isinstance(data, list) else [])
                    if float(d.get("free", 0)) + float(d.get("locked", 0)) > 0]
        except Exception as e:
            logger.warning("get_funding_balance: %s", e); return []

    async def get_spot_balance(self) -> list[dict]:
        """Баланс Spot акаунта."""
        if not self.is_authenticated: return []
        try:
            data = await self._signed_get(f"{BASE_URL}/sapi/v1/capital/config/getall")
            return [{"coin": d["coin"], "free": float(d["free"]), "locked": float(d["locked"]),
                     "total": float(d["free"]) + float(d["locked"])}
                    for d in (data if isinstance(data, list) else [])
                    if float(d.get("free", 0)) + float(d.get("locked", 0)) > 0]
        except Exception as e:
            logger.warning("get_spot_balance: %s", e); return []

    async def get_balance(self) -> list[dict]:
        """Комбінований баланс Spot + Funding акаунтів."""
        spot = await self.get_spot_balance()
        funding = await self.get_funding_balance()
        merged = {}
        for coin_dict in spot + funding:
            coin = coin_dict["coin"]
            if coin not in merged:
                merged[coin] = {
                    "coin": coin,
                    "free": 0.0,
                    "locked": 0.0,
                    "total": 0.0
                }
            merged[coin]["free"] += coin_dict["free"]
            merged[coin]["locked"] += coin_dict["locked"]
            merged[coin]["total"] += coin_dict["total"]
        return list(merged.values())

    async def get_my_p2p_orders(self, trade_type: str = "BUY", rows: int = 20) -> list[dict]:
        """
        Мої P2P угоди.
        trade_type: BUY | SELL
        """
        if not self.is_authenticated: return []
        payload = {"tradeType": trade_type, "page": 1, "rows": rows}
        try:
            data = await self._signed_post(f"{P2P_URL}/bapi/c2c/v2/private/c2c/order-match/listUserOrderHistory", params=payload)
            return data.get("data", []) or []
        except Exception as e:
            logger.warning("get_my_p2p_orders: %s", e); return []

    async def get_account_info(self) -> dict:
        """Базова інформація акаунта."""
        if not self.is_authenticated: return {}
        try:
            data = await self._signed_get(f"{BASE_URL}/api/v3/account")
            return {"accountType": data.get("accountType"), "canTrade": data.get("canTrade"),
                    "uid": data.get("uid")}
        except Exception as e:
            logger.warning("get_account_info: %s", e); return {}