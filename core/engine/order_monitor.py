"""
OrderMonitor — фоновий моніторинг статусів P2P-ордерів.

Призначення:
  1. Стежить за Maker-ордерами у стані WAITING_BUYER / WAITING_COUNTERPARTY.
  2. Коли ордер заповнюється (COMPLETED на біржі) → тригерить callback.
  3. Коли ордер зависає >15 хвилин (EXPIRED) → тригерить callback для скасування.
  4. Підтримує паузу оплати (PENDING_PAYMENT) — сповіщає якщо юзер не оплатив вчасно.

Архітектура:
  - Один OrderMonitor може стежити за КІЛЬКОМА угодами паралельно.
  - Кожен "watch" — окреме asyncio.Task.
  - Callbacks: on_filled(trade_id), on_expired(trade_id), on_paid(trade_id).
"""
import asyncio
import logging
import time
from typing import Callable, Awaitable, Optional

from core.storage.merchant_db import MerchantDB
from infrastructure.http.bybit_p2p_client import BybitP2PClient

logger = logging.getLogger("OrderMonitor")

# Маппінг статусів Bybit → внутрішні FSM статуси
BYBIT_STATUS_MAP = {
    "10":  "CREATED",            # Ордер щойно створений
    "20":  "PENDING_PAYMENT",    # Чекаємо оплати покупця
    "30":  "PAID",               # Покупець оплатив, чекаємо підтвердження
    "40":  "COMPLETED",          # Крипта звільнена ✅
    "50":  "CANCELLED",          # Скасовано
    "60":  "APPEAL",             # Апеляція
    "70":  "EXPIRED",            # Таймаут
}

# Маппінг статусів ad/item
BYBIT_AD_STATUS_MAP = {
    "10": "ACTIVE",
    "20": "PAUSED",
    "30": "FILLED",
    "40": "CANCELLED",
}

# FSM: terminal statuses (після яких опитування зупиняється)
TERMINAL_STATUSES = {"COMPLETED", "CANCELLED", "EXPIRED", "FAILED"}

# Callback types
OnFilledCb  = Callable[[int, str, dict], Awaitable[None]]  # (trade_id, order_id, data)
OnExpiredCb = Callable[[int, str], Awaitable[None]]         # (trade_id, order_id)
OnPaidCb    = Callable[[int, str], Awaitable[None]]         # (trade_id, order_id)


