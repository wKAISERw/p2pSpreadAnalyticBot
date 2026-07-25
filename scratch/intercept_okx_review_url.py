import asyncio
import logging
from pathlib import Path
from playwright.async_api import async_playwright

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("InterceptOKX")

async def main():
    user_data_dir = Path("data/browser_profiles/okx")
    logger.info(f"Using browser profile directory: {user_data_dir}")
    
    # OKX merchant URL
    url = "https://www.okx.com/ru/p2p/ads-merchant?publicUserId=6f9658b1d8"
    
    async with async_playwright() as p:
        context = await p.chromium.launch_persistent_context(
            user_data_dir=str(user_data_dir),
            headless=True,
            args=[
                "--disable-blink-features=AutomationControlled",
                "--disable-http2",
                "--window-size=1280,720",
            ],
        )
        
        page = context.pages[0] if context.pages else await context.new_page()
        
        # Intercept requests
        def handle_request(req):
            if "review" in req.url or "feedback" in req.url or "history" in req.url:
                logger.info(f"🎯 REQUEST URL: {req.url}")
                logger.info(f"  Method: {req.method}")
                logger.info(f"  Headers: {dict(req.headers)}")
                
        page.on("request", handle_request)
        
        logger.info(f"Navigating to {url}...")
        try:
            await page.goto(url, wait_until="commit", timeout=60000)
            await page.wait_for_timeout(5000)
            
            # Click reviews tab using evaluation JS
            logger.info("Attempting to click reviews tab via JS...")
            success = await page.evaluate('''() => {
                const keywords = ["відгуки", "отзывы", "review", "feedback"];
                const els = Array.from(document.querySelectorAll('div, span, a, button, li'));
                let clicked = false;
                for (let el of els) {
                    if (el.innerText && keywords.some(k => el.innerText.toLowerCase().includes(k))) {
                        if (el.offsetWidth > 0 && el.offsetHeight > 0 && el.offsetWidth < 500) {
                            el.click();
                            clicked = true;
                        }
                    }
                }
                return clicked;
            }''')
            logger.info(f"JS click reviews tab result: {success}")
            
            await page.wait_for_timeout(5000)
        except Exception as e:
            logger.error(f"Error during navigation/click: {e}")
            
        await context.close()

if __name__ == '__main__':
    asyncio.run(main())
