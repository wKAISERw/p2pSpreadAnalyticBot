# exchanges/wallet.py
import logging
import asyncio
from typing import List, Tuple
from decimal import Decimal
from exchanges.base import BaseExchange, Order
from infrastructure.http.wallet_client import WalletClient
from config.banks import BankRegistry

logger = logging.getLogger(__name__)


class WalletExchange(BaseExchange):
    def __init__(self, client: WalletClient):
        self.client = client
        self.url = "https://p2p.walletbot.me/p2p/integration-api/v1/item/online"

    def _parse_order(self, item: dict) -> Order:
        raw_banks = item.get("payments", [])
        bank_codes = []
        for b in raw_banks:
            code = BankRegistry.from_api_code(b.lower(), "Wallet")
            if code:
                bank_codes.append(code)

        finish_rate = float(item.get("executeRate", "0")) * 100
        is_online = bool(item.get("isOnline"))
        last_online_mins = 0 if is_online else 5

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
            ).strip().lower(),
            last_online_mins=last_online_mins,
        )

    async def _fetch_orders(self, side: str, banks: List[str]) -> List[Order]:
        payload = {
            "cryptoCurrency": "USDT",
            "fiatCurrency": "UAH",
            "side": side,
            "page": 1,
            "pageSize": 50
        }

        # НЕ ловимо RuntimeError — нехай підніметься до CircuitBreaker
        data = await self.client.fetch(self.url, payload)

        if data.get("status") != "SUCCESS":
            logger.error("Wallet API error: %s", data)
            return []

        items = data.get("data", [])
        orders = []
        for item in items:
            try:
                order = self._parse_order(item)
                if not banks or any(b.lower() in [pb.lower() for pb in order.bank_codes] for b in banks):
                    orders.append(order)
            except Exception as e:
                logger.warning("Не вдалося розпарсити Wallet ордер: %s", e)

        # 🚀 Паралельно завантажуємо умови для топ-12 ордерів (найвигідніші спреди)
        top_orders = orders[:12]
        if top_orders:
            comments = await asyncio.gather(
                *[self.client.fetch_offer_comment(int(o.id)) for o in top_orders],
                return_exceptions=True
            )
            for order, comment in zip(top_orders, comments):
                if isinstance(comment, Exception):
                    logger.warning("⚠️ Не вдалося завантажити умови Wallet оффера %s через виняток: %s", order.id, comment)
                elif comment is None:
                    logger.warning("⚠️ Не вдалося достукатися до умов Wallet оффера %s (помилка авторизації або мережі)", order.id)
                else:
                    # Успішно отримали умови (можуть бути порожніми "" або містити текст)
                    order.trade_terms = comment.lower()

        return orders


    async def get_buy_orders(self, amount: float, banks: List[str]) -> List[Order]:
        return await self._fetch_orders("SELL", banks)

    async def get_sell_orders(self, amount: float, banks: List[str]) -> List[Order]:
        return await self._fetch_orders("BUY", banks)

    async def fetch_both_multi(self, amounts: list[float], banks: list[str]) -> Tuple[List[Order], List[Order]]:
        results = await asyncio.gather(
            self.get_buy_orders(0, banks),
            self.get_sell_orders(0, banks),
            return_exceptions=True,
        )
        buy_orders = results[0] if not isinstance(results[0], Exception) else []
        sell_orders = results[1] if not isinstance(results[1], Exception) else []

        # Якщо обидві сторони впали — прокидаємо помилку до CircuitBreaker
        if isinstance(results[0], Exception) and isinstance(results[1], Exception):
            raise results[0]
        # Якщо одна сторона впала — логуємо, але повертаємо те що є
        for r in results:
            if isinstance(r, Exception):
                logger.warning("Wallet partial failure: %s", r)

        return self.dedup(buy_orders), self.dedup(sell_orders)
