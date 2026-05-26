"""
Тест ReviewFetcher для Binance, Bybit та OKX (нова browser-session логіка).
"""
import asyncio
import sys
import json
import logging
from core.storage.merchant_db import MerchantDB
from core.workers.review_fetcher import ReviewFetcher
from infrastructure.http.binance_client import BinanceClient
from infrastructure.http.bybit_p2p_client import BybitP2PClient
from infrastructure.http.okx_client import OkxClient

logging.basicConfig(level=logging.DEBUG, format="%(levelname)s:%(name)s:%(message)s")
logger = logging.getLogger("TestFetcher")


async def main():
    try:
        sys.stdout.reconfigure(encoding='utf-8')
    except Exception:
        pass

    db = MerchantDB()
    await db.start()

    async with BinanceClient() as bn_client:
        async with BybitP2PClient() as by_client:
            okx_client = OkxClient()

            fetcher = ReviewFetcher(db=db, binance_client=bn_client, bybit_client=by_client, okx_client=okx_client)
            fetcher.bind_clients(binance=bn_client, bybit=by_client, okx=okx_client)

            # ── Binance ────────────────────────────────────────────────────
            binance_merchant = "sa90446076d7c38cf995068caca25d822"
            print(f"\n{'='*60}")
            print(f"Binance merchant: {binance_merchant}")
            res = await fetcher.fetch_now("Binance", binance_merchant)
            print(f"  Status: {res['status']}")
            print(f"  Pos/Neg: {res['positive']}/{res['negative']}")
            print(f"  Bad texts count: {len(res.get('bad_texts', []))}")
            for bt in res.get('bad_texts', []):
                print(f"    - {repr(bt.get('text', ''))[:80]}")
            if res.get('error_reason'):
                print(f"  Error: {res['error_reason']}")

            # ── Bybit ─────────────────────────────────────────────────────
            bybit_merchant = "12769509"
            print(f"\n{'='*60}")
            print(f"Bybit merchant: {bybit_merchant}")
            res = await fetcher.fetch_now("Bybit", bybit_merchant)
            print(f"  Status: {res['status']}")
            print(f"  Pos/Neg: {res['positive']}/{res['negative']}")
            if res.get('error_reason'):
                print(f"  Error: {res['error_reason']}")

            # ── OKX (з негативними відгуками) ────────────────────────────
            okx_merchant = "0119460a01"  # cerus_rebus: 706 pos, 4 neg
            print(f"\n{'='*60}")
            print(f"OKX merchant: {okx_merchant} (has 4 neg reviews)")
            res = await fetcher.fetch_now("OKX", okx_merchant)
            print(f"  Status: {res['status']}")
            print(f"  Pos/Neg: {res['positive']}/{res['negative']}")
            print(f"  Bad texts count: {len(res.get('bad_texts', []))}")
            for bt in res.get('bad_texts', []):
                print(f"    - {repr(bt.get('text', ''))[:80]}")
            if res.get('error_reason'):
                print(f"  Error: {res['error_reason']}")

            # ── OKX (чистий мерчант) ─────────────────────────────────────
            okx_clean = "005977cb24"  # Tuzik: 103 pos, 0 neg
            print(f"\n{'='*60}")
            print(f"OKX merchant: {okx_clean} (clean — 0 neg)")
            res = await fetcher.fetch_now("OKX", okx_clean)
            print(f"  Status: {res['status']}")
            print(f"  Pos/Neg: {res['positive']}/{res['negative']}")
            if res.get('error_reason'):
                print(f"  Error: {res['error_reason']}")

    await db.stop()
    print("\n\nДО БД ПЕРЕВІРИМО ЗАПИСИ:")
    import sqlite3
    conn = sqlite3.connect('data/merchants.db')
    conn.row_factory = sqlite3.Row
    for exchange, mid in [
        ("Binance", binance_merchant),
        ("Bybit", bybit_merchant),
        ("OKX", okx_merchant),
        ("OKX", okx_clean),
    ]:
        cur = conn.execute(
            "SELECT status, positive_count, negative_count, error_reason FROM merchant_reviews WHERE exchange=? AND merchant_id=?",
            (exchange, mid)
        )
        row = cur.fetchone()
        if row:
            print(f"  [{exchange}] {mid}: status={row['status']}, pos={row['positive_count']}, neg={row['negative_count']}, err={row['error_reason'][:60] if row['error_reason'] else ''}")
        else:
            print(f"  [{exchange}] {mid}: НЕ ЗНАЙДЕНО в БД!")


if __name__ == '__main__':
    asyncio.run(main())
