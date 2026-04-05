# exchanges/binance.py
import asyncio
import logging
from decimal import Decimal
from typing import List, Tuple

from exchanges.base import BaseExchange, Order
from infrastructure.http.binance_client import BinanceClient
from config.banks import BankRegistry

logger = logging.getLogger(__name__)


class BinanceExchange(BaseExchange):
    def __init__(self, client: BinanceClient):
        self.client = client

    def _parse_order(self, item: dict, bank_code: str) -> Order:
        adv = item.get("adv", {})
        user = item.get("advertiser", {})

        trade_methods = adv.get("tradeMethods", [])
        bank_codes = []
        for m in trade_methods:
            identifier = m.get("identifier", "")
            code = BankRegistry.from_api_code(identifier, "Binance")
            if code:
                bank_codes.append(code)

        finish_rate = float(user.get("monthFinishRate", 0)) * 100
        order_count = int(user.get("monthOrderCount", 0))
        # positiveRate — це % позитивних ВІДГУКІВ (не completion rate!)
        positive_rate = float(user.get("positiveRate", 0) or 0)

        return Order(
            id=f"bn_{adv.get('advNo', '')}",
            price=Decimal(str(adv.get("price", "0"))),
            available_amount=Decimal(str(adv.get("surplusAmount", "0"))),
            min_limit=Decimal(str(adv.get("minSingleTransAmount", "0"))),
            max_limit=Decimal(str(adv.get("maxSingleTransAmount", "0"))),
            merchant_id=str(user.get("userNo", "")),
            merchant_name=str(user.get("nickName", "Unknown")),
            month_order_count=order_count,
            finish_rate_pct=round(finish_rate, 1),
            positive_rate=positive_rate,
            exchange="Binance",
            link=f"https://c2c.binance.com/uk-UA/advertiserDetail?advertiserNo={user.get('userNo', '')}",
            bank_codes=bank_codes if bank_codes else [bank_code],
            trade_terms=str(adv.get("remarks", "") or "").strip().lower(),
            is_verified=str(user.get("userType", "")) == "merchant",
        )

    async def _fetch_orders(self, side: str, banks: List[str]) -> List[Order]:
        orders: List[Order] = []
        sem = asyncio.Semaphore(2)

        async def fetch_one(bank_code: str) -> List[Order]:
            binance_pay = BankRegistry.get_exchange_code(bank_code, "Binance")
            if not binance_pay:
                return []

            payload = {
                "asset": "USDT",
                "fiat": "UAH",
                "merchantCheck": False,
                "page": 1,
                "payTypes": [binance_pay],
                "publisherType": None,
                "rows": 20,
                "side": side,
                "tradeType": side,
            }

            async with sem:
                try:
                    result = await self.client.fetch(payload)
                    parsed: List[Order] = []

                    if isinstance(result, dict) and "data" in result:
                        for item in result.get("data", []):
                            try:
                                parsed.append(self._parse_order(item, bank_code))
                            except Exception as e:
                                logger.warning("Binance parse помилка: %s", e)
                    else:
                        logger.error("Binance fetch помилка [%s]: невірний формат", bank_code)

                    await asyncio.sleep(0.15)
                    return parsed

                except Exception as e:
                    logger.error("Помилка запиту Binance [%s]: %s", bank_code, e)
                    await asyncio.sleep(0.25)
                    return []

        chunks = await asyncio.gather(*(fetch_one(bank) for bank in banks), return_exceptions=True)

        for chunk in chunks:
            if isinstance(chunk, Exception):
                logger.error("Binance gather error: %s", chunk)
                continue
            orders.extend(chunk)

        return orders

    async def get_buy_orders(self, amount: float, banks: List[str]) -> List[Order]:
        return await self._fetch_orders("BUY", banks)

    async def get_sell_orders(self, amount: float, banks: List[str]) -> List[Order]:
        return await self._fetch_orders("SELL", banks)

    async def fetch_both_multi(self, amounts: List[float], banks: List[str]) -> Tuple[List[Order], List[Order]]:
        buy_orders, sell_orders = await asyncio.gather(
            self.get_buy_orders(0, banks),
            self.get_sell_orders(0, banks),
        )
        return self.dedup(buy_orders), self.dedup(sell_orders)
