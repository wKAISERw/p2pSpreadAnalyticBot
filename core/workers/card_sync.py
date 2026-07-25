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

                        # 🚀 Отримуємо попередній баланс та дані картки
                        old_bal = None
                        owner_id = None
                        label = f"Картка *{last_four}"
                        try:
                            async with conn.execute("SELECT balance, owner_id, label FROM cards WHERE id=?", (card_id,)) as c_cur:
                                c_row = await c_cur.fetchone()
                                if c_row:
                                    old_bal = float(c_row["balance"]) if c_row["balance"] is not None else None
                                    owner_id = c_row["owner_id"]
                                    if c_row["label"]:
                                        label = c_row["label"]
                        except Exception as c_err:
                            logger.debug(f"Failed to fetch old card balance: {c_err}")

                        await self.db.update_card_balance(card_id, true_balance)
                        logger.info(
                            f"🔄 [Background Card Sync] Card *{last_four} balance updated: {true_balance:.2f} ₴"
                        )

                        # 🚀 Якщо баланс збільшився — надсилаємо сповіщення Трекера коштів!
                        if old_bal is not None and true_balance > old_bal + 0.01 and owner_id:
                            delta = true_balance - old_bal
                            await self._notify_mono_income(token, account_id, card_id, delta, true_balance, owner_id, label)

                        # Trigger Buy Mode Auto-scaler check
                        try:
                            if owner_id:
                                from core.engine.taker_scanner import trigger_buy_autoscale_check
                                await trigger_buy_autoscale_check(self.db, owner_id)
                        except Exception as auto_err:
                            logger.debug(f"Card sync autoscale trigger error: {auto_err}")
                    else:
                        logger.warning(
                            f"Mono account {account_id} not found in client-info for card *{last_four}"
                        )
            except Exception as e:
                logger.error(f"Failed to sync Monobank balance: {e}")

    async def _notify_mono_income(self, token: str, account_id: str, card_id: str, delta: float, new_balance: float, owner_id: int, label: str):
        """Отримує деталі транзакції з Monobank Statement API та надсилає сповіщення в Telegram."""
        try:
            from_ts = int(time.time()) - 600
            url = f"https://api.monobank.ua/personal/statement/{account_id}/{from_ts}"
            headers = {"X-Token": token}
            matching_item = None
            async with aiohttp.ClientSession() as session:
                async with session.get(url, headers=headers, timeout=10) as resp:
                    if resp.status == 200:
                        items = await resp.json()
                        if isinstance(items, list):
                            for it in items:
                                amt = float(it.get("amount", 0)) / 100.0
                                if amt > 0 and abs(amt - delta) < 1.0:
                                    matching_item = it
                                    break
                            if not matching_item and items:
                                matching_item = items[0]

            from bot.handlers.core import _bot
            if _bot and owner_id:
                sender = matching_item.get("counterName") or matching_item.get("description") or "Зарахування коштів" if matching_item else "Зарахування коштів"
                comment = matching_item.get("comment") if matching_item else ""
                
                lines = ["🐈 <b>Monobank — Нова транзакція!</b>\n"]
                lines.append(f"💰 <b>Сума:</b> 🟢 +{delta:,.2f} ₴")
                lines.append(f"👤 <b>Відправник/Опис:</b> {sender}")
                if comment:
                    lines.append(f"💬 <b>Коментар:</b> <i>{comment}</i>")
                from datetime import datetime
                time_str = datetime.now().strftime("%d.%m.%Y %H:%M:%S")
                if matching_item and matching_item.get("time"):
                    time_str = datetime.fromtimestamp(matching_item.get("time")).strftime("%d.%m.%Y %H:%M:%S")
                lines.append(f"⏰ <b>Час:</b> {time_str}")
                lines.append(f"💳 <b>Картка:</b> {label}")
                lines.append(f"📊 <b>Новий залишок:</b> {new_balance:,.2f} ₴")
                
                # Перевіряємо чи є співпадаючий ордер
                order_leg = await self.db.find_pending_order_for_card(card_id, delta) if hasattr(self.db, "find_pending_order_for_card") else None
                if order_leg:
                    lines.append(f"\n🔗 <b>✅ Співпадає з P2P ордером #{order_leg.get('order_id', order_leg.get('id', ''))}!</b>")
                
                await _bot.send_message(chat_id=owner_id, text="\n".join(lines), parse_mode="HTML")
                logger.info(f"✅ Sent Monobank money tracker notification for card {label}: +{delta} UAH to user {owner_id}")
        except Exception as e:
            logger.error(f"Failed to process Monobank money tracker notification: {e}")
