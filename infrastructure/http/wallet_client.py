# infrastructure/http/wallet_client.py
import logging
from typing import Optional, Any, List
from config import settings
from .base_client import BaseHttpClient

logger = logging.getLogger(__name__)

# Wallet P2P API endpoint
_WALLET_P2P_URL = "https://p2p.walletbot.me/p2p/integration-api/v1/item/online"


class WalletClient(BaseHttpClient):
    # Wallet 429: ретраємо 3 рази з швидкою паузою.
    # backoff * 3 (для 429) = 0.3s, 0.6s, 0.9s — всі 3 спроби за ~2с.
    # Якщо всі 3 провалились — RuntimeError підніметься до CircuitBreaker.
    MAX_RETRIES = 3
    RETRY_BACKOFF = [0.1, 0.2, 0.3]

    def __init__(self, proxy: Optional[str] = None):
        token = settings.wallet_token or ""
        extra_headers = {
            "accept": "application/json",
            "content-type": "application/json",
        }
        if token:
            extra_headers["X-API-Key"] = token
        super().__init__(proxy=proxy, extra_headers=extra_headers, timeout=4.0)
        self.userbot: Any = None
        self._jwt_token: str = ""
        self._jwt_fetched_at: float = 0.0

    def set_userbot(self, userbot: Any) -> None:
        """Зберігає посилання на запущеного юзербота для отримання JWT-токенів."""
        self.userbot = userbot

    async def get_valid_jwt(self) -> str:
        """Повертає діючий JWT токен. Кешує його на 8 хвилин (термін дії JWT 10 хв)."""
        import time
        # Кешуємо на 8 хвилин (480 сек)
        if self._jwt_token and (time.monotonic() - self._jwt_fetched_at < 480.0):
            return self._jwt_token

        if not self.userbot:
            logger.warning("⚠️ Не встановлено CryptoBotUserbot для оновлення JWT токена Wallet")
            return ""

        jwt = await self.userbot.get_wallet_jwt_token()
        if jwt:
            self._jwt_token = jwt
            self._jwt_fetched_at = time.monotonic()
            return jwt
        return ""

    async def fetch_offer_comment(self, offer_id: int) -> Optional[str]:
        """
        Отримує коментар (умови) оффера через внутрішній P2P API.
        
        :param offer_id: Ідентифікатор оффера
        :returns:
            - str: коментар (умови) мерчанта (може бути порожнім рядоком "")
            - None: якщо не вдалося завантажити умови через помилку авторизації чи мережі
        """
        jwt = await self.get_valid_jwt()
        if not jwt:
            logger.error("❌ Немає валідного JWT для отримання умов Wallet оффера %d", offer_id)
            return None

        url = "https://p2p.walletbot.me/p2p/public-api/v2/offer/get"
        headers = {
            "Authorization": f"Bearer {jwt}",
            "Content-Type": "application/json"
        }
        payload = {"offerId": int(offer_id)}

        try:
            # Використовуємо наш базовий метод _post, але з кастомними заголовками
            # (оскільки base_client.py дозволяє перевизначати заголовки або робити запити)
            # Запити йдуть через _post(url, json=payload, headers=headers)
            res = await self._post(url, json=payload, headers=headers)
            if res and res.get("status") == "SUCCESS":
                return str(res.get("data", {}).get("comment", "") or "").strip()
            
            logger.warning("⚠️ Wallet offer/get повернув статус: %s", res)
            return None
        except Exception as e:
            logger.error("❌ Помилка при отриманні коментаря Wallet оффера %d: %s", offer_id, e)
            return None

    def set_credentials(self, api_key: str) -> None:
        """Встановлює API ключ з БД (замість .env)."""
        if self._session:
            self._session.headers["X-API-Key"] = api_key
        self._extra_headers["X-API-Key"] = api_key

    async def fetch(self, url: str, payload: dict) -> Any:
        return await self._post(url, json=payload)

    # ═══════════════════════════════════════════════════════════════════════
    # Блок B: Уніфіковані методи для сканера
    # ═══════════════════════════════════════════════════════════════════════

    async def fetch_p2p_ads(
        self,
        fiat: str = "UAH",
        asset: str = "USDT",
        side: str = "SELL",
        page: int = 1,
        size: int = 50,
    ) -> List[dict]:
        """
        Уніфікований метод отримання оголошень Wallet P2P.

        :param fiat: Фіатна валюта (UAH, RUB тощо)
        :param asset: Криптовалюта (USDT, TON тощо)
        :param side: SELL (для покупки крипти) або BUY (для продажу)
        :param page: Номер сторінки
        :param size: Кількість оголошень на сторінці
        :returns: Список оголошень у уніфікованому форматі
        """
        payload = {
            "cryptoCurrency": asset,
            "fiatCurrency": fiat,
            "side": side,
            "page": page,
            "pageSize": size,
        }

        try:
            data = await self.fetch(_WALLET_P2P_URL, payload)

            if not data or data.get("status") != "SUCCESS":
                logger.warning("Wallet fetch_p2p_ads: non-SUCCESS response: %s", data)
                return []

            items = data.get("data", [])
            result = []
            for item in items:
                try:
                    result.append({
                        "id": str(item.get("id", "")),
                        "price": float(item.get("price", 0)),
                        "available_amount": float(item.get("lastQuantity", 0)),
                        "min_limit": float(item.get("minAmount", 0)),
                        "max_limit": float(item.get("maxAmount", 0)),
                        "merchant_id": str(item.get("userId", "")),
                        "merchant_name": str(item.get("nickname", "Unknown")),
                        "order_count": int(item.get("orderNum", 0)),
                        "finish_rate": float(item.get("executeRate", 0)) * 100,
                        "payments": item.get("payments", []),
                        "comment": str(
                            item.get("comment", "") or
                            item.get("notice", "") or
                            item.get("terms", "") or ""
                        ).strip(),
                        "exchange": "Wallet",
                        "side": side,
                    })
                except Exception as e:
                    logger.debug("Wallet ad parse error: %s", e)

            return result
        except Exception as e:
            logger.error("Wallet fetch_p2p_ads (%s %s/%s): %s", side, asset, fiat, e)
            return []

    async def fetch_p2p_book_top(
        self,
        fiat: str = "UAH",
        asset: str = "USDT",
        side: str = "SELL",
        exclude_ad_id: str = "",
    ) -> Optional[float]:
        """
        Повертає найкращу ціну у стакані Wallet P2P (аналог для AdRepricer).
        Для Wallet це read-only — використовується лише для аналізу ринку.

        :param fiat: Фіатна валюта
        :param asset: Криптовалюта
        :param side: SELL або BUY
        :param exclude_ad_id: ID оголошення для виключення (наше власне)
        :returns: Найкраща ціна або None
        """
        try:
            ads = await self.fetch_p2p_ads(fiat=fiat, asset=asset, side=side, page=1, size=10)
            if not ads:
                return None

            for ad in ads:
                if exclude_ad_id and str(ad.get("id", "")) == exclude_ad_id:
                    continue
                price = ad.get("price", 0)
                if price > 0:
                    return price

            return None
        except Exception as e:
            logger.debug("Wallet fetch_p2p_book_top: %s", e)
            return None
