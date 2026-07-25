# infrastructure/api/mexc_account.py
"""
MEXC офіційний API — акаунт.
Документація: https://mexcdevelop.github.io/apidocs/spot_v3_en/

Відповідальність: баланс Spot акаунта.
НЕ відповідає за: сканування P2P → infrastructure/http/mexc_client.py

Примітка: MEXC P2P API окремо не задокументований публічно.
Поки реалізуємо тільки баланс.
"""
from __future__ import annotations
import hashlib, hmac, logging, time, urllib.parse
from typing import Optional
from infrastructure.http.base_client import BaseHttpClient

logger = logging.getLogger("MEXCAccount")
BASE_URL = "https://api.mexc.com"

class MEXCAccountClient(BaseHttpClient):
    def __init__(self, api_key: str = "", api_secret: str = "", proxy: Optional[str] = None):
        super().__init__(proxy=proxy, extra_headers={
            "content-type": "application/json",
            "X-MEXC-APIKEY": api_key,
        })
        self._api_key = api_key
        self._api_secret = api_secret

    def set_credentials(self, api_key: str, api_secret: str) -> None:
        self._api_key = api_key
        self._api_secret = api_secret
        self._session.headers.update({"X-MEXC-APIKEY": api_key}) if self._session else None

    @property
    def is_authenticated(self) -> bool:
        return bool(self._api_key and self._api_secret)

    def _sign(self, params: dict) -> dict:
        params["timestamp"] = int(time.time() * 1000)
        qs = urllib.parse.urlencode(sorted(params.items()))
        params["signature"] = hmac.new(self._api_secret.encode(), qs.encode(), hashlib.sha256).hexdigest()
        return params

    async def get_balance(self) -> list[dict]:
        """Баланс Spot акаунта."""
        if not self.is_authenticated: return []
        headers = {"X-MEXC-APIKEY": self._api_key}
        try:
            data = await self._get(
                f"{BASE_URL}/api/v3/account",
                params=self._sign({}),
                headers=headers,
            )
            return [{"coin": b["asset"], "free": float(b["free"]),
                     "locked": float(b["locked"]),
                     "total": float(b["free"]) + float(b["locked"])}
                    for b in data.get("balances", [])
                    if float(b.get("free", 0)) + float(b.get("locked", 0)) > 0]
        except Exception as e:
            logger.warning("get_balance: %s", e); return []

    async def get_account_info(self) -> dict:
        """Базова інформація акаунта."""
        if not self.is_authenticated: return {}
        headers = {"X-MEXC-APIKEY": self._api_key}
        try:
            data = await self._get(f"{BASE_URL}/api/v3/account", params=self._sign({}), headers=headers)
            return {"accountType": data.get("accountType"), "canTrade": data.get("canTrade")}
        except Exception as e:
            logger.warning("get_account_info: %s", e); return {}