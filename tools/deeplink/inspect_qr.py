import requests

url = 'https://www.binance.com/uk-UA/qr/dplk393b631d811449868289e4123ae90536'
headers = {
    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36',
    'Accept-Language': 'uk-UA,uk;q=0.9,en-US;q=0.8,en;q=0.7',
    'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8',
}

session = requests.Session()
r = session.get(url, headers=headers, allow_redirects=True)
print('Status code:', r.status_code)
print('Final URL:', r.url)
print('Response length:', len(r.text))
print('Cookies:', session.cookies.get_dict())
print('Headers:', r.headers)

if r.text:
    import re
    print('\n=== Relevant URLs in HTML ===')
    urls = re.findall(r'(?:bnc|https?|intent)://[^\s"\'<>]+', r.text)
    for u in set(urls):
        if any(k in u for k in ['bnc', 'dplk', 'advertiser', 'p2p', 'user', 'fiat', 'qr', 'webview', 'schema', 'app.binance']):
            print('  ', u[:150])
            
    # Search for JSON data inside page
    scripts = re.findall(r'<script[^>]*>(.*?)</script>', r.text, re.DOTALL)
    print(f'\nTotal scripts: {len(scripts)}')
    for i, s in enumerate(scripts):
        if any(k in s for k in ['dplk', 'advertiser', 'p2p', 'bnc://', 'schema', 'deeplink']):
            print(f'\n--- Script {i} ---')
            print(s[:1000])
