import logging
import asyncio
import time
from typing import List, Tuple
from decimal import Decimal
from exchanges.base import BaseExchange, Order
from infrastructure.http.bybit_p2p_client import BybitP2PClient

logger = logging.getLogger(__name__)


class BybitExchange(BaseExchange):
    def __init__(self, client: BybitP2PClient, db = None):
        self.client = client
        self.db = db
        self.url = "https://api2.bybit.com/fiat/otc/item/online"

    def _parse_order(self, item: dict) -> Order:
        """Перетворює сирий JSON Bybit на строгу модель Order з точною математикою (Decimal)."""
        raw_payments = item.get("payments", [])
        parsed_banks = []
        for p in raw_payments:
            if isinstance(p, dict):
                parsed_banks.append(str(p.get("paymentType", "")))
            else:
                parsed_banks.append(str(p))

        is_online = bool(item.get("isOnline"))
        last_online_mins = 0
        if not is_online:
            last_logout = item.get("lastLogoutTime")
            if last_logout:
                try:
                    last_online_mins = max(0, (int(time.time()) - int(last_logout)) // 60)
                except (ValueError, TypeError):
                    last_online_mins = None
            else:
                last_online_mins = None
        raw_pid = str(item.get("userMaskId") or item.get("userId") or "").strip()
        profile_id = raw_pid if raw_pid.startswith("s") else (f"s{raw_pid}" if raw_pid else "")

        return Order(
            id=str(item.get("id", "")),
            price=Decimal(str(item.get("price", "0"))),
            available_amount=Decimal(str(item.get("lastQuantity", "0"))),
            min_limit=Decimal(str(item.get("minAmount", "0"))),
            max_limit=Decimal(str(item.get("maxAmount", "0"))),
            merchant_id=profile_id,
            merchant_name=str(item.get("nickName", "Unknown")),
            month_order_count=int(item.get("recentOrderNum", 0)),
            finish_rate_pct=float(item.get("recentExecuteRate", 0.0)),
            exchange="Bybit",
            link=f"https://www.bybit.com/uk-UA/p2p/profile/{profile_id}/USDT/UAH/item" if profile_id else "",
            bank_codes=parsed_banks,
            trade_terms=str(item.get("remark", "") or "").strip().lower(),
            is_verified=bool(item.get("authTag") or item.get("isVerified")),
            last_online_mins=last_online_mins,
        )

    async def _fetch_orders(self, amount: float, banks: List[str], side: str) -> List[Order]:
        headers = None
        cookies = None
        can_trade = False

        if self.db:
            try:
                headers, cookies, _ = await self.db.get_auth_session("Bybit")
                if headers or cookies:
                    can_trade = True
            except Exception as e:
                logger.debug("Failed to retrieve Bybit session from DB: %s", e)

        payload = {
            "userId": "",
            "tokenId": "USDT",
            "currencyId": "UAH",
            "payment": banks,
            "side": side,
            "size": "50",
            "page": "1",
            "amount": str(int(amount)) if amount > 0 else "",
            "authMaker": False,
            "canTrade": can_trade
        }
        try:
            data = await self.client.fetch(self.url, payload, headers=headers, cookies=cookies)
            items = data.get("result", {}).get("items", [])
            return [self._parse_order(item) for item in items]
        except Exception as e:
            logger.error("❌ Помилка парсингу Bybit (side=%s): %s", side, e)
            return []

    async def get_buy_orders(self, amount: float, banks: List[str]) -> List[Order]:
        return await self._fetch_orders(amount, banks, side="1")

    async def get_sell_orders(self, amount: float, banks: List[str]) -> List[Order]:
        return await self._fetch_orders(amount, banks, side="0")

    async def fetch_both_multi(self, amounts: list[float], banks: list[str]) -> Tuple[List[Order], List[Order]]:
        # Отримуємо топ-50 ордерів у стакані без фільтру суми (мінімізація API викликів)
        buy_orders, sell_orders = await asyncio.gather(
            self.get_buy_orders(0, banks),
            self.get_sell_orders(0, banks)
        )
        return self.dedup(buy_orders), self.dedup(sell_orders)

