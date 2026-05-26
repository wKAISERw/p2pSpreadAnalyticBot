import asyncio
import sqlite3
import json
import logging
from pathlib import Path
from playwright.async_api import async_playwright

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("InspectOKXLoggedIn")

async def main():
    # Load cookies from DB
    conn = sqlite3.connect('data/merchants.db')
    conn.row_factory = sqlite3.Row
    cur = conn.execute("SELECT cookies_json FROM auth_sessions WHERE exchange='OKX' AND is_active=1")
    row = cur.fetchone()
    if not row:
        logger.error("No active OKX session found in DB!")
        return

    cookies_dict = json.loads(row['cookies_json'] or "{}")
    
    # Format cookies for Playwright
    # Playwright cookies list format: [{"name": "...", "value": "...", "domain": ".okx.com", "path": "/"}]
    playwright_cookies = []
    for k, v in cookies_dict.items():
        playwright_cookies.append({
            "name": k,
            "value": v,
            "domain": ".okx.com",
            "path": "/"
        })
        
    logger.info(f"Loaded {len(playwright_cookies)} cookies from DB.")
    
    url = "https://www.okx.com/ru/p2p/ads-merchant?publicUserId=6f9658b1d8"
    
    async with async_playwright() as p:
        # We launch a persistent context or normal context
        browser = await p.chromium.launch(headless=True)
        context = await browser.new_context(
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
            viewport={"width": 1280, "height": 720}
        )
        
        # Add cookies
        await context.add_cookies(playwright_cookies)
        
        page = await context.new_page()
        
        # Monitor all requests
        page.on("request", lambda req: logger.info(f"REQ: {req.url}"))
        
        logger.info(f"Navigating to {url}...")
        try:
            await page.goto(url, wait_until="networkidle", timeout=60000)
            await page.wait_for_timeout(5000)
            
            # Save screenshot to see if logged in and if reviews tab is visible
            screenshot_path = "C:/Users/user/.gemini/antigravity/brain/8429dec0-2c12-45da-8137-3f456255efe1/okx_logged_in.png"
            await page.screenshot(path=screenshot_path)
            logger.info(f"Saved screenshot to {screenshot_path}")
            
            # Print page title
            title = await page.title()
            logger.info(f"Page title: {title}")
            
            # Let's find any tabs on the page
            tabs_text = await page.evaluate('''() => {
                const els = Array.from(document.querySelectorAll('div, span, a, button, li'));
                return els.map(el => el.innerText).filter(text => text && text.length < 50 && (text.includes("отзыв") || text.includes("відгук") || text.includes("review") || text.includes("feedback") || text.includes("Объявлен") || text.includes("Оголошен")));
            }''')
            logger.info(f"Found potential tabs/buttons texts: {set(tabs_text)}")
            
        except Exception as e:
            logger.error(f"Error: {e}")
            
        await context.close()
        await browser.close()

if __name__ == '__main__':
    asyncio.run(main())
