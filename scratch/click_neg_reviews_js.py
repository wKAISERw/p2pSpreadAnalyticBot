import asyncio
import sqlite3
import json
import logging
from pathlib import Path
from playwright.async_api import async_playwright

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("ClickNegReviews")

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
        
    url = "https://www.okx.com/ru/p2p/ads-merchant?publicUserId=0119460a01"
    
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        context = await browser.new_context(
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
            viewport={"width": 1280, "height": 1024}
        )
        await context.add_cookies(playwright_cookies)
        page = await context.new_page()
        
        # Intercept and log all requests
        def handle_request(req):
            if "review/history" in req.url:
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
            logger.info("Triggering reviews tab click via JS...")
            clicked = await page.evaluate('''() => {
                const els = Array.from(document.querySelectorAll('.dashboard-turn'));
                let found = false;
                for (let el of els) {
                    if (el.innerText.includes('Отзывы')) {
                        el.click();
                        if (el.parentElement) el.parentElement.click();
                        found = true;
                    }
                }
                return found;
            }''')
            logger.info(f"JS Click reviews tab result: {clicked}")
            await page.wait_for_timeout(4000)
            
            # Try to find and click the negative filter
            logger.info("Attempting to find and click the negative reviews filter...")
            filter_clicked = await page.evaluate('''() => {
                // Let's dump all button/div text to help debugging
                const allElements = Array.from(document.querySelectorAll('div, span, button, a'));
                const list = [];
                let found = false;
                for (let el of allElements) {
                    const text = (el.innerText || '').trim();
                    if (text && text.length < 50) {
                        list.push(text);
                    }
                    if (text.includes('Отрицательные') || text.includes('Негативные') || text.includes('Negative') || text.includes('Плохие')) {
                        el.click();
                        found = true;
                        // Don't break, try to click if there are nested elements
                    }
                }
                return {found, list: list.slice(0, 100)};
            }''')
            
            logger.info(f"Filter clicked: {filter_clicked['found']}")
            logger.info(f"Some visible element texts: {filter_clicked['list'][:20]}")
            
            await page.wait_for_timeout(5000)
            
            screenshot_path = "C:/Users/user/.gemini/antigravity/brain/8429dec0-2c12-45da-8137-3f456255efe1/okx_neg_reviews_clicked.png"
            await page.screenshot(path=screenshot_path)
            logger.info(f"Saved screenshot to {screenshot_path}")
            
        except Exception as e:
            logger.error(f"Error: {e}")
            
        await context.close()
        await browser.close()

if __name__ == '__main__':
    asyncio.run(main())
