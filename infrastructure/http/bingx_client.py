# infrastructure/http/bingx_client.py
import asyncio
import json
import logging
from typing import Optional, Any, Tuple
from playwright.async_api import async_playwright

from .base_client import BaseHttpClient

logger = logging.getLogger("BingxClient")

class BingxClient(BaseHttpClient):
    """
    Playwright-based HTTP client for BingX P2P.
    Loads fiat.bingx.com/p2p inside a headless Chromium instance to bypass
    dynamic client-side signature generation by capturing native network responses.
    """
    def __init__(self, proxy: Optional[str] = None):
        super().__init__(proxy=proxy, timeout=15.0)
        self._playwright = None
        self._browser = None
        self._context = None
        self._page = None

    async def __aenter__(self) -> "BingxClient":
        await super().__aenter__()
        
        logger.info("Starting headless browser for BingX P2P...")
        self._playwright = await async_playwright().start()
        
        launch_kwargs = {"headless": True}
        if self.proxy:
            launch_kwargs["proxy"] = {"server": self.proxy}
            
        self._browser = await self._playwright.chromium.launch(**launch_kwargs)
        self._context = await self._browser.new_context(
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
        )
        self._page = await self._context.new_page()
        
        # Pre-initialize page
        logger.info("Navigating to BingX P2P portal...")
        try:
            await self._page.goto("https://fiat.bingx.com/uk/p2p", timeout=15000)
            await self._page.wait_for_timeout(2000)
        except Exception as e:
            logger.warning("Initial navigation to BingX failed: %s", e)
            
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb) -> None:
        logger.info("Stopping headless browser for BingX...")
        if self._browser:
            try:
                await self._browser.close()
            except Exception as e:
                logger.debug("Error closing browser: %s", e)
        if self._playwright:
            try:
                await self._playwright.stop()
            except Exception as e:
                logger.debug("Error stopping playwright: %s", e)
        self._browser = None
        self._page = None
        await super().__aexit__(exc_type, exc_val, exc_tb)

    async def fetch_both(self) -> Tuple[dict, dict]:
        """
        Uses fast tab switching to trigger fresh P2P API requests in the open browser context,
        avoiding slow full-page reloads.
        """
        buy_data = None
        sell_data = None

        async def handle_response(response):
            nonlocal buy_data, sell_data
            url = response.url
            if "advert/list" in url:
                try:
                    request = response.request
                    payload = json.loads(request.post_data)
                    text = await response.text()
                    res_json = json.loads(text)
                    if payload.get("type") == 1:
                        buy_data = res_json
                    elif payload.get("type") == 2:
                        sell_data = res_json
                except Exception as e:
                    logger.debug("Error parsing intercepted response: %s", e)

        self._page.on("response", handle_response)

        try:
            # Fallback check: if the page is not loaded, do a full navigation first
            current_url = self._page.url
            if "fiat.bingx.com" not in current_url or "/p2p" not in current_url:
                logger.info("BingX page not loaded (current URL: %s), navigating to P2P portal...", current_url)
                await self._page.goto("https://fiat.bingx.com/uk/p2p", timeout=15000)
                await asyncio.sleep(2.0)

            # Determine currently active tab by checking .active class
            await self._page.evaluate("""
                () => {
                    const items = Array.from(document.querySelectorAll('.bx-segmented-item'));
                    const buyTab = items[0];
                    const isBuyActive = buyTab && buyTab.classList.contains('active');
                    window._activeTab = isBuyActive ? 'Buy' : 'Sell';
                }
            """)
            
            active_tab = await self._page.evaluate("window._activeTab")
            
            if active_tab == 'Buy':
                # 1. Click Sell tab to trigger Sell ads
                logger.debug("Clicking Sell tab...")
                await self._page.evaluate("""
                    () => {
                        const items = Array.from(document.querySelectorAll('.bx-segmented-item'));
                        const sellTab = items[1];
                        if (sellTab) sellTab.click();
                    }
                """)
                for _ in range(40):
                    if sell_data:
                        break
                    await asyncio.sleep(0.05)
                
                # 2. Click Buy tab to trigger Buy ads
                logger.debug("Clicking Buy tab...")
                await self._page.evaluate("""
                    () => {
                        const items = Array.from(document.querySelectorAll('.bx-segmented-item'));
                        const buyTab = items[0];
                        if (buyTab) buyTab.click();
                    }
                """)
                for _ in range(40):
                    if buy_data:
                        break
                    await asyncio.sleep(0.05)
            else:
                # 1. Click Buy tab to trigger Buy ads
                logger.debug("Clicking Buy tab...")
                await self._page.evaluate("""
                    () => {
                        const items = Array.from(document.querySelectorAll('.bx-segmented-item'));
                        const buyTab = items[0];
                        if (buyTab) buyTab.click();
                    }
                """)
                for _ in range(40):
                    if buy_data:
                        break
                    await asyncio.sleep(0.05)
                
                # 2. Click Sell tab to trigger Sell ads
                logger.debug("Clicking Sell tab...")
                await self._page.evaluate("""
                    () => {
                        const items = Array.from(document.querySelectorAll('.bx-segmented-item'));
                        const sellTab = items[1];
                        if (sellTab) sellTab.click();
                    }
                """)
                for _ in range(40):
                    if sell_data:
                        break
                    await asyncio.sleep(0.05)

        except Exception as e:
            logger.error("Error during BingX P2P fetch: %s", e)
        finally:
            self._page.remove_listener("response", handle_response)

        return buy_data or {}, sell_data or {}

    async def fetch_merchant_reviews(self, member_id: str, page: int = 1) -> dict:
        # Dummy reviews method to support the ReviewFetcher interface
        return {"total": 0, "good": 0, "bad": 0, "good_rating": "0", "reviews": []}
