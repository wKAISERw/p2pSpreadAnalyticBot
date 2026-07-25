import asyncio
import sqlite3
import json
import logging
from pathlib import Path
from playwright.async_api import async_playwright

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("ClickOKXReviews")

async def main():
    conn = sqlite3.connect('data/merchants.db')
    conn.row_factory = sqlite3.Row
    cur = conn.execute("SELECT cookies_json FROM auth_sessions WHERE exchange='OKX' AND is_active=1")
    row = cur.fetchone()
    if not row:
        logger.error("No active OKX session found!")
        return

    cookies_dict = json.loads(row['cookies_json'] or "{}")
    playwright_cookies = []
    for k, v in cookies_dict.items():
        playwright_cookies.append({
            "name": k,
            "value": v,
            "domain": ".okx.com",
            "path": "/"
        })
        
    url = "https://www.okx.com/ru/p2p/ads-merchant?publicUserId=6f9658b1d8"
    
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        context = await browser.new_context(
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
            viewport={"width": 1280, "height": 720}
        )
        await context.add_cookies(playwright_cookies)
        page = await context.new_page()
        
        # Intercept and log all requests
        def handle_request(req):
            # Print any OKX API request
            if "okx.com" in req.url and ("/api/" in req.url or "/priapi/" in req.url or "/v3/" in req.url):
                logger.info(f"🎯 API REQ: {req.url}")
                
        page.on("request", handle_request)
        
        logger.info(f"Navigating to {url}...")
        try:
            await page.goto(url, wait_until="networkidle", timeout=60000)
            await page.wait_for_timeout(3000)
            
            # Click reviews tab
            logger.info("Clicking reviews tab...")
            # We can find element with text "Отзывы"
            reviews_tab = page.locator("text=Отзывы").first
            await reviews_tab.click()
            logger.info("Clicked!")
            
            await page.wait_for_timeout(5000)
            
            # Save screenshot of reviews section
            screenshot_path = "C:/Users/user/.gemini/antigravity/brain/8429dec0-2c12-45da-8137-3f456255efe1/okx_reviews_tab.png"
            await page.screenshot(path=screenshot_path)
            logger.info(f"Saved reviews tab screenshot to {screenshot_path}")
            
        except Exception as e:
            logger.error(f"Error: {e}")
            
        await context.close()
        await browser.close()

if __name__ == '__main__':
    asyncio.run(main())
