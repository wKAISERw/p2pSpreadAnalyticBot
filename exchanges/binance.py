# exchanges/binance.py
import asyncio
import logging
from decimal import Decimal
from typing import List, Tuple

from exchanges.base import BaseExchange, Order
from infrastructure.http.binance_client import BinanceClient

logger = logging.getLogger(__name__)

BANK_CODE_TO_BINANCE = {
    "43": "Monobank",
    "14": "PrivatBank",
    "64": "PUMB",
    "48": "A-Bank",
    "99": "Oschadbank",
    "380": "RaiffeisenBankUkraine",
    "328": "SenseBank",
}

BINANCE_TO_CODE = {v: k for k, v in BANK_CODE_TO_BINANCE.items()}


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
            code = BINANCE_TO_CODE.get(identifier)
            if code:
                bank_codes.append(code)

        finish_rate = float(user.get("monthFinishRate", 0)) * 100
        order_count = int(user.get("monthOrderCount", 0))

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
            exchange="Binance",
            link=f"https://p2p.binance.com/en/advertiserDetail?advertiserNo={user.get('userNo', '')}",
            bank_codes=bank_codes if bank_codes else [bank_code],
            trade_terms=str(adv.get("remarks", "") or "").strip().lower(),
            is_verified=str(user.get("userType", "")) == "merchant",
        )

    async def _fetch_orders(self, side: str, banks: List[str]) -> List[Order]:
        orders = []
        # ЗАМІСТЬ GATHER РОБИМО ПОСЛІДОВНІ ЗАПИТИ ІЗ ЗАТРИМКОЮ
        for bank_code in banks:
            binance_pay = BANK_CODE_TO_BINANCE.get(bank_code)
            if not binance_pay:
                continue
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
            try:
                result = await self.client.fetch(payload)
                if isinstance(result, dict) and "data" in result:
                    for item in result.get("data", []):
                        try:
                            orders.append(self._parse_order(item, bank_code))
                        except Exception as e:
                            logger.warning("Binance parse помилка: %s", e)
                else:
                    logger.error("Binance fetch помилка [%s]: невірний формат", bank_code)
            except Exception as e:
                logger.error("Помилка запиту Binance [%s]: %s", bank_code, e)

            # 🛡 МІКРОПАУЗА ПРОТИ БАНУ 429
            await asyncio.sleep(0.35)

        return orders

    async def get_buy_orders(self, amount: float, banks: List[str]) -> List[Order]:
        return await self._fetch_orders("BUY", banks)

    async def get_sell_orders(self, amount: float, banks: List[str]) -> List[Order]:
        return await self._fetch_orders("SELL", banks)

    async def fetch_both_multi(
            self, amounts: List[float], banks: List[str]
    ) -> Tuple[List[Order], List[Order]]:
        # ПОСЛІДОВНО: Спочатку всі на купівлю, потім всі на продаж (без gather)
        buy_orders = await self.get_buy_orders(0, banks)
        sell_orders = await self.get_sell_orders(0, banks)

        return self._dedup(buy_orders), self._dedup(sell_orders)

    def _dedup(self, orders: List[Order]) -> List[Order]:
        seen, result = set(), []
        for o in orders:
            if o.id not in seen:
                seen.add(o.id)
                result.append(o)
        return result