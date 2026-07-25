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
        
        # Intercept and log all requests containing "c2c" or "review"
        page.on("request", lambda req: print(f"REQ: {req.url}"))
        
        print(f"Navigating to {url}...")
        await page.goto(url, wait_until="networkidle", timeout=60000)
        await page.wait_for_timeout(5000)
        
        html = await page.content()
        with open("scratch/okx_merchant.html", "w", encoding="utf-8") as f:
            f.write(html)
        print("HTML dumped to scratch/okx_merchant.html")
        
        await context.close()

if __name__ == '__main__':
    asyncio.run(main())
