# core/workers/card_sync.py
import asyncio
import logging
import time
import aiohttp
from typing import Optional
from core.utils.crypto import decrypt

logger = logging.getLogger("CardBalanceSync")

class CardBalanceSyncTask:
    def __init__(self, db, interval_seconds: int = 90):
        """
        :param db: MerchantDB object
        :param interval_seconds: How often to sync balances (default 90 seconds to stay safe of Mono's 60s rate limit)
        """
        self.db = db
        self.interval_seconds = interval_seconds
        self._task: Optional[asyncio.Task] = None

    def start(self) -> None:
        if self._task is None:
            self._task = asyncio.create_task(self._run_loop(), name="card_balance_sync_loop")
            logger.info(f"Card Balance Sync Worker started (interval: {self.interval_seconds} seconds).")

    async def stop(self) -> None:
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
            self._task = None
            logger.info("Card Balance Sync Worker stopped.")

    async def _run_loop(self) -> None:
        # Wait 10 seconds after startup to avoid overlapping with bot initialization
        await asyncio.sleep(10)
        while True:
            try:
                await self.sync_balances()
                await asyncio.sleep(self.interval_seconds)
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"Error in Card Balance Sync: {e}", exc_info=True)
                await asyncio.sleep(30)  # retry after 30 seconds on error

    async def sync_balances(self) -> None:
        from state import state
        if not state.stats.get("internet_connected", True):
            logger.debug("Card Balance Sync: Skipped because internet is offline.")
            return

        if not self.db:
            return

        conn = getattr(self.db, "db", None) or getattr(self.db, "_db", None)
        if not conn:
            logger.warning("Card Balance Sync: Database connection is not available.")
            return

        # 1. Fetch active Monobank cards with non-empty credentials
        try:
            async with conn.execute(
                "SELECT id, mono_x_token_encrypted, mono_account_id, last_four FROM cards "
                "WHERE bank_name='monobank' AND status='active' AND mono_x_token_encrypted IS NOT NULL AND mono_account_id IS NOT NULL"
            ) as cur:
                rows = await cur.fetchall()
        except Exception as e:
            logger.error(f"Failed to query active Monobank cards: {e}")
            return

        if not rows:
            logger.debug("Card Balance Sync: No active Monobank cards found.")
            return

        # 2. Group cards by decrypted X-Token to minimize Monobank API requests
        token_to_cards = {}
        for r in rows:
            card_id = r["id"]
            encrypted_token = r["mono_x_token_encrypted"]
            account_id = r["mono_account_id"]
            last_four = r["last_four"]
            
            try:
                token = decrypt(encrypted_token)
                if not token:
                    logger.warning(f"Could not decrypt Monobank token for card *{last_four}")
                    continue
                token_to_cards.setdefault(token, []).append((card_id, account_id, last_four))
            except Exception as e:
                logger.error(f"Failed to decrypt token for card *{last_four}: {e}")

        # 3. Request fresh client-info for each token group
        for token, card_list in token_to_cards.items():
            # Add a small delay between processing different tokens to avoid burst/IP rate limits
            if token != list(token_to_cards.keys())[0]:
                await asyncio.sleep(1.5)

            try:
                url = "https://api.monobank.ua/personal/client-info"
                headers = {"X-Token": token}
                
                async with aiohttp.ClientSession() as session:
                    async with session.get(url, headers=headers, timeout=10) as resp:
                        if resp.status != 200:
                            resp_text = await resp.text()
                            logger.error(
                                f"Monobank API sync error (status {resp.status}) for token: {resp_text[:150]}"
                            )
                            continue
                        data = await resp.json()

                accounts = data.get("accounts", [])
                # Create a map of account_id -> balance
                acc_map = {acc["id"]: float(acc.get("balance", 0)) / 100.0 for acc in accounts if "id" in acc}

                for card_id, account_id, last_four in card_list:
                    if account_id in acc_map:
                        true_balance = acc_map[account_id]
                        await self.db.update_card_balance(card_id, true_balance)
                        logger.info(
                            f"🔄 [Background Card Sync] Card *{last_four} balance updated: {true_balance:.2f} ₴"
                        )
                    else:
                        logger.warning(
                            f"Mono account {account_id} not found in client-info for card *{last_four}"
                        )
            except Exception as e:
                logger.error(f"Failed to sync Monobank balance: {e}")
