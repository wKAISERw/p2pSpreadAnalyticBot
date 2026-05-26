import asyncio
import sqlite3
import json
import logging
from pathlib import Path
from playwright.async_api import async_playwright

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("FindTabs")

async def main():
    conn = sqlite3.connect('data/merchants.db')
    conn.row_factory = sqlite3.Row
    cur = conn.execute("SELECT cookies_json FROM auth_sessions WHERE exchange='OKX' AND is_active=1")
    row = cur.fetchone()
    if not row:
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
        
        await page.goto(url, wait_until="networkidle", timeout=60000)
        await page.wait_for_timeout(3000)
        
        # Log elements matching "Отзывы"
        elements = await page.evaluate('''() => {
            const els = Array.from(document.querySelectorAll('*'));
            return els.map(el => {
                if (el.innerText === 'Отзывы') {
                    return {
                        tagName: el.tagName,
                        className: el.className,
                        id: el.id,
                        outerHTML: el.outerHTML.substring(0, 200)
                    };
                }
                return null;
            }).filter(x => x !== null);
        }''')
        
        logger.info(f"Elements matching 'Отзывы': {json.dumps(elements, indent=2)}")
        
        await context.close()
        await browser.close()

if __name__ == '__main__':
    asyncio.run(main())
