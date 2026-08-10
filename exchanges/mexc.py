# exchanges/mexc.py
import asyncio
import logging
import time
from decimal import Decimal
from typing import List, Tuple

from core.engine import terms_status
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

    def _parse_order(self, item: dict, bank_code: str = "", side: str = "") -> Order:
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

        last_online = merchant.get("lastOnlineTime")
        if last_online is not None:
            try:
                last_online_mins = max(0, int((time.time() * 1000 - float(last_online)) // 60000))
            except (ValueError, TypeError):
                last_online_mins = None
        else:
            last_online_mins = None

        raw_methods = item.get("payMethod", "") or ""
        bank_codes = []
        if raw_methods:
            method_ids = [m.strip() for m in str(raw_methods).split(",") if m.strip()]
            for m_id in method_ids:
                code = BankRegistry.from_api_code(m_id, "MEXC")
                if code:
                    bank_codes.append(code)

        if bank_code and bank_code not in bank_codes:
            internal_code = BankRegistry.from_api_code(bank_code, "MEXC") or bank_code
            if internal_code and internal_code not in bank_codes:
                bank_codes.append(internal_code)

        terms_text, terms_state = terms_status.from_payload(
            item, "tradeTerms", "remark")

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
            bank_codes=bank_codes,
            trade_terms=terms_text,
            terms_status=terms_state,
            is_verified=bool(merchant.get("isCertified") or merchant.get("isVerified")),
            last_online_mins=last_online_mins,
        )

    async def _fetch_orders(self, side: int, side_str: str, banks: List[str]) -> List[Order]:
        payload = self._build_payload(side, "")
        try:
            result = await self.client.fetch(payload)
            items = result.get("data") or []
        except Exception as e:
            logger.error("MEXC fetch error: %s", e)
            return []

        orders = []
        target_banks = set(banks)
        for item in items:
            try:
                order = self._parse_order(item, side=side_str)
                if any(b in target_banks for b in order.bank_codes):
                    orders.append(order)
            except Exception as e:
                logger.warning("MEXC parse error: %s", e)

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

