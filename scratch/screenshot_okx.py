import asyncio
from pathlib import Path
from playwright.async_api import async_playwright

async def main():
    user_data_dir = Path("data/browser_profiles/okx")
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
        
        print(f"Navigating to {url}...")
        try:
            await page.goto(url, wait_until="networkidle", timeout=60000)
            await page.wait_for_timeout(5000)
            
            # Save screenshot to artifacts directory so it can be viewed if needed
            screenshot_path = "C:/Users/user/.gemini/antigravity/brain/8429dec0-2c12-45da-8137-3f456255efe1/okx_screenshot.png"
            await page.screenshot(path=screenshot_path)
            print(f"Screenshot saved to {screenshot_path}")
            
            # Print page title and first 500 characters of text content
            title = await page.title()
            text_content = await page.evaluate("() => document.body.innerText")
            print(f"Page title: {title}")
            print(f"Page text (first 500 chars):\n{text_content[:500]}")
            
        except Exception as e:
            print(f"Error: {e}")
            
        await context.close()

if __name__ == '__main__':
    asyncio.run(main())
