import asyncio
import json
from playwright.async_api import async_playwright
from playwright_stealth import Stealth

async def main():
    try:
        import sys
        sys.stdout.reconfigure(encoding='utf-8')
    except Exception:
        pass
    async with async_playwright() as p:
        # Launch browser
        browser = await p.chromium.launch(headless=True)
        context = await browser.new_context(
            viewport={"width": 1280, "height": 800},
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
        )
        
        stealth_plugin = Stealth()
        await stealth_plugin.apply_stealth_async(context)
        
        page = await context.new_page()
        
        m_id = "sa90446076d7c38cf995068caca25d822" # kievbond
        url = f"https://c2c.binance.com/uk-UA/advertiserDetail?advertiserNo={m_id}"
        
        intercepted_requests = []
        
        async def handle_request(request):
            if "review" in request.url or "feedback" in request.url or "list-by-page" in request.url:
                try:
                    post_data = request.post_data
                except Exception:
                    post_data = None
                
                intercepted_requests.append({
                    "url": request.url,
                    "method": request.method,
                    "headers": request.headers,
                    "post_data": post_data
                })
                print(f"\n[Request Intercepted] URL: {request.url}")
                print(f"Method: {request.method}")
                print(f"Post Data: {post_data}")
                
        async def handle_response(response):
            if "review" in response.url or "feedback" in response.url or "list-by-page" in response.url:
                try:
                    body = await response.text()
                    print(f"[Response Intercepted] URL: {response.url}")
                    print(f"Status: {response.status}")
                    print(f"Body: {body[:1000]}")
                except Exception as e:
                    print(f"[Response Error] {e}")

        page.on("request", handle_request)
        page.on("response", handle_response)
        
        print(f"Navigating to {url}...")
        await page.goto(url, wait_until="commit", timeout=60000)
        
        print("Waiting for page load...")
        await page.wait_for_timeout(8000)
        
        print("Listing candidate elements for reviews tab...")
        candidates = await page.evaluate('''() => {
            const keywords = ["відгуки", "отзывы", "review", "feedback"];
            const els = Array.from(document.querySelectorAll('div, span, a, button, li, p'));
            return els.map(el => {
                if (el.innerText && keywords.some(k => el.innerText.toLowerCase().includes(k))) {
                    if (el.offsetWidth > 0 && el.offsetHeight > 0) {
                        return {
                            tagName: el.tagName,
                            text: el.innerText.substring(0, 100),
                            className: el.className,
                            width: el.offsetWidth,
                            height: el.offsetHeight,
                            hasParentFooter: !!el.closest('footer')
                        };
                    }
                }
                return null;
            }).filter(Boolean);
        }''')
        for i, c in enumerate(candidates):
            print(f"[{i}] Tag: {c['tagName']}, Text: {repr(c['text'])}, Class: {c['className']}, Size: {c['width']}x{c['height']}, Footer: {c['hasParentFooter']}")

        print("Attempting to click reviews tab (excluding footer)...")
        success = await page.evaluate('''() => {
            const keywords = ["відгуки", "отзывы", "review", "feedback"];
            const els = Array.from(document.querySelectorAll('div, span, a, button, li, p'));
            for (let el of els) {
                if (el.closest('footer')) continue; // skip footer
                if (el.innerText && keywords.some(k => el.innerText.toLowerCase().includes(k))) {
                    if (el.offsetWidth > 0 && el.offsetHeight > 0 && el.offsetWidth < 400) {
                        el.click();
                        return el.innerText;
                    }
                }
            }
            return null;
        }''')
        print(f"Clicked reviews tab: {success}")
        
        await page.wait_for_timeout(8000)
        
        await browser.close()

if __name__ == '__main__':
    asyncio.run(main())
