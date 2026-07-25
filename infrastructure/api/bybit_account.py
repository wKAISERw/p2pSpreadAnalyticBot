# infrastructure/api/bybit_account.py
"""
Bybit V5 офіційний API — акаунт і торгівля.
Документація: https://bybit-exchange.github.io/docs/v5/intro

Відповідальність:
  - Баланс (Unified + Funding)
  - Свої активні P2P оголошення
  - Історія P2P угод

НЕ відповідає за: сканування ринку → infrastructure/http/bybit_p2p_client.py
"""
from __future__ import annotations
import hashlib, hmac, logging, time
from typing import Any, Optional
from infrastructure.http.base_client import BaseHttpClient

logger = logging.getLogger("BybitAccount")
BASE_URL = "https://api.bybit.com"
RECV_WINDOW = "5000"

class BybitAccountClient(BaseHttpClient):
    def __init__(self, api_key: str = "", api_secret: str = "", proxy: Optional[str] = None):
        super().__init__(proxy=proxy, extra_headers={"content-type": "application/json"})
        self._api_key = api_key
        self._api_secret = api_secret

    def set_credentials(self, api_key: str, api_secret: str) -> None:
        self._api_key = api_key
        self._api_secret = api_secret

    @property
    def is_authenticated(self) -> bool:
        return bool(self._api_key and self._api_secret)

    def _sign_headers(self, query_string: str = "") -> dict:
        ts = str(int(time.time() * 1000))
        sign_str = ts + self._api_key + RECV_WINDOW + (query_string or "")
        signature = hmac.new(self._api_secret.encode(), sign_str.encode(), hashlib.sha256).hexdigest()
        return {
            "X-BAPI-API-KEY": self._api_key,
            "X-BAPI-TIMESTAMP": ts,
            "X-BAPI-SIGN": signature,
            "X-BAPI-RECV-WINDOW": RECV_WINDOW,
        }

    async def get_balance(self, account_type: str = "ALL") -> list[dict]:
        """Баланс по монетах. account_type: UNIFIED | FUND | SPOT | ALL"""
        if not self.is_authenticated: return []
        if account_type == "ALL":
            unified = await self.get_balance("UNIFIED")
            funding = await self.get_balance("FUND")
            merged = {}
            for coin_dict in unified + funding:
                coin = coin_dict["coin"]
                if coin not in merged:
                    merged[coin] = {
                        "coin": coin,
                        "free": 0.0,
                        "locked": 0.0,
                        "total": 0.0,
                        "usd_value": 0.0
                    }
                merged[coin]["free"] += coin_dict["free"]
                merged[coin]["locked"] += coin_dict["locked"]
                merged[coin]["total"] += coin_dict["total"]
                merged[coin]["usd_value"] += coin_dict.get("usd_value", 0.0)
            return list(merged.values())

        query = f"accountType={account_type}"
        try:
            data = await self._get(f"{BASE_URL}/v5/account/wallet-balance?{query}", headers=self._sign_headers(query))
            coins = data.get("result", {}).get("list", [{}])[0].get("coin", [])
            return [{"coin": c["coin"], "free": float(c.get("availableToWithdraw") or 0),
                     "locked": float(c.get("locked") or 0), "total": float(c.get("walletBalance") or 0),
                     "usd_value": float(c.get("usdValue") or 0)}
                    for c in coins if float(c.get("walletBalance") or 0) > 0]
        except Exception as e:
            logger.warning("get_balance: %s", e); return []

    async def get_funding_balance(self) -> list[dict]:
        """Баланс Funding акаунта (для P2P)."""
        return await self.get_balance("FUND")

    async def get_my_ads(self, status: str = "ONLINE") -> list[dict]:
        """Заглушка: Bybit не віддає P2P дані через звичайний API (повертає 404)"""
        return []

    async def get_my_orders(self, status: str = "TRADING", limit: int = 20) -> list[dict]:
        """Заглушка: Bybit не віддає P2P дані через звичайний API"""
        return []

    async def get_account_info(self) -> dict:
        """Базова інформація акаунта (UID, рівень, статус верифікації)."""
        if not self.is_authenticated: return {}
        try:
            data = await self._get(f"{BASE_URL}/v5/user/query-api", headers=self._sign_headers())
            return data.get("result", {}) or {}
        except Exception as e:
            logger.warning("get_account_info: %s", e); return {}