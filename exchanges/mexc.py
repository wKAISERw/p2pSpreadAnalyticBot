# exchanges/mexc.py
import asyncio
import logging
from decimal import Decimal
from typing import List, Tuple

from exchanges.base import BaseExchange, Order
from infrastructure.http.mexc_client import MexcClient
from config.banks import BankRegistry

logger = logging.getLogger(__name__)


class MexcExchange(BaseExchange):
    def __init__(self, client: MexcClient):
        self.client = client

    def _build_payload(self, side: int, payment_method: str, page: int = 1) -> dict:
        trade_str = "SELL" if side == 1 else "BUY"
        return {
            "adsType": "1",
            "allowTrade": "false",
            "amount": "",
            "blockTrade": "false",
            "certifiedMerchant": "false",
            "coinId": "128f589271cb4951b03e71e6323eb7be",
            "countryCode": "",
            "currency": "UAH",
            "follow": "false",
            "haveTrade": "false",
            "page": str(page),
            "payMethod": payment_method,
            "tradeType": trade_str
        }

    def _parse_order(self, item: dict, bank_code: str, side: str) -> Order:
        merchant = item.get("merchant", {})
        stats = item.get("merchantStatistics", {})

        adv_no = str(item.get("id", ""))
        price = Decimal(str(item.get("price", "0")))
        available = Decimal(str(item.get("availableQuantity", "0")))
        min_limit = Decimal(str(item.get("minTradeLimit", "0")))
        max_limit = Decimal(str(item.get("maxTradeLimit", "0")))
        nickname = str(merchant.get("nickName", "Unknown"))
        user_id = str(merchant.get("memberId", ""))
        order_count = int(stats.get("doneLastMonthCount", 0))

        raw_rate = stats.get("lastMonthCompleteRate", "0")
        try:
            finish_rate = float(raw_rate) * 100
        except ValueError:
            finish_rate = 0.0

        return Order(
            id=f"mx_{adv_no}",
            price=price,
            available_amount=available,
            min_limit=min_limit,
            max_limit=max_limit,
            merchant_id=user_id,
            merchant_name=nickname,
            month_order_count=order_count,
            finish_rate_pct=round(finish_rate, 1),
            exchange="MEXC",
            link=f"https://www.mexc.com/uk-UA/buy-crypto/merchant?id={user_id}",
            bank_codes=[bank_code],
            trade_terms=str(item.get("remark", "") or "").strip().lower(),
            is_verified=bool(merchant.get("isCertified") or merchant.get("isVerified")),
        )

    async def _fetch_orders(self, side: int, side_str: str, banks: List[str]) -> List[Order]:
        tasks = []
        valid_banks = []
        for bank_code in banks:
            mexc_pay = BankRegistry.get_exchange_code(bank_code, "MEXC")
            if not mexc_pay:
                continue
            tasks.append(self.client.fetch(self._build_payload(side, mexc_pay)))
            valid_banks.append(bank_code)

        if not tasks:
            return []

        results = await asyncio.gather(*tasks, return_exceptions=True)

        orders = []
        for bank_code, result in zip(valid_banks, results):
            if isinstance(result, Exception):
                logger.error("MEXC fetch [%s]: %s", bank_code, result)
                continue

            items = result.get("data") or []
            if not items:
                logger.debug("MEXC [%s/%s]: порожня відповідь", bank_code, side_str)
                continue

            for item in items:
                try:
                    orders.append(self._parse_order(item, bank_code, side_str))
                except Exception as e:
                    logger.warning("MEXC parse [%s]: %s", bank_code, e)

        return orders

    async def get_buy_orders(self, amount: float, banks: List[str]) -> List[Order]:
        return await self._fetch_orders(1, "buy", banks)

    async def get_sell_orders(self, amount: float, banks: List[str]) -> List[Order]:
        return await self._fetch_orders(2, "sell", banks)

    async def fetch_both_multi(self, amounts: List[float], banks: List[str]) -> Tuple[List[Order], List[Order]]:
        buy_orders, sell_orders = await asyncio.gather(
            self.get_buy_orders(0, banks),
            self.get_sell_orders(0, banks),
        )
        return self.dedup(buy_orders), self.dedup(sell_orders)

