# exchanges/binance.py
import asyncio
import logging
from decimal import Decimal
from typing import List, Tuple

from exchanges.base import BaseExchange, Order
from infrastructure.http.binance_client import BinanceClient
from config.banks import BankRegistry
from core.engine import bank_discovery

logger = logging.getLogger(__name__)


class BinanceExchange(BaseExchange):
    def __init__(self, client: BinanceClient, db = None):
        self.client = client
        self.db = db

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
            elif identifier:
                # Невідомий метод мовчки випадав зі списку банків ордера —
                # тобто ордер міг відпасти як «не той банк», хоч насправді
                # приймав саме наш. Тепер це видно в /checkup.
                bank_discovery.note("Binance", identifier, m)

        finish_rate = float(user.get("monthFinishRate", 0)) * 100
        order_count = int(user.get("monthOrderCount", 0))
        # positiveRate — це % позитивних ВІДГУКІВ (не completion rate!)
        positive_rate = float(user.get("positiveRate", 0) or 0)

        active_sec = user.get("activeTimeInSecond")
        last_online_mins = int(active_sec) // 60 if active_sec is not None else None

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
            last_online_mins=last_online_mins,
        )

    async def _fetch_orders(self, side: str, banks: List[str]) -> List[Order]:
        if not banks:
            return []

        binance_pays = []
        for b in banks:
            code = BankRegistry.get_exchange_code(b, "Binance")
            if code:
                binance_pays.append(code)

        if not binance_pays:
            return []

        payload = {
            "asset": "USDT",
            "fiat": "UAH",
            "merchantCheck": False,
            "page": 1,
            "payTypes": binance_pays,
            "publisherType": None,
            "rows": 20,
            "side": side,
            "tradeType": side,
        }

        headers = None
        cookies = None
        if self.db:
            try:
                headers, cookies, _ = await self.db.get_auth_session("Binance")
            except Exception as e:
                logger.debug("Failed to retrieve Binance session from DB: %s", e)

        try:
            result = await self.client.fetch(payload, headers=headers, cookies=cookies)
            parsed: List[Order] = []

            if isinstance(result, dict) and "data" in result:
                for item in result.get("data", []):
                    try:
                        # В _parse_order передаємо перший банк зі списку як fallback,
                        # проте основні банки розпарсяться з tradeMethods
                        parsed.append(self._parse_order(item, banks[0]))
                    except Exception as e:
                        logger.warning("Binance parse помилка: %s", e)
            else:
                logger.error("Binance fetch помилка: невірний формат")

            return parsed

        except Exception as e:
            logger.error("Помилка запиту Binance: %s", e)
            return []

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
