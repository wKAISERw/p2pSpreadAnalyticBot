import logging
import asyncio
from typing import List, Tuple
from decimal import Decimal
from exchanges.base import BaseExchange, Order
from infrastructure.http.bybit_p2p_client import BybitP2PClient

logger = logging.getLogger(__name__)


class BybitExchange(BaseExchange):
    def __init__(self, client: BybitP2PClient):
        self.client = client
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

        return Order(
            id=str(item.get("id", "")),
            price=Decimal(str(item.get("price", "0"))),
            available_amount=Decimal(str(item.get("lastQuantity", "0"))),
            min_limit=Decimal(str(item.get("minAmount", "0"))),
            max_limit=Decimal(str(item.get("maxAmount", "0"))),
            merchant_id=str(item.get("userId", "")),
            merchant_name=str(item.get("nickName", "Unknown")),
            month_order_count=int(item.get("recentOrderNum", 0)),
            finish_rate_pct=float(item.get("recentExecuteRate", 0.0)),
            exchange="Bybit",
            link=f"https://www.bybit.com/fiat/trade/otc/profile/{item.get('userId', '')}",
            bank_codes=parsed_banks,
            trade_terms=str(item.get("remark", "") or "").strip().lower(),
            is_verified=bool(item.get("authTag") or item.get("isVerified")),
        )

    async def _fetch_orders(self, amount: float, banks: List[str], side: str) -> List[Order]:
        payload = {
            "userId": "",
            "tokenId": "USDT",
            "currencyId": "UAH",
            "payment": banks,
            "side": side,
            "size": "50",
            "page": "1",
            "amount": str(int(amount)),
            "authMaker": False,
            "canTrade": False
        }
        try:
            data = await self.client.fetch(self.url, payload)
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
        tasks = []
        for amount in amounts:
            tasks.append(self.get_buy_orders(amount, banks))
            tasks.append(self.get_sell_orders(amount, banks))

        results = await asyncio.gather(*tasks)

        raw_buys = []
        raw_sells = []
        for i in range(0, len(results), 2):
            raw_buys.extend(results[i])
            raw_sells.extend(results[i + 1])

        buy_orders = self.dedup(raw_buys)
        sell_orders = self.dedup(raw_sells)

        return buy_orders, sell_orders

        return result