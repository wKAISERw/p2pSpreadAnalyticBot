# core/engine/credentials.py
import logging
from dataclasses import dataclass

from core.storage.merchant_db import MerchantDB
from infrastructure.api.binance_account import BinanceAccountClient
from infrastructure.api.bybit_account import BybitAccountClient
from infrastructure.api.mexc_account import MEXCAccountClient
from infrastructure.api.okx_account import OKXAccountClient

from infrastructure.http.binance_client import BinanceClient
from infrastructure.http.bybit_p2p_client import BybitP2PClient
from infrastructure.http.okx_client import OkxClient
from infrastructure.http.wallet_client import WalletClient

logger = logging.getLogger("Scanner.Credentials")


@dataclass
class AccountClients:
    bybit: BybitAccountClient
    binance: BinanceAccountClient
    okx: OKXAccountClient
    mexc: MEXCAccountClient

    def as_dict(self) -> dict:
        return {
            "Bybit": self.bybit, "Binance": self.binance,
            "OKX": self.okx, "MEXC": self.mexc,
        }


async def load_credentials(db: MerchantDB, user_id: int = 0) -> AccountClients:
    """
    Завантажує зашифровані credentials з БД і ініціалізує account клієнтів.
    Повертає AccountClients — навіть якщо credentials немає (порожні клієнти).
    """
    creds = await db.get_all_credentials(user_id=user_id)

    bybit_acc = BybitAccountClient()
    binance_acc = BinanceAccountClient()
    okx_acc = OKXAccountClient()
    mexc_acc = MEXCAccountClient()

    if "Bybit" in creds:
        bybit_acc.set_credentials(creds["Bybit"]["api_key"], creds["Bybit"]["api_secret"])
        logger.info("✅ Bybit API credentials завантажено")
    if "Binance" in creds:
        binance_acc.set_credentials(creds["Binance"]["api_key"], creds["Binance"]["api_secret"])
        logger.info("✅ Binance API credentials завантажено")
    if "OKX" in creds:
        okx_acc.set_credentials(
            creds["OKX"]["api_key"], creds["OKX"]["api_secret"],
            creds["OKX"].get("passphrase", ""),
        )
        logger.info("✅ OKX API credentials завантажено")
    if "MEXC" in creds:
        mexc_acc.set_credentials(creds["MEXC"]["api_key"], creds["MEXC"]["api_secret"])
        logger.info("✅ MEXC API credentials завантажено")

    return AccountClients(bybit_acc, binance_acc, okx_acc, mexc_acc)


def bind_http_credentials(
        creds: dict,
        b_client: BybitP2PClient,
        bn_client: BinanceClient,
        o_client: OkxClient,
        w_client: WalletClient = None,
) -> None:
    """Прив'язує ті самі credentials до HTTP клієнтів (для ReviewFetcher)."""
    if "Bybit" in creds:
        b_client.set_credentials(creds["Bybit"]["api_key"], creds["Bybit"]["api_secret"])
    if "Binance" in creds:
        bn_client.set_credentials(creds["Binance"]["api_key"], creds["Binance"]["api_secret"])
    if "OKX" in creds:
        o_client.set_credentials(
            creds["OKX"]["api_key"], creds["OKX"]["api_secret"],
            creds["OKX"].get("passphrase", ""),
        )
    if "Wallet" in creds and w_client:
        w_client.set_credentials(creds["Wallet"]["api_key"])
