# exchanges/bingx.py
import asyncio
import logging
from decimal import Decimal
from typing import List, Tuple, Optional

from core.engine import terms_status
from exchanges.base import BaseExchange, Order
from infrastructure.http.bingx_client import BingxClient
from config.banks import BankRegistry

logger = logging.getLogger("BingxExchange")

class BingxExchange(BaseExchange):
    def __init__(self, client: BingxClient):
        self.client = client
        self._cached_buy = {}
        self._cached_sell = {}
        self._bg_task = None
        self._last_fetch_time = 0.0

    def start_bg_task(self):
        if not self._bg_task or self._bg_task.done():
            self._bg_task = asyncio.create_task(self._bg_loop())

    async def _bg_loop(self):
        logger.info("BingX background fetch loop started.")
        while True:
            try:
                # Stop if browser has been closed/deallocated (only for real Playwright clients)
                if hasattr(self.client, "_browser") and not self.client._browser:
                    logger.info("BingX browser is closed, stopping background loop.")
                    break
                
                buy_raw, sell_raw = await self.client.fetch_both()
                if buy_raw and sell_raw:
                    self._cached_buy = buy_raw
                    self._cached_sell = sell_raw
                    self._last_fetch_time = asyncio.get_event_loop().time()
                    logger.debug("BingX background fetch succeeded.")
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error("Error in BingX background fetch: %s", e)
            await asyncio.sleep(2.0)

    def _parse_order(self, item: dict, bank_codes: List[str]) -> Order:
        merchant_info = item.get("merchantInfo", {})
        merchant_stat = item.get("merchantStat", {})

        adv_no = str(item.get("advertNo", ""))
        price = Decimal(str(item.get("price", "0")))
        subsidized_info = item.get("subsidizedInfo", {})
        if subsidized_info and subsidized_info.get("isSubsidized"):
            sub_price = subsidized_info.get("subsidizedPrice")
            if sub_price:
                price = Decimal(str(sub_price))
        available = Decimal(str(item.get("availableNumber", "0")))
        min_limit = Decimal(str(item.get("minAmount", "0")))
        max_limit = Decimal(str(item.get("maxAmount", "0")))
        nickname = str(merchant_info.get("nickname", "Unknown"))
        user_id = str(merchant_info.get("merchantUid", ""))
        order_count = int(merchant_stat.get("latestSuccessOrderCount", 0))

        raw_rate = merchant_stat.get("latestTradeSuccessRate", "0")
        try:
            finish_rate = float(raw_rate)
        except ValueError:
            finish_rate = 0.0

        # Last online status logic
        online_status = merchant_info.get("onlineStatus", False)
        last_online_mins = 0 if online_status else None
        
        # If offline, attempt to parse offline time from onlineHint
        if not online_status:
            online_hint = str(merchant_info.get("onlineHint", ""))
            if "min" in online_hint:
                try:
                    # e.g., "Last seen 22 min ago"
                    last_online_mins = int(online_hint.split("seen")[1].split("min")[0].strip())
                except Exception:
                    pass

        terms_text, terms_state = terms_status.from_payload(item, "termsDesc")

        return Order(
            id=f"bx_{adv_no}",
            price=price,
            available_amount=available,
            min_limit=min_limit,
            max_limit=max_limit,
            merchant_id=user_id,
            merchant_name=nickname,
            month_order_count=order_count,
            finish_rate_pct=round(finish_rate, 1),
            exchange="BingX",
            link=f"https://bingx.com/p2p?uid={user_id}",
            bank_codes=bank_codes,
            trade_terms=terms_text,
            terms_status=terms_state,
            is_verified=bool(merchant_info.get("verificationType", 0) > 0),
            last_online_mins=last_online_mins,
            is_new_user_subsidy=bool(subsidized_info.get("isNewUserSubsidy", False)),
        )

    async def get_buy_orders(self, amount: float, banks: List[str]) -> List[Order]:
        buy_orders, _ = await self.fetch_both_multi([amount], banks)
        return buy_orders

    async def get_sell_orders(self, amount: float, banks: List[str]) -> List[Order]:
        _, sell_orders = await self.fetch_both_multi([amount], banks)
        return sell_orders

    async def fetch_both_multi(self, amounts: List[float], banks: List[str]) -> Tuple[List[Order], List[Order]]:
        """
        Loads cached buy/sell raw listings fetched by the background worker,
        parses bank code matches, and performs fast deduplication.
        """
        # Ensure the background fetch loop is running
        self.start_bg_task()

        # Wait for the first background fetch to populate data if empty
        for _ in range(150):
            if self._cached_buy and self._cached_sell:
                break
            await asyncio.sleep(0.1)

        buy_items = self._cached_buy.get("data", {}).get("result", []) or []
        sell_items = self._cached_sell.get("data", {}).get("result", []) or []

        buy_orders = []
        sell_orders = []

        # Target banks lookup set for quick matching
        target_banks_set = set(banks)

        # Parse Buy Advertisements (User Buys, merchant sells - type 2)
        for item in buy_items:
            matched_banks = []
            payment_methods = item.get("paymentMethodList", [])
            for method in payment_methods:
                method_id = str(method.get("id", ""))
                internal_bank = BankRegistry.from_api_code(method_id, "BingX")
                if internal_bank and internal_bank in target_banks_set:
                    matched_banks.append(internal_bank)
            
            if matched_banks:
                try:
                    buy_orders.append(self._parse_order(item, matched_banks))
                except Exception as e:
                    logger.warning("BingX parse error for BUY ad: %s", e)

        # Parse Sell Advertisements (User Sells, merchant buys - type 1)
        for item in sell_items:
            matched_banks = []
            payment_methods = item.get("paymentMethodList", [])
            for method in payment_methods:
                method_id = str(method.get("id", ""))
                internal_bank = BankRegistry.from_api_code(method_id, "BingX")
                if internal_bank and internal_bank in target_banks_set:
                    matched_banks.append(internal_bank)
            
            if matched_banks:
                try:
                    sell_orders.append(self._parse_order(item, matched_banks))
                except Exception as e:
                    logger.warning("BingX parse error for SELL ad: %s", e)

        return self.dedup(buy_orders), self.dedup(sell_orders)
