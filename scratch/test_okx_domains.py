import asyncio
from curl_cffi.requests import AsyncSession

async def main():
    domains = [
        "www.okx.com",
        "okx.com",
        "www.okx.cab",
        "okx.cab",
        "aws.okx.com",
        "ext.okx.com",
        "ext.okx.cab",
        "www.okx.com.ua"
    ]
    
    path = "/api/v5/public/time"
    
    async with AsyncSession(impersonate="chrome120") as session:
        for domain in domains:
            url = f"https://{domain}{path}"
            print(f"Testing: {url}")
            try:
                resp = await session.get(url, timeout=5)
                print(f"  Status Code: {resp.status_code}")
                print(f"  Response: {resp.text[:150]}")
            except Exception as e:
                print(f"  Failed: {e}")

if __name__ == '__main__':
    asyncio.run(main())
