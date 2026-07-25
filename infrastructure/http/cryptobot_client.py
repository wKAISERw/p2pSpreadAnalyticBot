# infrastructure/http/cryptobot_client.py
from __future__ import annotations
import asyncio
import logging
import time
from typing import Any, Optional, List

from infrastructure.http.base_client import BaseHttpClient

logger = logging.getLogger("CryptoBotWebClient")


class CryptoBotWebClient(BaseHttpClient):
    """
    HTTP-клієнт для взаємодії з веб-версією P2P CryptoBot (app.send.tg).
    Використовує Pyrogram-юзербота для періодичного оновлення авторизаційного токена.
    """

    def __init__(self, proxy: Optional[str] = None):
        super().__init__(proxy=proxy)
        self.userbot = None
        self._last_auth_time: float = 0.0
        self._auth_lock = asyncio.Lock()

    def set_userbot(self, userbot) -> None:
        """Встановлює зв'язок з юзерботом для авторизації."""
        self.userbot = userbot

    async def ensure_authorized(self) -> None:
        """Перевіряє свіжість токена і за потреби авторизується."""
        async with self._auth_lock:
            now = time.monotonic()
            # Оновлюємо токен кожні 8 хвилин (480 сек)
            if not self._last_auth_time or (now - self._last_auth_time > 480.0):
                await self._login()

    async def _login(self) -> None:
        if not self.userbot:
            raise RuntimeError("Userbot not set on CryptoBotWebClient")

        logger.info("CryptoBotWebClient: Obtaining fresh tgWebAppData from userbot...")
        init_data = await self.userbot.get_webapp_init_data()
        if not init_data:
            raise RuntimeError("Failed to get tgWebAppData from userbot")

        logger.info("CryptoBotWebClient: Authenticating with app.send.tg...")
        if self._session is None:
            await self.__aenter__()

        url = "https://app.send.tg/internal/v1/authentication/webapp"
        headers = {
            "Content-Type": "application/json",
            "Accept": "application/json, text/plain, */*",
            "x-telegram-platform": "android",
            "Origin": "https://app.send.tg",
            "Referer": "https://app.send.tg/"
        }

        # Робимо прямий запит через AsyncSession щоб уникнути рекурсії ensure_authorized
        resp = await self._session.request("POST", url, json={"initData": init_data}, headers=headers)
        if resp.status_code != 200:
            raise RuntimeError(f"CryptoBot Web API auth failed: status={resp.status_code}, body={resp.text[:200]}")

        logger.info("CryptoBotWebClient: Successfully authenticated and set access_token cookie!")
        self._last_auth_time = time.monotonic()

    async def _request(self, method: str, url: str, **kwargs) -> Any:
        if "authentication/webapp" not in url:
            await self.ensure_authorized()
        
        # Додаємо обов'язковий заголовок платформи
        if "headers" not in kwargs:
            kwargs["headers"] = {}
        kwargs["headers"]["x-telegram-platform"] = "android"
        
        return await super()._request(method, url, **kwargs)

    async def fetch_offers(self, fiat: str, asset: str, side: str, count: int = 50) -> dict:
        """Отримує список активних ордерів (offers)."""
        url = "https://app.send.tg/internal/v1/p2p/offers"
        params = {
            "count": str(count),
            "type": side.lower(),  # buy або sell
            "asset": asset.upper(),
            "fiat": fiat.upper()
        }
        return await self._get(url, params=params)

    async def fetch_offer_details(self, offer_id: int) -> dict:
        """Отримує детальні умови конкретного ордера (description/terms)."""
        url = f"https://app.send.tg/internal/v1/p2p/offers/{offer_id}"
        return await self._get(url)

    async def fetch_user_profile(self, user_id: int) -> dict:
        """Отримує профіль користувача (для дати створення created_at)."""
        url = f"https://app.send.tg/internal/v1/p2p/profile/{user_id}"
        return await self._get(url)

    async def fetch_review_stats(self, user_id: int) -> dict:
        """Статистика відгуків: {"positive": int, "negative": int, "count": int}."""
        url = f"https://app.send.tg/internal/v1/p2p/profile/{user_id}/reviews/stats"
        return await self._get(url)

    async def fetch_negative_reviews(self, user_id: int, max_pages: int = 5) -> List[dict]:
        """
        Отримує список негативних відгуків з пагінацією через nextCursor.
        Захист від зависання: зупиняється якщо items порожні, cursor не змінюється,
        або досягнуто max_pages.
        """
        url = f"https://app.send.tg/internal/v1/p2p/profile/{user_id}/reviews"
        all_items: List[dict] = []
        cursor: Optional[str] = None
        seen_cursors: set[str] = set()

        for _ in range(max_pages):
            params = {"type": "negative"}
            if cursor:
                params["cursor"] = cursor
            resp = await self._get(url, params=params)
            items = resp.get("items", [])
            if not items:
                break
            all_items.extend(items)

            next_cursor = resp.get("nextCursor")
            if not next_cursor or next_cursor in seen_cursors:
                break
            seen_cursors.add(next_cursor)
            cursor = next_cursor

        return all_items
