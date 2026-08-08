import json
import sqlite3
import time

# Parse full curl text provided by user in terminal prompt
curl_raw = """curl 'https://c2c.binance.com/bapi/c2c/v1/private/c2c/user/profile' \
  -H 'accept: */*' \
  -H 'accept-language: uk,en;q=0.9,en-GB;q=0.8,en-US;q=0.7' \
  -H 'bnc-level: 0' \
  -H 'bnc-location: UA' \
  -H 'bnc-time-zone: Europe/Kiev' \
  -H 'bnc-uuid: 658da99f-8159-4b2e-9cc6-105bf8ab30db' \
  -H 'c2ctype: c2c_web' \
  -H 'clienttype: web' \
  -H 'content-type: application/json' \
  -b 'bnc-uuid=658da99f-8159-4b2e-9cc6-105bf8ab30db; BNC_FV_KEY=33d8f1eee2afafa3b099cc9835dbac9b68fe2f53; lang=uk-UA; se_gd=AIJFRXxsRBWURNRIIDldgZZU1BQwQBZW1VUZcVERVVUWgW1NWVQU1; se_gsd=eysnPzxlJjU0GRExJDY7IyovFwIEAAUOV1lFWlVWVFVSCVNT1; BNC-Location=UA; neo-theme=dark_midnight; changeBasisTimeZone=; _gcl_au=1.1.2072860411.1780081011; fiat-prefer-currency=UAH; common_fiat=%7B%22fiat%22%3A%22UAH%22%7D; AMP_ae7b5071dc=JTdCJTIyZGV2aWNlSWQlMjIlM0ElMjI4NjM2YzFmZC1iNmFjLTQzYzUtYWVlZS05OWVlMzcwZmExNTklMjIlMkMlMjJzZXNzaW9uSWQlMjIlM0ExNzgwNzYwNjg5NTEzJTJDJTIyb3B0T3V0JTIyJTNBZmFsc2UlMkMlMjJsYXN0RXZlbnRUaW1lJTIyJTNBMTc4MDc2NjMwNzQ2NiU3RA==; currentAccount=; isAccountsLoggedIn=y; _gid=GA1.2.756233811.1782903900; registerChannel=SEO_page; theme=dark; logined=y; userPreferredCurrency=USD_USD; se_sd=AAGCAWAEMBNVQ8HIECQMgZZClABUIEVWlUOBcW0FlVdWwF1NWWcG1; s9r1=BFCD46E6F0B5F2313574683E658DCD9B; r20t=web.1227783036.3C429ADF027DCB7E9892BA0A15086AA5; r30t=1; cr00=D73053C4B99FB0055A5F1CE8C9B975AD; d1og=web.1227783036.B33EDAE2B64EA76A5B2EE1D488629677; r2o1=web.1227783036.5E68E2965042AB85311C77BA94D80C82; f30l=web.1227783036.AA36503CCE96C6BD7FAACD8CDA51C866; p20t=web.1227783036.6CFA7A5C961DCBCD744A4F0F285128FD; sensorsdata2015jssdkcross=%7B%22distinct_id%22%3A%221227783036%22%2C%22first_id%22%3A%2219c912d33ff18f4-0af76e7a10b72b-4c657b58-1821369-19c912d34003465%22%2C%22props%22%3A%7B%22%24latest_traffic_source_type%22%3A%22%E7%9B%B4%E6%8E%A5%E6%B5%81%E9%87%8F%22%2C%22%24latest_search_keyword%22%3A%22%E6%9C%AA%E5%8F%96%E5%88%B0%E5%80%BC_%E7%9B%B4%E6%8E%A5%E6%89%93%E5%BC%80%22%2C%22%24latest_referrer%22%3A%22%22%7D%2C%22identities%22%3A%22eyIkaWRlbnRpdHlfY29va2llX2lkIjoiMTljOTEyZDMzZmYxOGY0LTBhZjc2ZTdhMTBiNzJiLTRjNjU3YjU4LTE4MjEzNjktMTljOTEyZDM0MDAzNDY1IiwiJGlkZW50aXR5X2xvZ2luX2lkIjoiMTIyNzc4MzAzNiJ9%22%2C%22history_login_id%22%3A%7B%22name%22%3A%22%24identity_login_id%22%2C%22value%22%3A%221227783036%22%7D%2C%22%24device_id%22%3A%2219d5dacb39b3b3-0fcaf953edbd478-4c657b58-1821369-19d5dacb39c3057%22%7D; AMP_MKTG_ae7b5071dc=JTdCJTdE; _uetsid=11f9d880860a11f1a2879579a2b2b361; _uetvid=22abdab011b911f196caabdf24f611d3; BNC_FV_KEY_T=101-adMSzKH%2B7DFtFUtlTzw%2BIi18FpXRgGWWlEYAByViXIjvLpqvtpf1mmLKywnYjvpB%2FI09E8WdT05qoW40RSXxhQ%3D%3D-ZGFpgnwAzNFG4XqFIEkw8w%3D%3D-69; BNC_FV_KEY_EXPIRE=1785028759570; _h_desk_key=c96a85a67a054038812df4d85ce19ec4; _gat_UA-162512367-1=1; _ga=GA1.1.1343164175.1771962185; _ga_3WP50LGEEC=GS2.1.s1785007156$o189$g1$t1785007167$j49$l0$h0; OptanonConsent=isGpcEnabled=0&datestamp=Sat+Jul+25+2026+22%3A19%3A28+GMT%2B0300+(%D0%B7%D0%B0+%D1%81%D1%85%D1%96%D0%B4%D0%BD%D0%BE%D1%94%D0%B2%D1%80%D0%BE%D0%BF%D0%B5%D0%B9%D1%81%D1%8C%D0%BA%D0%B8%D0%BC+%D0%BB%D1%96%D1%82%D0%BD%D1%96%D0%BC+%D1%87%D0%B0%D1%81%D0%BE%D0%BC)&version=202604.2.0&browserGpcFlag=0&isIABGlobal=false&hosts=&consentId=b354e7f8-bd07-43e1-a12b-e5338531bfb7&interactionCount=1&isAnonUser=1&landingPath=NotLandingPage&groups=C0001%3A1%2CC0003%3A1%2CC0004%3A1%2CC0002%3A1&AwaitingReconsent=false&isDntEnabled=0&prevHadToken=0; AMP_ae7b5071dc=JTdCJTIyZGV2aWNlSWQlMjIlM0ElMjI8NjM2YzFmZC1iNmFjLTQzYzUtYWVlZS05OWVlMzcwZmExNTklMjIlMkMlMjJzZXNzaW9uSWQlMjIlM0ExNzg1MDA3MTY5MjA2JTJDJTIyb3B0T3V0JTIyJTNBZmFsc2UlN0Q=' \
  -H 'csrftoken: 0703c7bc87d1289d83f38fb62db88c8f' \
  -H 'device-info: eyJzY3JlZW5fcmVzb2x1dGlvbiI6IjE3MDcsMTA2NyIsImF2YWlsYWJsZV9zY3JlZW5fcmVzb2x1dGlvbiI6IjE3MDcsMTAxOSIsInN5c3RlbV92ZXJzaW9uIjoiV2luZG93cyAxMCIsImJyYW5kX21vZGVsIjoidW5rbm93biIsInN5c3RlbV9sYW5nIjoidWsiLCJ0aW1lem9uZSI6IkdNVCswMzowMCIsInRpbWV6b25lT2Zmc2V0IjotMTgwLCJ1c2VyX2FnZW50IjoiTW96aWxsYS81LjAgKFdpbmRvd3MgTlQgMTAuMDsgV2luNjQ7IHg2NCkgQXBwbGVXZWJLaXQvNTM3LjM2IChLSFRNTCwgbGlrZSBHZWNrbykgQ2hyb21lLzE1MC4wLjAuMCBTYWZhcmkvNTM3LjM2IEVkZy8xNTAuMC4wLjAiLCJsaXN0X3BsdWdpbiI6IlBERiBWaWV3ZXIsQ2hyb21lIFBERiBWaWV3ZXIsQ2hyb21pdW0gUERGIFZpZXdlcixNaWNyb3NvZnQgRWRnZSBQREYgVmlld2VyLFdlYktpdCBidWlsdC1pbiBQREYiLCJjYW52YXNfY29kZSI6IjA2Y2VmMjU3Iiwid2ViZ2xfdmVuZG9yIjoiR29vZ2xlIEluYy4gKEludGVsKSIsIndlYmdsX3JlbmRlcmVyIjoiQU5HTEUgKEludGVsLCBJbnRlbChSKSBHcmFwaGljcyAoMHgwMDAwN0Q2NykgRGlyZWN0M0QxMSB2c181XzAgcHNfNV8wLCBEM0QxMSkiLCJhdWRpbyI6IjEyNC4wNDM0NzUyNzUxNjA3NCIsInBsYXRmb3JtIjoiV2luMzIiLCJ3ZWJfdGltZXpvbmUiOiJFdXJvcGUvS2lldiIsImRldmljZV9uYW1lIjoiQ2hyb21lIFYxNTAuMC4wLjAgKFdpbmRvd3MpIiwiZmluZ2VycHJpbnQiOiIzOWQ2MGVmN2E0ZWY5NWYyMWY4MjA5NGYwNTk2OTI4MyIsImRldmljZV9pZCI6IiIsInJlbGF0ZWRfZGV2aWNlX2lkcyI6IiJ9' \
  -H 'fvideo-id: 33d8f1eee2afafa3b099cc9835dbac9b68fe2f53' \
  -H 'fvideo-token: rwScZwSv/i0+99CnmAIUfqFlCqKUiMCERw26mupeVCya0xOe4+/QMPGdnPJszGRdoXaKJRJEd733tep/8Acj18rcHxxwMjiZ1+TONV0H3MZgwcyF3v6LRKuCMCEYvvmFov8mx+jfLgrnFN4xrbFT7I6iq8BWKRqgkzfsjRa/CTSYn753d8utt6jji2xYGrOyU=04' \
  -H 'lang: uk-UA' \
  -H 'user-agent: Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/150.0.0.0 Safari/537.36 Edg/150.0.0.0'
"""

from tools.deeplink.import_curl import parse_curl

headers, cookies = parse_curl(curl_raw)
print(f"Parsed {len(headers)} headers and {len(cookies)} cookies.")
print("Key cookies:", {k: v[:15] + "..." for k, v in cookies.items() if k in ["p20t", "cr00", "d1og", "bnc-uuid"]})

# Insert into SQLite auth_sessions for user 1115620363
conn = sqlite3.connect("data/merchants.db")
c = conn.cursor()
c.execute("""
    INSERT OR REPLACE INTO auth_sessions (user_id, exchange, headers_json, cookies_json, updated_at, is_active)
    VALUES (?, ?, ?, ?, ?, 1)
""", (1115620363, "Binance", json.dumps(headers), json.dumps(cookies), time.time()))
conn.commit()
conn.close()

print("Successfully saved full browser session to data/merchants.db!")
