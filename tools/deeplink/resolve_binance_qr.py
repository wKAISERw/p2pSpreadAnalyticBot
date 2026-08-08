import requests
import json

code = "dplk393b631d811449868289e4123ae90536"

endpoints = [
    ("POST", "https://www.binance.com/bapi/composite/v1/public/qr/code/resolve", {"qrCode": code}),
    ("GET", f"https://www.binance.com/bapi/composite/v1/public/qr/code/detail?qrCode={code}", None),
    ("POST", "https://www.binance.com/bapi/c2c/v1/public/c2c/qr/resolve", {"code": code}),
    ("GET", f"https://www.binance.com/bapi/c2c/v2/friendly/c2c/adv/qr-detail?code={code}", None),
    ("GET", f"https://www.binance.com/bapi/composite/v1/public/qrcode/detail?code={code}", None),
    ("POST", "https://www.binance.com/bapi/composite/v1/friendly/qrcode/parse", {"qrCode": f"https://www.binance.com/uk-UA/qr/{code}"}),
]

headers = {
    'User-Agent': 'Binance/2.80.0 (Android; 14)',
    'Content-Type': 'application/json',
    'Accept-Language': 'uk-UA',
}

for method, url, body in endpoints:
    try:
        if method == "POST":
            r = requests.post(url, json=body, headers=headers, timeout=5)
        else:
            r = requests.get(url, headers=headers, timeout=5)
        print(f"[{r.status_code}] {url}")
        if r.status_code == 200:
            print("  Response:", r.text[:500])
    except Exception as e:
        print(f"Error {url}: {e}")
