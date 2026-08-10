# exchanges/okx.py
import logging
import asyncio
import time
from typing import List, Tuple
from decimal import Decimal
from exchanges.base import BaseExchange, Order
from infrastructure.http.okx_client import OkxClient
from config.banks import BankRegistry
from core.engine import bank_discovery, terms_status

logger = logging.getLogger(__name__)

# Ключі, під якими біржі кладуть «коли мерчант був онлайн». Точну назву для
# OKX з документації не видно, а поле в інтерфейсі є — тому пробуємо
# кілька, а невідому відповідь один раз логуємо, щоб додати сюди факт, а
# не здогадку.
_ONLINE_FLAGS = ("isOnline", "online", "userOnline")
_LAST_SEEN_KEYS = ("lastOnlineTime", "lastActiveTime", "lastLogoutTime", "latestActiveTime")

_online_keys_logged = False


def _online_minutes(item: dict) -> int | None:
    """
    Скільки хвилин тому мерчанта бачили. None — біржа цього не сказала.

    None і 0 — різні відповіді: нуль означає «онлайн зараз», а None — що ми
    не знаємо. Показувати друге як «давно не заходив» не можна.
    """
    global _online_keys_logged

    for key in _ONLINE_FLAGS:
        if key in item:
            return 0 if bool(item.get(key)) else None

    now = int(time.time())
    for key in _LAST_SEEN_KEYS:
        raw = item.get(key)
        if not raw:
            continue
        try:
            ts = int(raw)
        except (TypeError, ValueError):
            continue
        # OKX віддає час у мілісекундах — секундне значення було б у 1970-х.
        if ts > 1e11:
            ts //= 1000
        return max(0, (now - ts) // 60)

    if not _online_keys_logged:
        _online_keys_logged = True
        logger.debug("OKX: онлайн-статусу немає у відповіді; ключі: %s", sorted(item))
    return None


class OkxExchange(BaseExchange):
    def __init__(self, client: OkxClient, db = None):
        self.client = client
        self.db = db
        self.url_prelogin = "https://www.okx.com/v3/c2c/tradingOrders/getMarketplaceAdsPrelogin"
        self.url_auth = "https://www.okx.com/v3/c2c/tradingOrders/getMarketplaceAds"

    def _parse_order(self, item: dict, api_side: str = "") -> Order:
        bank_codes = []
        raw_methods = item.get("paymentMethods", [])

        for method in raw_methods:
            name = method.get("bankName", "") if isinstance(method, dict) else str(method)
            code = BankRegistry.from_api_code(name, "OKX")
            if code:
                bank_codes.append(code)
            elif name:
                # Як і в Binance: невідомий метод випадав зі списку банків
                # ордера мовчки. Фіксуємо, щоб реєстр можна було доповнити
                # за фактом, а не за здогадкою.
                bank_discovery.note("OKX", name, method)

        # Умови OKX кладе в tradingOrderInfo.tradeOrderDesc. Якщо самого
        # блоку в відповіді немає — це не «мерчант не вказав умов», а «ми їх
        # не бачили»: у списку ордерів OKX цей блок з'являється не завжди.
        order_info = item.get("tradingOrderInfo")
        if isinstance(order_info, dict):
            terms_text, terms_state = terms_status.from_payload(order_info, "tradeOrderDesc")
        else:
            terms_text, terms_state = "", terms_status.UNKNOWN

        last_online_mins = _online_minutes(item)

        if not bank_codes:
            logger.debug("⚠️ Не вдалося розпізнати банки в ордері OKX: %s", raw_methods)

        frontend_side = ""
        if api_side == "sell":
            frontend_side = "buy"
        elif api_side == "buy":
            frontend_side = "sell"

        link = f"https://www.okx.com/p2p/ads-merchant?publicUserId={item.get('publicUserId', '')}"

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
            link=link,
            bank_codes=bank_codes,
            trade_terms=terms_text,
            terms_status=terms_state,
            is_verified=bool(item.get("isAuthenticatedMerchant") or item.get("isMerchant")),
            last_online_mins=last_online_mins,
        )

    async def _fetch_orders(self, amount: float, bank_code: str, side: str) -> List[Order]:
        bank_name = BankRegistry.get_exchange_code(bank_code, "OKX") or "all"

        params = {
             "fiatCurrency": "UAH",
             "cryptoCurrency": "USDT",
             "paymentMethod": bank_name,
             "side": side,
             "userType": "all",
             "sortType": "price_asc" if side == "sell" else "price_desc",
             "numberPerPage": "50",
             "t": str(int(time.time() * 1000)),
        }

        # Session loading for user-personalized order book
        headers = None
        cookies = None
        url = self.url_prelogin
        if self.db:
            try:
                headers, cookies, _ = await self.db.get_auth_session("OKX")
                if headers or cookies:
                    url = self.url_auth
            except Exception as e:
                logger.debug("Failed to retrieve OKX session from DB: %s", e)

        try:
            data = await self.client.fetch(url, params, method="GET", headers=headers, cookies=cookies)
            if not isinstance(data, dict) or data.get("code") != 0:
                if url == self.url_auth:
                    logger.debug("OKX auth endpoint returned non-zero code, trying prelogin endpoint...")
                    data = await self.client.fetch(self.url_prelogin, params, method="GET", headers=headers, cookies=cookies)
            if not isinstance(data, dict) or data.get("code") != 0:
                return []
            items = data.get("data", {}).get(side, [])
            return [self._parse_order(item, side) for item in items]
        except Exception as e:
            logger.debug("OKX fetch error: %s", e)
            if url == self.url_auth:
                try:
                    logger.debug("Retrying OKX prelogin fallback...")
                    data = await self.client.fetch(self.url_prelogin, params, method="GET", headers=headers, cookies=cookies)
                    if isinstance(data, dict) and data.get("code") == 0:
                        items = data.get("data", {}).get(side, [])
                        return [self._parse_order(item, side) for item in items]
                except Exception as e2:
                    logger.debug("OKX prelogin fallback failed: %s", e2)
            return []

    async def get_buy_orders(self, amount: float, banks: List[str]) -> List[Order]:
        orders = await self._fetch_orders(amount, "all", "sell")
        target_banks = set(banks)
        filtered = []
        for o in orders:
            if any(b in target_banks for b in o.bank_codes):
                filtered.append(o)
        return filtered

    async def get_sell_orders(self, amount: float, banks: List[str]) -> List[Order]:
        orders = await self._fetch_orders(amount, "all", "buy")
        target_banks = set(banks)
        filtered = []
        for o in orders:
            if any(b in target_banks for b in o.bank_codes):
                filtered.append(o)
        return filtered

    async def fetch_both_multi(self, amounts: list[float], banks: list[str]) -> Tuple[List[Order], List[Order]]:
        max_amount = max(amounts) if amounts else 1000.0
        buys, sells = await asyncio.gather(
            self.get_buy_orders(max_amount, banks),
            self.get_sell_orders(max_amount, banks)
        )
        return buys, sells
