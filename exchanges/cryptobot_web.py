# exchanges/cryptobot_web.py
import logging
import asyncio
from typing import List, Tuple
from decimal import Decimal
from datetime import datetime, timezone

from exchanges.base import BaseExchange, Order
from infrastructure.http.cryptobot_client import CryptoBotWebClient
from config.banks import BankRegistry

logger = logging.getLogger(__name__)


class CryptoBotWebExchange(BaseExchange):
    def __init__(self, client: CryptoBotWebClient):
        self.client = client

    def _parse_order(self, item: dict) -> Order:
        user_dict = item.get("user", {})
        raw_payment_methods = item.get("payment", {}).get("maker_accepts", [])
        
        bank_codes = []
        for p in raw_payment_methods:
            identifier = p.get("identifier")
            if identifier:
                # Мапимо "monobank" -> "choose-method-monobank"
                cb_code = f"choose-method-{identifier}"
                code = BankRegistry.from_api_code(cb_code, "CryptoBot")
                if code:
                    bank_codes.append(code)

        price = Decimal(str(item.get("price", "0")))
        limits_dict = item.get("limits", {})
        min_limit = Decimal(str(limits_dict.get("min", "0")))
        max_limit = Decimal(str(limits_dict.get("max", "0")))
        
        # Обчислюємо обсяг у валюті активу
        available = max_limit / price if price > 0 else Decimal("0")
        
        finish_rate = float(user_dict.get("orders_percent", 0))
        is_online = bool(user_dict.get("is_online"))
        last_online_mins = 0 if is_online else 5
        is_verified = bool(user_dict.get("is_verified_merchant", False))
        positive_rate = float(user_dict.get("rating", 0)) / 100.0

        return Order(
            id=str(item.get("id", "")),
            price=price,
            available_amount=available,
            min_limit=min_limit,
            max_limit=max_limit,
            merchant_id=str(user_dict.get("id", "")),
            merchant_name=str(user_dict.get("name", "Unknown")),
            month_order_count=int(user_dict.get("orders", 0)),
            finish_rate_pct=finish_rate,
            exchange="CryptoBot",
            link=f"https://t.me/CryptoBot?start=u-{user_dict.get('id', '')}",
            bank_codes=bank_codes,
            trade_terms="",
            last_online_mins=last_online_mins,
            account_age_days=0,
            is_verified=is_verified,
            positive_rate=positive_rate,
        )

    async def _fetch_orders(self, side: str, banks: List[str]) -> List[Order]:
        data = await self.client.fetch_offers(fiat="UAH", asset="USDT", side=side, count=50)
        items = data.get("offers", [])
        orders = []
        for item in items:
            try:
                order = self._parse_order(item)
                # Фільтруємо за банками — тільки ті, що нам підходять
                if not banks or any(b.lower() in [pb.lower() for pb in order.bank_codes] for b in banks):
                    orders.append(order)
            except Exception as e:
                logger.warning("Не вдалося розпарсити CryptoBot ордер: %s", e)

        # 🚀 Сортуємо ВІДФІЛЬТРОВАНІ ордери за найкращим курсом.
        # Купуємо ми (side="SELL" від мерчанта) → шукаємо найдешевший.
        # Продаємо ми (side="BUY" від мерчанта) → шукаємо найдорожчий.
        if side == "SELL":
            orders.sort(key=lambda o: o.price)
        else:
            orders.sort(key=lambda o: o.price, reverse=True)

        # Ліміт — захист від сплеску запитів, якщо після фільтра лишилось забагато.
        # Якщо всі підходять і їх менше ліміту — перевіряємо тільки найкращі.
        MAX_DETAIL_FETCH = 8
        top_orders = orders[:MAX_DETAIL_FETCH]

        if top_orders:
            details_tasks = [self.client.fetch_offer_details(int(o.id)) for o in top_orders]
            profiles_tasks = [self.client.fetch_user_profile(int(o.merchant_id)) for o in top_orders]

            results = await asyncio.gather(*(details_tasks + profiles_tasks), return_exceptions=True)

            details = results[:len(top_orders)]
            profiles = results[len(top_orders):]

            for i, order in enumerate(top_orders):
                detail = details[i]
                if isinstance(detail, Exception):
                    logger.debug("Не вдалося отримати умови оффера %s: %s", order.id, detail)
                    order.trade_terms = "не вдалося отримати доступ до умов через технічну помилку сесії"
                elif detail:
                    order.trade_terms = str(detail.get("description", "") or "").strip().lower()

                profile = profiles[i]
                if isinstance(profile, Exception):
                    logger.debug("Не вдалося отримати профіль користувача %s: %s", order.merchant_id, profile)
                elif profile:
                    created_at_str = profile.get("created_at")
                    if created_at_str:
                        try:
                            created_at = datetime.fromisoformat(created_at_str.replace("Z", "+00:00"))
                            now = datetime.now(timezone.utc)
                            order.account_age_days = max(0, (now - created_at).days)
                        except Exception as ex:
                            logger.warning("Помилка парсингу created_at %s для %s: %s", created_at_str,
                                           order.merchant_id, ex)

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

        # Якщо обидва впали — прокидаємо помилку до CircuitBreaker
        if isinstance(results[0], Exception) and isinstance(results[1], Exception):
            raise results[0]
        # Якщо один впав — логуємо, але повертаємо те що є
        for r in results:
            if isinstance(r, Exception):
                logger.warning("CryptoBot partial failure: %s", r)

        return self.dedup(buy_orders), self.dedup(sell_orders)

