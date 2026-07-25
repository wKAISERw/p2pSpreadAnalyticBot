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
        
        playwright_cookies = []
        for name, value in cookies.items():
            playwright_cookies.append({
                "name": name,
                "value": value,
                "domain": ".binance.com",
                "path": "/"
            })
        await context.add_cookies(playwright_cookies)
        
        stealth_plugin = Stealth()
        await stealth_plugin.apply_stealth_async(context)
        page = await context.new_page()
        
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
        
        print(f"Navigating to {url}...")
        await page.goto(url, wait_until="commit", timeout=60000)
        await page.wait_for_timeout(8000)
        
        # Scroll down
        print("Scrolling down the page...")
        await page.evaluate("window.scrollTo(0, 800);")
        await page.wait_for_timeout(3000)
        
        # Capture screenshot
        screenshot_path = "C:\\Users\\user\\.gemini\\antigravity\\brain\\8429dec0-2c12-45da-8137-3f456255efe1\\binance_detail_scrolled.png"
        await page.screenshot(path=screenshot_path)
        print(f"Screenshot saved to {screenshot_path}")
        
        # Dump DOM items that are candidate elements now that we scrolled
        candidates = await page.evaluate('''() => {
            const keywords = ["відгук", "отзыв", "review", "feedback"];
            const els = Array.from(document.querySelectorAll('*'));
            return els.map(el => {
                if (el.innerText && keywords.some(k => el.innerText.toLowerCase().includes(k))) {
                    if (el.offsetWidth > 0 && el.offsetHeight > 0 && el.offsetWidth < 500) {
                        return {
                            tagName: el.tagName,
                            text: el.innerText.substring(0, 100),
                            className: el.className
                        };
                    }
                }
                return null;
            }).filter(Boolean);
        }''')
        print(f"Candidate elements after scrolling: {len(candidates)}")
        for i, c in enumerate(candidates[:20]):
            print(f"  [{i}] {c['tagName']} (class={c['className']}): {repr(c['text'])}")
            
        await browser.close()

if __name__ == '__main__':
    asyncio.run(main())
