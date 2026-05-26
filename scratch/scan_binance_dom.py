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
        
        # Click the (...) button if it exists
        print("Looking for (...) button...")
        # Let's find elements that might be the (...) button or have a similar class/SVG
        # The SVG or button is next to "Сер. час оплати"
        await page.evaluate('''() => {
            // Find all buttons, SVGs, divs near the end of stats
            const els = Array.from(document.querySelectorAll('div, button, svg, span'));
            // Let's print or click the element next to stats
            // We can search for elements that are clickable and don't have text
        }''')
        
        # Let's search the entire DOM for the target keywords
        matches = await page.evaluate('''() => {
            const keywords = ["відгуки", "отзывы", "review", "feedback"];
            const allElements = Array.from(document.getElementsByTagName('*'));
            const results = [];
            for (let el of allElements) {
                // Check inner text
                const text = el.innerText || "";
                if (keywords.some(k => text.toLowerCase().includes(k))) {
                    // Check if it has direct text or child nodes
                    const directText = Array.from(el.childNodes)
                        .filter(node => node.nodeType === Node.TEXT_NODE)
                        .map(node => node.nodeValue.trim())
                        .join(" ");
                    
                    results.push({
                        tagName: el.tagName,
                        id: el.id,
                        className: el.className,
                        directText: directText.substring(0, 100),
                        innerText: text.substring(0, 100),
                        visible: el.offsetWidth > 0 && el.offsetHeight > 0,
                        width: el.offsetWidth,
                        height: el.offsetHeight
                    });
                }
            }
            return results;
        }''')
        
        print(f"Found {len(matches)} DOM elements containing keywords:")
        for i, m in enumerate(matches[:30]):
            print(f"[{i}] {m['tagName']} (id={m['id']}, class={m['className']}) - DirectText: {repr(m['directText'])}, InnerText: {repr(m['innerText'])}, Visible: {m['visible']} ({m['width']}x{m['height']})")
            
        await browser.close()

if __name__ == '__main__':
    asyncio.run(main())
