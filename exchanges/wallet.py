# exchanges/wallet.py
import logging
import asyncio
from typing import List, Tuple
from decimal import Decimal
from exchanges.base import BaseExchange, Order
from infrastructure.http.wallet_client import WalletClient

logger = logging.getLogger(__name__)

class WalletExchange(BaseExchange):
    def __init__(self, client: WalletClient):
        self.client = client
        # НОВИЙ ОФІЦІЙНИЙ URL
        self.url = "https://p2p.walletbot.me/p2p/integration-api/v1/item/online"

    def _parse_order(self, item: dict) -> Order:
        # Додаємо словник перекладу
        bank_map = {"monobank": "43", "privatbank": "14", "pumb": "64", "abank": "48"}

        raw_banks = item.get("payments", [])
        bank_codes = []
        for b in raw_banks:
            if b.lower() in bank_map:
                bank_codes.append(bank_map[b.lower()])

        finish_rate = float(item.get("executeRate", "0")) * 100

        return Order(
            id=str(item.get("id", "")),
            price=Decimal(str(item.get("price", "0"))),
            available_amount=Decimal(str(item.get("lastQuantity", "0"))),
            min_limit=Decimal(str(item.get("minAmount", "0"))),
            max_limit=Decimal(str(item.get("maxAmount", "0"))),
            merchant_id=str(item.get("userId", "")),
            merchant_name=str(item.get("nickname", "Unknown")),
            month_order_count=int(item.get("orderNum", 0)),
            finish_rate_pct=finish_rate,
            exchange="Wallet",
            link="https://t.me/wallet",
            bank_codes=bank_codes,
            trade_terms=str(
                item.get("comment", "") or
                item.get("notice", "") or
                item.get("description", "") or
                item.get("terms", "")
            ).strip().lower()
        )

    async def _fetch_orders(self, side: str, banks: List[str]) -> List[Order]:
        payload = {
            "cryptoCurrency": "USDT",
            "fiatCurrency": "UAH",
            "side": side,
            "page": 1,
            "pageSize": 50
        }

        try:
            data = await self.client.fetch(self.url, payload)

            if data.get("status") != "SUCCESS":
                logger.error("Wallet API error: %s", data)
                return []

            items = data.get("data", [])
            orders = []
            for item in items:
                try:
                    order = self._parse_order(item)
                    # Фільтруємо банки
                    if not banks or any(b.lower() in [pb.lower() for pb in order.bank_codes] for b in banks):
                        orders.append(order)
                except Exception as e:
                    logger.warning("Не вдалося розпарсити Wallet ордер: %s", e)
            return orders

        except Exception as e:
            logger.error("Помилка Wallet (side=%s): %s", side, e)
            return []

    async def get_buy_orders(self, amount: float, banks: List[str]) -> List[Order]:
        # "SELL" = мерчант продає USDT (а ми купуємо)
        return await self._fetch_orders("SELL", banks)

    async def get_sell_orders(self, amount: float, banks: List[str]) -> List[Order]:
        # "BUY" = мерчант купує USDT (а ми йому продаємо)
        return await self._fetch_orders("BUY", banks)

    async def fetch_both_multi(self, amounts: list[float], banks: list[str]) -> Tuple[List[Order], List[Order]]:
        buy_orders, sell_orders = await asyncio.gather(
            self.get_buy_orders(0, banks),
            self.get_sell_orders(0, banks),
        )
        return self._dedup_by_id(buy_orders), self._dedup_by_id(sell_orders)

    def _dedup_by_id(self, orders: List[Order]) -> List[Order]:
        seen, result = set(), []
        for order in orders:
            if order.id not in seen:
                seen.add(order.id)
                result.append(order)
        return result