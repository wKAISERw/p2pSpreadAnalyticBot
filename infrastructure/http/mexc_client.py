# infrastructure/http/mexc_client.py
import logging
from typing import Optional, Any
from .base_client import BaseHttpClient

logger = logging.getLogger(__name__)

BASE_URL = "https://www.mexc.com/api/platform/p2p/api/market"


class MexcClient(BaseHttpClient):
    def __init__(self, proxy: Optional[str] = None):
        extra_headers = {
            "accept": "application/json, text/plain, */*",
            "referer": "https://www.mexc.com/p2p",
        }
        super().__init__(proxy=proxy, extra_headers=extra_headers, timeout=8.0)

    async def fetch(self, payload: dict) -> Any:
        return await self._get(BASE_URL, params=payload)

    async def fetch_merchant_reviews(self, member_id: str, page: int = 1) -> dict:
        """
        Один запит → і статистика, і тексти відгуків.
        Повертає: {good: int, bad: int, total: int, good_rating: str, reviews: list[dict]}
        """
        url = "https://www.mexc.com/api/platform/p2p/api/order/review/out/list"

        params = {
            "pageNum": page,
            "memberId": member_id
        }

        headers = {
            "X-Device-Id": "unknowndeviceid",
            "X-Platform-Type": "web",
            "Language": "uk-UA",
            "Referer": f"https://www.mexc.com/uk-UA/buy-crypto/merchant?id={member_id}"
        }

        try:
            data = await self._get(url, params=params, headers=headers)
            d = data.get("data", {})
            return {
                "total": int(d.get("totalCount", 0) or 0),
                "good": int(d.get("goodCount", 0) or 0),
                "bad": int(d.get("badCount", 0) or 0),
                "good_rating": d.get("goodRating", "0"),
                "reviews": d.get("result", []) or [],
            }
        except Exception as e:
            logger.debug("MEXC fetch_merchant_reviews [%s]: %s", member_id, e)
            return {"total": 0, "good": 0, "bad": 0, "good_rating": "0", "reviews": []}
