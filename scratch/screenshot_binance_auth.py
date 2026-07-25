import asyncio
import json
import sqlite3
from playwright.async_api import async_playwright
from playwright_stealth import Stealth

async def main():
    try:
        import sys
        sys.stdout.reconfigure(encoding='utf-8')
    except Exception:
        pass

    # Read session from DB
    conn = sqlite3.connect('data/merchants.db')
    conn.row_factory = sqlite3.Row
    cur = conn.execute("SELECT headers_json, cookies_json FROM auth_sessions WHERE exchange='Binance' AND is_active=1")
    row = cur.fetchone()
    if not row:
        print("No active Binance session found in DB!")
        return

    headers = json.loads(row['headers_json'] or "{}")
    cookies = json.loads(row['cookies_json'] or "{}")
    conn.close()

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        context = await browser.new_context(
            viewport={"width": 1280, "height": 1000},
            user_agent=headers.get("user-agent", "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36")
        )
        
        # Add cookies to context
        playwright_cookies = []
        for name, value in cookies.items():
            playwright_cookies.append({
                "name": name,
                "value": value,
                "domain": ".binance.com", # default to main domain
                "path": "/"
            })
        await context.add_cookies(playwright_cookies)
        
        stealth_plugin = Stealth()
        await stealth_plugin.apply_stealth_async(context)
        page = await context.new_page()
        
        # Set extra headers
        req_headers = {
            "accept": "application/json",
            "referer": "https://c2c.binance.com/",
        }
        for k in ("csrftoken", "bnc-uuid", "fvideo-id", "fvideo-token"):
            v = headers.get(k) or headers.get(k.upper())
            if v:
                req_headers[k] = v
        await page.set_extra_http_headers(req_headers)
        
        m_id = "sa90446076d7c38cf995068caca25d822"
        url = f"https://c2c.binance.com/uk-UA/advertiserDetail?advertiserNo={m_id}"
        
        print(f"Navigating to {url} with auth session...")
        await page.goto(url, wait_until="commit", timeout=60000)
        await page.wait_for_timeout(8000)
        
        # Dump page text
        text_content = await page.evaluate("() => document.body.innerText")
        print("\n=== AUTH PAGE INNER TEXT ===")
        print(text_content[:2000])
        print("=======================\n")
        
        # Capture screenshot
        screenshot_path = "C:\\Users\\user\\.gemini\\antigravity\\brain\\8429dec0-2c12-45da-8137-3f456255efe1\\binance_detail_auth.png"
        await page.screenshot(path=screenshot_path)
        print(f"Screenshot saved to {screenshot_path}")
        
        await browser.close()

if __name__ == '__main__':
    asyncio.run(main())
