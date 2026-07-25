import asyncio
from playwright.async_api import async_playwright
from playwright_stealth import Stealth

async def main():
    try:
        import sys
        sys.stdout.reconfigure(encoding='utf-8')
    except Exception:
        pass
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        context = await browser.new_context(
            viewport={"width": 1280, "height": 1000},
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
        )
        stealth_plugin = Stealth()
        await stealth_plugin.apply_stealth_async(context)
        page = await context.new_page()
        
        m_id = "sa90446076d7c38cf995068caca25d822"
        url = f"https://c2c.binance.com/uk-UA/advertiserDetail?advertiserNo={m_id}"
        
        print(f"Navigating to {url}...")
        await page.goto(url, wait_until="commit", timeout=60000)
        await page.wait_for_timeout(8000)
        
        # Dump page text
        text_content = await page.evaluate("() => document.body.innerText")
        print("\n=== PAGE INNER TEXT ===")
        print(text_content[:2000])
        print("=======================\n")
        
        # Capture screenshot
        screenshot_path = "C:\\Users\\user\\.gemini\\antigravity\\brain\\8429dec0-2c12-45da-8137-3f456255efe1\\binance_detail.png"
        await page.screenshot(path=screenshot_path)
        print(f"Screenshot saved to {screenshot_path}")
        
        await browser.close()

if __name__ == '__main__':
    asyncio.run(main())
