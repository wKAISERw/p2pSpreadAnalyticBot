import aiohttp
import logging
from typing import Optional, Dict

logger = logging.getLogger(__name__)

class MonoApiClient:
    BASE_URL = "https://api.monobank.ua"

    def __init__(self, token: str):
        self.token = token
        self.headers = {"X-Token": self.token}

    async def get_client_info(self) -> Optional[Dict]:
        """Returns client info including accounts."""
        try:
            async with aiohttp.ClientSession(headers=self.headers) as session:
                async with session.get(f"{self.BASE_URL}/personal/client-info") as resp:
                    if resp.status == 200:
                        return await resp.json()
                    logger.error(f"Mono API error: {resp.status} - {await resp.text()}")
        except Exception as e:
            logger.error(f"Mono API exception: {e}")
        return None

    async def setup_webhook(self, url: str) -> bool:
        """Sets the webhook URL."""
        try:
            async with aiohttp.ClientSession(headers=self.headers) as session:
                async with session.post(f"{self.BASE_URL}/personal/webhook", json={"webHookUrl": url}) as resp:
                    if resp.status == 200:
                        return True
                    logger.error(f"Mono API webhook setup error: {resp.status} - {await resp.text()}")
        except Exception as e:
            logger.error(f"Mono API webhook setup exception: {e}")
        return False
