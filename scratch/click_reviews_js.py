import asyncio
import sqlite3
import json
import logging
from pathlib import Path
from playwright.async_api import async_playwright

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("ClickReviewsJS")

async def main():
    conn = sqlite3.connect('data/merchants.db')
    conn.row_factory = sqlite3.Row
    cur = conn.execute("SELECT cookies_json FROM auth_sessions WHERE exchange='OKX' AND is_active=1")
    row = cur.fetchone()
    if not row:
        logger.error("No OKX session found!")
        return

    cookies_dict = json.loads(row['cookies_json'] or "{}")
    playwright_cookies = [{"name": k, "value": v, "domain": ".okx.com", "path": "/"} for k, v in cookies_dict.items()]
        
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
            if "okx.com" in req.url:
                logger.info(f"🎯 REQUEST URL: {req.url}")
                if "review" in req.url or "feedback" in req.url or "history" in req.url:
                    logger.info(f"🔥 FOUND TARGET REQ: {req.url}")
                    logger.info(f"  Method: {req.method}")
                    logger.info(f"  Headers: {dict(req.headers)}")
                    if req.post_data:
                        logger.info(f"  Payload: {req.post_data}")
                
        page.on("request", handle_request)
        
        logger.info(f"Navigating to {url}...")
        try:
            await page.goto(url, wait_until="networkidle", timeout=60000)
            await page.wait_for_timeout(3000)
            
            # Click reviews tab via JS
            logger.info("Triggering click via JS...")
            clicked = await page.evaluate('''() => {
                const els = Array.from(document.querySelectorAll('.dashboard-turn'));
                let found = false;
                for (let el of els) {
                    if (el.innerText.includes('Отзывы')) {
                        el.click();
                        // Also try clicking its parent just in case
                        if (el.parentElement) el.parentElement.click();
                        found = true;
                    }
                }
                return found;
            }''')
            logger.info(f"JS Click result: {clicked}")
            
            await page.wait_for_timeout(5000)
            
            # Take screenshot to verify tab transition
            screenshot_path = "C:/Users/user/.gemini/antigravity/brain/8429dec0-2c12-45da-8137-3f456255efe1/okx_reviews_clicked_js.png"
            await page.screenshot(path=screenshot_path)
            logger.info(f"Saved screenshot to {screenshot_path}")
            
        except Exception as e:
            logger.error(f"Error: {e}")
            
        await context.close()
        await browser.close()

if __name__ == '__main__':
    asyncio.run(main())