class OrderMonitor:
    """
    Фоновий монітор P2P-ордерів.
    Використовує підписаний Bybit API для polling статусів.
    """

    POLL_INTERVAL    = 15.0   # секунди між опитуваннями
    PAYMENT_TIMEOUT  = 900    # 15 хвилин — таймаут для PENDING_PAYMENT
    MAKER_TIMEOUT    = 3600   # 60 хвилин — таймаут для WAITING_BUYER

    def __init__(self, db: MerchantDB):
        self._db     = db
        self._tasks: dict[int, asyncio.Task] = {}  # trade_id → Task

    # ─── Публічний API ──────────────────────────────────────────────────────

    def watch_order(
        self,
        trade_id: int,
        order_id: str,
        exchange: str,
        credentials: dict,
        fsm_status: str = "WAITING_BUYER",   # поточний FSM статус
        on_filled:  Optional[OnFilledCb]  = None,
        on_expired: Optional[OnExpiredCb] = None,
        on_paid:    Optional[OnPaidCb]    = None,
    ) -> asyncio.Task:
        """
        Запускає фоновий моніторинг для конкретного ордера.
        Повертає asyncio.Task для можливості скасування.
        """
        task = asyncio.create_task(
            self._monitor_loop(
                trade_id=trade_id,
                order_id=order_id,
                exchange=exchange,
                credentials=credentials,
                fsm_status=fsm_status,
                on_filled=on_filled,
                on_expired=on_expired,
                on_paid=on_paid,
            ),
            name=f"order_monitor_{trade_id}"
        )
        self._tasks[trade_id] = task
        logger.info(f"[OrderMonitor] Запущено моніторинг: trade#{trade_id}, order={order_id}, exchange={exchange}")
        return task

    def cancel_watch(self, trade_id: int) -> None:
        """Зупиняє моніторинг конкретного ордера."""
        task = self._tasks.pop(trade_id, None)
        if task and not task.done():
            task.cancel()
            logger.info(f"[OrderMonitor] Моніторинг скасовано для trade#{trade_id}")

    def cancel_all(self) -> None:
        """Зупиняє всі активні монітори."""
        for trade_id, task in list(self._tasks.items()):
            if not task.done():
                task.cancel()
        self._tasks.clear()
        logger.info("[OrderMonitor] Всі монітори зупинено.")

    # ─── Внутрішній цикл ────────────────────────────────────────────────────

    async def _monitor_loop(
        self,
        trade_id: int,
        order_id: str,
        exchange: str,
        credentials: dict,
        fsm_status: str,
        on_filled: Optional[OnFilledCb],
        on_expired: Optional[OnExpiredCb],
        on_paid: Optional[OnPaidCb],
    ) -> None:
        started_at = time.time()
        timeout = self.PAYMENT_TIMEOUT if fsm_status == "PENDING_PAYMENT" else self.MAKER_TIMEOUT

        while True:
            try:
                elapsed = time.time() - started_at

                # Таймаут
                if elapsed > timeout:
                    logger.warning(
                        f"[OrderMonitor] trade#{trade_id} EXPIRED після {elapsed:.0f}s"
                    )
                    await self._db.update_active_trade_status(trade_id, "EXPIRED")
                    if on_expired:
                        await on_expired(trade_id, order_id)
                    break

                # Запит статусу з біржі
                status_data = await self._fetch_order_status(exchange, order_id, credentials)
                if not status_data:
                    await asyncio.sleep(self.POLL_INTERVAL)
                    continue

                raw_status = str(status_data.get("orderStatus") or status_data.get("status") or "")
                mapped     = BYBIT_STATUS_MAP.get(raw_status, raw_status)

                logger.debug(f"[OrderMonitor] trade#{trade_id} → Bybit status={raw_status} ({mapped})")

                # Оплачено (перехід до PAID_PENDING_RELEASE)
                if mapped == "PAID" and on_paid:
                    await self._db.update_active_trade_status(trade_id, "PAID_PENDING_RELEASE")
                    await on_paid(trade_id, order_id)

                # Завершено (BUY_COMPLETED або COMPLETED в залежності від ноги)
                elif mapped == "COMPLETED":
                    logger.info(f"[OrderMonitor] trade#{trade_id} COMPLETED ✅")
                    if on_filled:
                        await on_filled(trade_id, order_id, status_data)
                    break

                # Скасовано
                elif mapped in ("CANCELLED", "EXPIRED"):
                    logger.warning(f"[OrderMonitor] trade#{trade_id} {mapped}")
                    await self._db.update_active_trade_status(trade_id, mapped)
                    if on_expired:
                        await on_expired(trade_id, order_id)
                    break

            except asyncio.CancelledError:
                logger.info(f"[OrderMonitor] trade#{trade_id} — CancelledError, зупинено.")
                break
            except Exception as e:
                logger.error(f"[OrderMonitor] trade#{trade_id} помилка: {e}", exc_info=True)

            await asyncio.sleep(self.POLL_INTERVAL)

        self._tasks.pop(trade_id, None)

    async def _fetch_order_status(
        self, exchange: str, order_id: str, credentials: dict
    ) -> dict:
        """Запитуємо статус ордера з відповідної біржі."""
        if exchange == "Bybit":
            client = BybitP2PClient()
            client.set_credentials(
                credentials.get("api_key", ""),
                credentials.get("api_secret", "")
            )
            return await client.get_order_info(order_id)

        # TODO (Phase 2): Binance order status via SessionHijack
        elif exchange == "Binance":
            logger.debug(f"[OrderMonitor] Binance order polling ще не реалізовано для {order_id}")
            return {}

        return {}

    # ─── Відновлення після рестарту ─────────────────────────────────────────

    async def restore_from_db(
        self,
        on_filled:  Optional[OnFilledCb]  = None,
        on_expired: Optional[OnExpiredCb] = None,
        on_paid:    Optional[OnPaidCb]    = None,
    ) -> int:
        """
        Відновлює моніторинг для всіх незавершених ордерів з БД.
        Викликається при старті бота.
        """
        pending_statuses = ("PENDING_PAYMENT", "PAID_PENDING_RELEASE", "WAITING_BUYER", "WAITING_COUNTERPARTY")
        trades = await self._db.get_active_trades_by_status(*pending_statuses)

        if not trades:
            logger.info("[OrderMonitor] Немає незавершених ордерів для відновлення.")
            return 0

        restored = 0
        for trade in trades:
            trade_id  = trade["id"]
            order_id  = trade["order_id"]
            exchange  = trade["exchange"]
            fsm_status = trade["status"]
            owner_id  = trade.get("owner_user_id") or 0

            # Пропускаємо тимчасові placeholder IDs
            if not order_id or order_id.startswith("PENDING_"):
                continue

            creds = await self._db.get_credentials(exchange=exchange, user_id=owner_id) or {}

            self.watch_order(
                trade_id=trade_id,
                order_id=order_id,
                exchange=exchange,
                credentials=creds,
                fsm_status=fsm_status,
                on_filled=on_filled,
                on_expired=on_expired,
                on_paid=on_paid,
            )
            restored += 1

        logger.info(f"[OrderMonitor] Відновлено {restored} моніторів після рестарту.")
        return restored
