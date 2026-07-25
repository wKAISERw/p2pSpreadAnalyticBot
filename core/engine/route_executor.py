import logging
import uuid
import json
from typing import Dict, Any, Optional

from config import settings
from infrastructure.http.bybit_p2p_client import BybitP2PClient
from infrastructure.http.binance_client import BinanceClient
from core.utils.rate_limiter import global_rate_limiter

logger = logging.getLogger("RouteExecutor")

class RouteExecutor:
    """
    Клас, що відповідає за низькорівневе відправлення POST/PUT запитів на біржі.
    Реалізовує підтримку DRY_RUN_MODE: якщо увімкнено, замість реальних HTTP-запитів
    повертає фейкові order_id та статуси успіху, щоб можна було безпечно тестувати всю логіку.
    """

    def __init__(self):
        pass

    async def execute_taker_order(
        self,
        exchange: str,
        action: str,  # 'BUY' або 'SELL'
        ad_id: str,
        fiat_amount: float,
        price: float,
        credentials: Dict[str, str],
        fiat: str = "UAH",
        asset: str = "USDT"
    ) -> Dict[str, Any]:
        """
        Виконує створення Taker ордеру (стає на чуже оголошення).
        Повертає уніфікований словник: {"success": bool, "order_id": str, "error": str}
        """
        logger.info(f"[{exchange}] Збираю TAKER запит: {action} {fiat_amount} {fiat} по ціні {price} (Ad: {ad_id})")

        # 🛑 ПЕРЕВІРКА DRY RUN
        if settings.dry_run_mode:
            logger.warning(f"⚠️ [DRY RUN] Імітуємо виконання ордеру TAKER {action} на {exchange} (Ad: {ad_id})")
            fake_order_id = f"DRY_{uuid.uuid4().hex[:8].upper()}"
            return {
                "success": True,
                "order_id": fake_order_id,
                "error": None
            }

        # БЛОК РЕАЛЬНОГО БОЙОВОГО ЗАПУСКУ (якщо DRY_RUN_MODE = False)
        # Блок 5: RateLimiter — обмежуємо кількість запитів per exchange
        async with global_rate_limiter(exchange):
            if exchange == "Bybit":
                return await self._execute_bybit_taker(action, ad_id, fiat_amount, price, credentials)
            elif exchange == "Binance":
                return await self._execute_binance_taker(action, ad_id, fiat_amount, price, credentials)
            elif exchange == "OKX":
                logger.error(f"OKX Taker наразі не підтримується (Потрібна фаза 3 - Session Hijacking).")
                return {"success": False, "order_id": None, "error": "OKX explicitly blocked in Phase 1"}
            else:
                return {"success": False, "order_id": None, "error": f"Unsupported exchange for executor: {exchange}"}

    async def update_maker_ad_price(
        self,
        exchange: str,
        ad_id: str,
        new_price: float,
        credentials: Dict[str, str],
    ) -> bool:
        """
        Блок 4 (Maker): Оновлює ціну власного оголошення на біржі.
        Це — основний інструмент AdRepricer для утримання топ-позиції.
        Повертає True якщо успішно.
        """
        logger.info(f"[{exchange}] 🔄 Оновлення ціни Maker-оголошення {ad_id} → {new_price:.4f}")

        if settings.dry_run_mode:
            logger.warning(f"⚠️ [DRY RUN] Імітуємо update_maker_ad_price на {exchange}: {ad_id} = {new_price:.4f}")
            return True

        async with global_rate_limiter(exchange):
            if exchange == "Bybit":
                return await self._update_bybit_ad_price(ad_id, new_price, credentials)
            else:
                logger.warning(f"[{exchange}] update_maker_ad_price ще не реалізовано.")
                return False

    # ─── Bybit ──────────────────────────────────────────────────────────────

    async def _execute_bybit_taker(self, action: str, ad_id: str, fiat_amount: float, price: float, credentials: Dict[str, str]) -> Dict[str, Any]:
        """ Приватний метод для Bybit (OpenAPI /fiat/otc/order/openapi/create) """
        try:
            client = BybitP2PClient()
            client.set_credentials(credentials.get("api_key", ""), credentials.get("api_secret", ""))
            
            url = "https://api2.bybit.com/fiat/otc/order/openapi/create"
            payload = {
                "itemId": str(ad_id),
                "amount": str(fiat_amount), 
                "clientId": f"P2PS_{uuid.uuid4().hex[:10]}"
            }

            async with client:
                payload_str = json.dumps(payload)
                signed_headers = client._sign_headers(payload_str)
                resp = await client._session.post(url, json=payload, headers=signed_headers)
                
                logger.debug(f"[Bybit] Raw Order Response: {resp.status_code} - {resp.text}")
                
                if resp.status_code == 200:
                    data = resp.json()
                    if data.get("ret_code") == 0 and "result" in data:
                        order_id = data["result"].get("orderId")
                        return {"success": True, "order_id": order_id, "error": None}
                    else:
                        return {"success": False, "order_id": None, "error": data.get("ret_msg", resp.text)}
                else:
                    return {"success": False, "order_id": None, "error": f"HTTP {resp.status_code}: {resp.text}"}

        except Exception as e:
            logger.error(f"[Bybit] Error executing taker: {e}", exc_info=True)
            return {"success": False, "order_id": None, "error": str(e)}

    async def _update_bybit_ad_price(self, ad_id: str, new_price: float, credentials: Dict[str, str]) -> bool:
        """
        Оновлює ціну власного P2P-оголошення Bybit.
        Endpoint: PUT /fiat/otc/item/openapi/update
        """
        try:
            client = BybitP2PClient()
            client.set_credentials(credentials.get("api_key", ""), credentials.get("api_secret", ""))

            url = "https://api2.bybit.com/fiat/otc/item/openapi/update"
            payload = {
                "itemId": str(ad_id),
                "price": str(new_price),
            }

            async with client:
                payload_str = json.dumps(payload)
                signed_headers = client._sign_headers(payload_str)
                resp = await client._session.put(url, json=payload, headers=signed_headers)

                logger.debug(f"[Bybit] Ad Update Response: {resp.status_code} - {resp.text}")

                if resp.status_code == 200:
                    data = resp.json()
                    if data.get("ret_code") == 0:
                        return True
                    else:
                        logger.warning(f"[Bybit] Ad update failed: {data.get('ret_msg')}")
                        return False
                else:
                    logger.warning(f"[Bybit] Ad update HTTP error: {resp.status_code}")
                    return False

        except Exception as e:
            logger.error(f"[Bybit] Error updating ad price: {e}", exc_info=True)
            return False

    # ─── Binance ─────────────────────────────────────────────────────────────

    async def _execute_binance_taker(
        self,
        action: str,
        ad_id: str,
        fiat_amount: float,
        price: float,
        credentials: Dict[str, str],
    ) -> Dict[str, Any]:
        """
        Binance Taker через SessionHijack (Фаза 2).
        Використовує перехоплені cookies + CSRF з auth_sessions
        (той самий механізм що й для відгуків).
        """
        # credentials мають містити 'headers' та 'cookies' від SessionManager
        session_headers = credentials.get("headers") or {}
        session_cookies = credentials.get("cookies") or {}

        if not session_headers or not session_cookies:
            logger.error("[Binance] Відсутня Auth Session. Запустіть SessionManager для Binance.")
            return {"success": False, "order_id": None, "error": "No Binance auth session. Run SessionManager first."}

        try:
            client = BinanceClient()

            url = "https://p2p.binance.com/bapi/c2c/v2/friendly/c2c/order/create"
            # Визначаємо tradeType: якщо ми BUY — беремо SELL-оголошення (продавця), і навпаки
            trade_type = "BUY" if action == "BUY" else "SELL"

            payload = {
                "advNo":         ad_id,
                "tradeType":     trade_type,
                "asset":         "USDT",
                "fiatUnit":      "UAH",
                "amount":        str(round(fiat_amount / price, 4)),  # USDT кількість
                "fiatAmount":    str(round(fiat_amount, 2)),
                "totalPrice":    str(round(fiat_amount, 2)),
                "paymentId":     "",  # Буде автоматично підібрано
                "paymentType":   "",
                "buyType":       "BY_MONEY",
            }

            req_headers = dict(session_headers)
            req_headers.pop("Content-Length", None)
            req_headers.pop("Accept-Encoding", None)
            req_headers["Referer"] = f"https://p2p.binance.com/en/trade/{trade_type.lower()}/{ad_id}"

            # Витягуємо csrftoken з cookies (обов'язковий для POST)
            csrf = session_cookies.get("csrftoken", "")
            if csrf:
                req_headers["X-CSRF-TOKEN"] = csrf

            if client._session is None:
                await client.__aenter__()

            response = await client._session.request(
                "POST", url,
                json=payload,
                headers=req_headers,
                cookies=session_cookies,
            )

            logger.debug(f"[Binance] Order response: {response.status_code} - {response.text[:300]}")

            if response.status_code in (401, 403):
                raise RuntimeError(f"AuthError: HTTP {response.status_code}")

            data = response.json()
            code = str(data.get("code", ""))

            if code in ("000004", "000008", "401") or "Unauthorized" in str(data):
                raise RuntimeError(f"AuthError: Token expired. {data}")

            if data.get("success") or data.get("code") == "000000":
                order_id = data.get("data", {}).get("orderNumber") or data.get("data", {}).get("orderId")
                return {"success": True, "order_id": order_id, "error": None}
            else:
                return {"success": False, "order_id": None, "error": data.get("message", str(data))}

        except RuntimeError as e:
            if "AuthError" in str(e):
                logger.error(f"[Binance] Auth Error — сесія протухла: {e}")
                return {"success": False, "order_id": None, "error": str(e)}
            raise
        except Exception as e:
            logger.error(f"[Binance] Error executing taker: {e}", exc_info=True)
            return {"success": False, "order_id": None, "error": str(e)}

    # ─── Maker Operations ────────────────────────────────────────────────────

    async def create_maker_ad(
        self,
        exchange: str,
        action: str,         # 'BUY' або 'SELL'
        price: float,
        amount_usdt: float,
        fiat: str = "UAH",
        asset: str = "USDT",
        min_order_uah: float = 500.0,
        max_order_uah: Optional[float] = None,
        credentials: Optional[Dict[str, str]] = None,
        payment_methods: Optional[list] = None,
    ) -> Dict[str, Any]:
        """
        Блок 7: Розміщує власне Maker-оголошення на біржі.
        Повертає {"success": bool, "ad_id": str, "error": str}
        """
        credentials = credentials or {}
        max_order_uah = max_order_uah or (amount_usdt * price)
        logger.info(
            f"[{exchange}] 📢 Розміщення Maker AD: {action} {amount_usdt} {asset} @ {price} {fiat} "
            f"(min={min_order_uah}, max={max_order_uah:.0f})"
        )

        if settings.dry_run_mode:
            fake_ad_id = f"DRYAD_{uuid.uuid4().hex[:8].upper()}"
            logger.warning(f"⚠️ [DRY RUN] Імітуємо create_maker_ad на {exchange}: {fake_ad_id}")
            return {"success": True, "ad_id": fake_ad_id, "error": None}

        async with global_rate_limiter(exchange):
            if exchange == "Bybit":
                return await self._create_bybit_maker_ad(
                    action, price, amount_usdt, fiat, asset,
                    min_order_uah, max_order_uah, credentials, payment_methods or []
                )
            else:
                return {"success": False, "ad_id": None, "error": f"create_maker_ad не реалізовано для {exchange}"}

    async def cancel_maker_ad(
        self,
        exchange: str,
        ad_id: str,
        credentials: Optional[Dict[str, str]] = None,
    ) -> bool:
        """
        Скасовує власне Maker-оголошення (стан CANCELLING_AD у FSM).
        """
        credentials = credentials or {}
        logger.info(f"[{exchange}] 🗑 Скасування Maker AD: {ad_id}")

        if settings.dry_run_mode:
            logger.warning(f"⚠️ [DRY RUN] Імітуємо cancel_maker_ad на {exchange}: {ad_id}")
            return True

        async with global_rate_limiter(exchange):
            if exchange == "Bybit":
                return await self._cancel_bybit_maker_ad(ad_id, credentials)
            else:
                logger.warning(f"[{exchange}] cancel_maker_ad ще не реалізовано.")
                return False

    async def _create_bybit_maker_ad(
        self, action: str, price: float, amount_usdt: float,
        fiat: str, asset: str, min_uah: float, max_uah: float,
        credentials: Dict[str, str], payment_methods: list
    ) -> Dict[str, Any]:
        """Bybit POST /fiat/otc/item/openapi/create"""
        try:
            client = BybitP2PClient()
            client.set_credentials(credentials.get("api_key", ""), credentials.get("api_secret", ""))

            url = "https://api2.bybit.com/fiat/otc/item/openapi/create"
            side = "1" if action == "BUY" else "0"  # Bybit: 1=Buy, 0=Sell
            payload = {
                "tokenId":         asset,
                "currencyId":      fiat,
                "side":            side,
                "priceType":       "0",   # 0=Fixed price
                "premium":         "0",
                "price":           str(price),
                "quantity":        str(amount_usdt),
                "minAmount":       str(min_uah),
                "maxAmount":       str(max_uah),
                "paymentIds":      payment_methods or [],
                "remark":          "",
                "itemRegion":      "1",   # 1=Ukraine
            }

            async with client:
                payload_str = json.dumps(payload)
                signed_headers = client._sign_headers(payload_str)
                resp = await client._session.post(url, json=payload, headers=signed_headers)

                logger.debug(f"[Bybit] Create Ad Response: {resp.status_code} - {resp.text[:200]}")

                if resp.status_code == 200:
                    data = resp.json()
                    if data.get("ret_code") == 0:
                        ad_id = data.get("result", {}).get("itemId")
                        return {"success": True, "ad_id": ad_id, "error": None}
                    else:
                        return {"success": False, "ad_id": None, "error": data.get("ret_msg", resp.text)}
                else:
                    return {"success": False, "ad_id": None, "error": f"HTTP {resp.status_code}"}

        except Exception as e:
            logger.error(f"[Bybit] Error creating maker ad: {e}", exc_info=True)
            return {"success": False, "ad_id": None, "error": str(e)}

    async def _cancel_bybit_maker_ad(self, ad_id: str, credentials: Dict[str, str]) -> bool:
        """Bybit POST /fiat/otc/item/openapi/cancel"""
        try:
            client = BybitP2PClient()
            client.set_credentials(credentials.get("api_key", ""), credentials.get("api_secret", ""))

            url = "https://api2.bybit.com/fiat/otc/item/openapi/cancel"
            payload = {"itemId": str(ad_id)}

            async with client:
                payload_str = json.dumps(payload)
                signed_headers = client._sign_headers(payload_str)
                resp = await client._session.post(url, json=payload, headers=signed_headers)

                if resp.status_code == 200:
                    data = resp.json()
                    return data.get("ret_code") == 0
                return False

        except Exception as e:
            logger.error(f"[Bybit] Error cancelling ad: {e}", exc_info=True)
            return False

