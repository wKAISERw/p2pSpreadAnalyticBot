# infrastructure/api/okx_account.py
"""
OKX офіційний API — акаунт.
Документація: https://www.okx.com/docs-v5/en/

Відповідальність: баланс, P2P угоди.
НЕ відповідає за: сканування ринку → infrastructure/http/okx_client.py

ВАЖЛИВО: OKX вимагає passphrase при створенні API ключа.
"""
from __future__ import annotations
import base64, hashlib, hmac, json, logging
from datetime import datetime, timezone
from typing import Optional
from infrastructure.http.base_client import BaseHttpClient

logger = logging.getLogger("OKXAccount")
BASE_URL = "https://www.okx.com"

class OKXAccountClient(BaseHttpClient):
    def __init__(self, api_key: str = "", api_secret: str = "", passphrase: str = "", proxy: Optional[str] = None):
        super().__init__(proxy=proxy, extra_headers={"content-type": "application/json"})
        self._api_key = api_key
        self._api_secret = api_secret
        self._passphrase = passphrase

    def set_credentials(self, api_key: str, api_secret: str, passphrase: str = "") -> None:
        self._api_key = api_key
        self._api_secret = api_secret
        self._passphrase = passphrase

    @property
    def is_authenticated(self) -> bool:
        return bool(self._api_key and self._api_secret and self._passphrase)

    def _sign_headers(self, method: str, path: str, body: str = "") -> dict:
        ts = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"
        msg = ts + method.upper() + path + (body or "")
        sig = base64.b64encode(hmac.new(self._api_secret.encode(), msg.encode(), hashlib.sha256).digest()).decode()
        return {
            "OK-ACCESS-KEY": self._api_key, "OK-ACCESS-SIGN": sig,
            "OK-ACCESS-TIMESTAMP": ts, "OK-ACCESS-PASSPHRASE": self._passphrase,
        }

    async def get_balance(self, currencies: str = "USDT,UAH") -> list[dict]:
        """Баланс Trading акаунта."""
        if not self.is_authenticated: return []
        path = f"/api/v5/account/balance?ccy={currencies}"
        try:
            data = await self._get(f"{BASE_URL}{path}", headers=self._sign_headers("GET", path))
            details = data.get("data", [{}])[0].get("details", [])
            return [{"coin": d["ccy"], "free": float(d.get("availBal") or 0),
                     "locked": float(d.get("frozenBal") or 0),
                     "total": float(d.get("bal") or 0)}
                    for d in details if float(d.get("bal") or 0) > 0]
        except Exception as e:
            logger.warning("get_balance: %s", e); return []

    async def get_funding_balance(self, currencies: str = "USDT,UAH") -> list[dict]:
        """Баланс Funding акаунта (для P2P виводів)."""
        if not self.is_authenticated: return []
        path = f"/api/v5/asset/balances?ccy={currencies}"
        try:
            data = await self._get(f"{BASE_URL}{path}", headers=self._sign_headers("GET", path))
            return [{"coin": d["ccy"], "free": float(d.get("availBal") or 0),
                     "locked": float(d.get("frozenBal") or 0),
                     "total": float(d.get("bal") or 0)}
                    for d in data.get("data", []) if float(d.get("bal") or 0) > 0]
        except Exception as e:
            logger.warning("get_funding_balance: %s", e); return []

    async def get_my_p2p_orders(self, state: str = "ongoing", limit: int = 20) -> list[dict]:
        """
        Мої P2P угоди.
        state: ongoing | end | all
        """
        if not self.is_authenticated: return []
        path = f"/api/v5/c2c/order/list?state={state}&limit={limit}"
        try:
            data = await self._get(f"{BASE_URL}{path}", headers=self._sign_headers("GET", path))
            return data.get("data", []) or []
        except Exception as e:
            logger.warning("get_my_p2p_orders: %s", e); return []

    async def get_account_info(self) -> dict:
        """Інформація акаунта (UID, рівень)."""
        if not self.is_authenticated: return {}
        path = "/api/v5/account/config"
        try:
            data = await self._get(f"{BASE_URL}{path}", headers=self._sign_headers("GET", path))
            cfg = data.get("data", [{}])[0]
            return {"uid": cfg.get("uid"), "acctLv": cfg.get("acctLv")}
        except Exception as e:
            logger.warning("get_account_info: %s", e); return {}