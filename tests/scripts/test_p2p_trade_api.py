import asyncio
import logging
import sys
import os
from dotenv import load_dotenv

# Завантажуємо .env файл у змінні середовища, щоб crypto.py міг знайти ENCRYPTION_KEY
load_dotenv()

# Дозволяємо імпорт модулів з корня проекту
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# Зчитуємо конфігурацію, щоб підвантажились ключі для шифрування
from config import settings

from core.storage.merchant_db import MerchantDB
from infrastructure.http.binance_client import BinanceClient
from infrastructure.http.bybit_p2p_client import BybitP2PClient

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("TestTradeAPI")

async def test_binance(db):
    creds = await db.get_all_credentials()
    if "Binance" not in creds:
        logger.error("No Binance credentials found.")
        return
        
    client = BinanceClient()
    client.set_credentials(creds["Binance"]["api_key"], creds["Binance"]["api_secret"])
    
    url = "https://p2p.binance.com/bapi/c2c/v2/private/c2c/order-match/create"
    payload = {
        "advOrderNumber": "1234567890123456",
        "tradeType": "BUY",
        "asset": "USDT",
        "fiatUnit": "UAH",
        "buyType": "BY_AMOUNT",
        "buyAmount": "100"
    }

    try:
        await client.__aenter__()
        
        logger.info("[Binance] Testing Taker Order Creation...")
        resp = await client._session.post(url, json=payload, headers={"X-MBX-APIKEY": creds["Binance"]["api_key"]})
        data = resp.json()
        logger.info(f"Binance API Key Auth Response: {data}")
    except Exception as e:
        logger.error(f"Binance test failed: {e}")

async def test_bybit(db):
    creds = await db.get_all_credentials()
    if "Bybit" not in creds:
        logger.error("No Bybit credentials found.")
        return
        
    client = BybitP2PClient()
    client.set_credentials(creds["Bybit"]["api_key"], creds["Bybit"]["api_secret"])
    
    logger.info("[Bybit] Testing Web API Taker Order Creation...")
    url = "https://api2.bybit.com/fiat/otc/order/openapi/create" 
    payload = {
        "itemId": "123456",
        "amount": "100",
        "clientId": "dummy123"
    }
    
    try:
        await client.__aenter__()
        
        import json
        payload_str = json.dumps(payload)
        signed_headers = client._sign_headers(payload_str) 
        
        resp = await client._session.post(url, json=payload, headers=signed_headers)
        logger.info(f"Bybit Create Order Response: {resp.text}")
    except Exception as e:
        logger.error(f"Bybit test failed: {e}")

async def test_bybit_item_list(db):
    creds = await db.get_all_credentials()
    if "Bybit" not in creds:
        logger.error("No Bybit credentials found for item list test.")
        return
        
    client = BybitP2PClient()
    client.set_credentials(creds["Bybit"]["api_key"], creds["Bybit"]["api_secret"])
    
    logger.info("[Bybit] Testing Web API GET /fiat/otc/item/online (My Ads)...")
    url = "https://api2.bybit.com/fiat/otc/item/online" 
    
    try:
        await client.__aenter__()
        
        # Запит не має payload для GET, але якщо Bybit вимагає POST:
        # url = "https://api2.bybit.com/fiat/otc/item/online"  (is typically POST for V3)
        # Змінимо на GET, якщо потрібно
        signed_headers = client._sign_headers("") 
        
        resp = await client._session.post(url, headers=signed_headers)
        logger.info(f"Bybit Active Ads Response: {resp.text}")
    except Exception as e:
        logger.error(f"Bybit active ads test failed: {e}")

async def main():
    db = MerchantDB()
    await db.start()
    await test_binance(db)
    await test_bybit(db)
    await test_bybit_item_list(db)
    await db.stop()

if __name__ == "__main__":
    asyncio.run(main())
