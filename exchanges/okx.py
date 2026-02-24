import logging
import asyncio
import time
from typing import List, Tuple
from decimal import Decimal
from exchanges.base import BaseExchange, Order
from infrastructure.http.okx_client import OkxClient

logger = logging.getLogger(__name__)

# Маппінг для уніфікації банків
BANK_NAME_TO_CODE = {
    "Monobank": "43",
    "PrivatBank": "14",
    "PUMB": "64",
    "A-Bank": "48",
    "Sense SuperApp": "61"
}

BANK_CODE_TO_NAME = {v: k for k, v in BANK_NAME_TO_CODE.items()}


class OkxExchange(BaseExchange):
    def __init__(self, client: OkxClient):
        self.client = client
        # ВИПРАВЛЕНО: Використовуємо робочий URL для публічних даних
        self.url = "https://www.okx.com/v3/c2c/tradingOrders/getMarketplaceAdsPrelogin"

    def _parse_order(self, item: dict) -> Order:
        bank_codes = []
        # OKX може присилати або список рядків, або список об'єктів
        raw_methods = item.get("paymentMethods", [])

        for method in raw_methods:
            # Якщо це словник, беремо значення ключа 'bankName', інакше — сам метод як рядок
            name = method.get("bankName", "") if isinstance(method, dict) else str(method)

            code = BANK_NAME_TO_CODE.get(name)
            if code:
                bank_codes.append(code)

        # Додамо дебаг прямо сюди, щоб бачити, чи розпізнані банки
        if not bank_codes:
            logger.debug(f"⚠️ Не вдалося розпізнати банки в ордері: {raw_methods}")

        return Order(
            id=str(item.get("id", "")),
            price=Decimal(str(item.get("price", "0"))),
            available_amount=Decimal(str(item.get("availableAmount", "0"))),
            min_limit=Decimal(str(item.get("quoteMinAmountPerOrder", "0"))),
            max_limit=Decimal(str(item.get("quoteMaxAmountPerOrder", "0"))),
            merchant_id=item.get("publicUserId") or item.get("merchantId", ""),
            merchant_name=str(item.get("nickName", "Unknown")),
            month_order_count=int(item.get("completedOrderQuantity", 0)),
            finish_rate_pct=float(item.get("completedRate", "0")) * 100,
            exchange="OKX",
            link="https://www.okx.com/ua/p2p-markets/uah/buy-usdt",
            bank_codes=bank_codes,
        )

    async def _fetch_orders(self, amount: float, bank_code: str, side: str) -> List[Order]:
        bank_name = BANK_CODE_TO_NAME.get(bank_code, "all")

        # ВИПРАВЛЕНО: Параметри для цього ендпоїнта
        params = {
            "fiatCurrency": "UAH",
            "cryptoCurrency": "USDT",
            "paymentMethod": bank_name,
            "side": side,
            "userType": "all",
            "sortType": "price_asc" if side == "sell" else "price_desc",
            "numberPerPage": "20",
            "t": str(int(time.time() * 1000)),
        }

        try:
            data = await self.client.fetch(self.url, params, method="GET")
            if not isinstance(data, dict) or data.get("code") != 0:
                return []

            items = data.get("data", {}).get(side, [])
            # У файл exchanges/okx.py всередину методу _fetch_orders перед рядком 'return orders'
            #logger.info(f"DEBUG OKX: Отримано {len(items)} сирих ордерів для {side} через {bank_name}")
            return [self._parse_order(item) for item in items]
        except Exception as e:
            logger.error("❌ OKX Fetch Error: %s", e)
            return []

    async def get_buy_orders(self, amount: float, banks: List[str]) -> List[Order]:
        tasks = [self._fetch_orders(amount, b, "sell") for b in banks]
        results = await asyncio.gather(*tasks)
        return self._dedup_by_id([o for res in results for o in res])

    async def get_sell_orders(self, amount: float, banks: List[str]) -> List[Order]:
        tasks = [self._fetch_orders(amount, b, "buy") for b in banks]
        results = await asyncio.gather(*tasks)
        return self._dedup_by_id([o for res in results for o in res])

    async def fetch_both_multi(self, amounts: list[float], banks: list[str]) -> Tuple[List[Order], List[Order]]:
        # Беремо найбільшу суму для запиту, щоб бачити мерчантів із високим порогом
        max_amount = max(amounts) if amounts else 1000.0
        buys = await self.get_buy_orders(max_amount, banks)
        sells = await self.get_sell_orders(max_amount, banks)
        return buys, sells

    def _dedup_by_id(self, orders: List[Order]) -> List[Order]:
        seen, res = set(), []
        for o in orders:
            if o.id not in seen:
                seen.add(o.id);
                res.append(o)
        return res