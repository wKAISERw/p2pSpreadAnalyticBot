# core/engine/maker_ad_monitor.py
"""
MakerAdMonitor — фоновий моніторинг вхідних P2P-ордерів на мейкер-оголошення.

Призначення:
  1. Polling Bybit API (get_pending_orders) кожні ~15 секунд.
  2. Детектить НОВІ вхідні замовлення (яких ще не було).
  3. Підтягує профіль контрагента (merchant_id, stats, відгуки).
  4. Запускає RiskEngine.analyze_for_spread() для LLM-аналізу.
  5. Відправляє TG-нотифікацію з ріск-бейджами та кнопками Прийняти/Відхилити.

Архітектура:
  - Один MakerAdMonitor на всю систему (multi-user).
  - Per-user polling task: dict[int, asyncio.Task].
  - Кожен user може мати кілька ad_ids.
"""
from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from decimal import Decimal
from typing import Callable, Awaitable, Optional

from exchanges.base import Order
from infrastructure.http.bybit_p2p_client import BybitP2PClient
from core.storage.merchant_db import MerchantDB

logger = logging.getLogger("MakerAdMonitor")

# Callback type: (chat_id, order_data, counterparty_order, exchange)
MakerNotifyCb = Callable[[int, dict, Order, str], Awaitable[None]]


@dataclass
class UserWatch:
    """Дані для моніторингу одного юзера."""
    user_id: int
    chat_id: int
    exchange: str
    credentials: dict
    ad_ids: set[str]      # ID оголошень що моніторимо
    task: Optional[asyncio.Task] = None


class MakerAdMonitor:
    """
    Фоновий монітор вхідних ордерів на мейкер-оголошення.
    Polling Bybit API для кожного юзера з активними оголошеннями.
    """

    POLL_INTERVAL = 15.0   # секунди між опитуваннями
    MAX_ORDERS_CACHE = 500  # макс. кеш order_id

    def __init__(
        self,
        db: MerchantDB,
        risk_engine=None,
        notify_cb: Optional[MakerNotifyCb] = None,
    ):
        self._db = db
        self._risk_engine = risk_engine
        self._notify_cb = notify_cb
        self._watches: dict[int, UserWatch] = {}   # user_id → UserWatch
        self._seen_order_ids: set[str] = set()      # глобальний кеш побачених ордерів

    # ─── Публічний API ──────────────────────────────────────────────────────

    def set_notify_callback(self, cb: MakerNotifyCb) -> None:
        """Встановлює callback для TG-нотифікацій."""
        self._notify_cb = cb

    async def start_watching(
        self,
        user_id: int,
        chat_id: int,
        exchange: str,
        ad_id: str,
    ) -> None:
        """
        Додає oголошення для моніторингу.
        Якщо юзер вже має активний polling task — просто додаємо ad_id.
        """
        if user_id in self._watches:
            watch = self._watches[user_id]
            watch.ad_ids.add(ad_id)
            logger.info(
                "[MakerAdMonitor] Додано ad %s для user %d (всього: %d)",
                ad_id, user_id, len(watch.ad_ids),
            )
            return

        # Завантажуємо credentials з БД
        creds = await self._db.get_credentials(exchange=exchange, user_id=user_id) or {}
        if not creds.get("api_key"):
            # Fallback: single-user credentials (user_id=0)
            creds = await self._db.get_credentials(exchange=exchange, user_id=0) or {}

        if not creds.get("api_key"):
            logger.warning(
                "[MakerAdMonitor] Немає credentials для %s user %d — не можу моніторити",
                exchange, user_id,
            )
            return

        watch = UserWatch(
            user_id=user_id,
            chat_id=chat_id,
            exchange=exchange,
            credentials=creds,
            ad_ids={ad_id},
        )
        watch.task = asyncio.create_task(
            self._poll_loop(watch),
            name=f"maker_monitor_{user_id}",
        )
        self._watches[user_id] = watch
        logger.info(
            "[MakerAdMonitor] Запущено моніторинг для user %d | exchange=%s | ad=%s",
            user_id, exchange, ad_id,
        )

    def remove_ad(self, user_id: int, ad_id: str) -> None:
        """Прибирає оголошення з моніторингу."""
        watch = self._watches.get(user_id)
        if not watch:
            return
        watch.ad_ids.discard(ad_id)
        if not watch.ad_ids:
            # Немає більше оголошень — зупиняємо polling
            self._stop_watch(user_id)

    def stop_all(self) -> None:
        """Зупиняє всі активні монітори."""
        for user_id in list(self._watches.keys()):
            self._stop_watch(user_id)
        self._watches.clear()
        logger.info("[MakerAdMonitor] Всі монітори зупинено.")

    def _stop_watch(self, user_id: int) -> None:
        watch = self._watches.pop(user_id, None)
        if watch and watch.task and not watch.task.done():
            watch.task.cancel()
            logger.info("[MakerAdMonitor] Зупинено монітор для user %d", user_id)

    # ─── Внутрішній цикл ────────────────────────────────────────────────────

    async def _poll_loop(self, watch: UserWatch) -> None:
        """Основний polling цикл для одного юзера."""
        client = BybitP2PClient()
        client.set_credentials(
            watch.credentials.get("api_key", ""),
            watch.credentials.get("api_secret", ""),
        )

        while True:
            try:
                async with client:
                    orders = await client.get_pending_orders()

                if not orders:
                    await asyncio.sleep(self.POLL_INTERVAL)
                    continue

                for order_data in orders:
                    order_id = str(order_data.get("id") or order_data.get("orderId") or "")
                    if not order_id:
                        continue

                    # Вже бачили — пропускаємо
                    if order_id in self._seen_order_ids:
                        continue

                    # Фільтруємо тільки наші оголошення
                    item_id = str(order_data.get("itemId") or order_data.get("adId") or "")
                    if item_id and watch.ad_ids and item_id not in watch.ad_ids:
                        continue

                    # Визначаємо сторону: якщо ми Maker (продавець), хтось відкрив BUY
                    # orderStatus "20" = PENDING_PAYMENT в Bybit
                    status = str(order_data.get("orderStatus") or "")
                    # Беремо тільки свіжі ордери (CREATED або PENDING_PAYMENT)
                    if status not in ("10", "20", ""):
                        # Ордер вже в процесі або завершений
                        self._seen_order_ids.add(order_id)
                        continue

                    self._seen_order_ids.add(order_id)

                    # Обмежуємо розмір кешу
                    if len(self._seen_order_ids) > self.MAX_ORDERS_CACHE:
                        # Видаляємо найстаріші (set не ordered, але для safety)
                        excess = len(self._seen_order_ids) - self.MAX_ORDERS_CACHE
                        for _ in range(excess):
                            self._seen_order_ids.pop()

                    logger.info(
                        "[MakerAdMonitor] 🔔 Новий вхідний ордер! user=%d order=%s item=%s",
                        watch.user_id, order_id, item_id,
                    )

                    # Обробляємо асинхронно
                    asyncio.create_task(
                        self._process_incoming_order(watch, order_data, order_id),
                        name=f"maker_process_{order_id}",
                    )

            except asyncio.CancelledError:
                logger.info("[MakerAdMonitor] Polling для user %d зупинено.", watch.user_id)
                break
            except Exception as e:
                logger.error(
                    "[MakerAdMonitor] Помилка polling user %d: %s",
                    watch.user_id, e, exc_info=True,
                )

            await asyncio.sleep(self.POLL_INTERVAL)

    async def _process_incoming_order(
        self,
        watch: UserWatch,
        order_data: dict,
        order_id: str,
    ) -> None:
        """Обробляє новий вхідний ордер: аналіз контрагента → TG-нотифікація."""
        try:
            # Дістаємо інфу про контрагента
            counterparty_id = str(
                order_data.get("targetUserId")
                or order_data.get("userId")
                or order_data.get("oppositeUserId")
                or ""
            )
            counterparty_name = str(
                order_data.get("targetNickName")
                or order_data.get("nickName")
                or order_data.get("makerNickName")
                or "Unknown"
            )

            # Суми
            price = float(order_data.get("price") or order_data.get("unitPrice") or 0)
            amount = float(order_data.get("amount") or order_data.get("quantity") or 0)
            total_fiat = float(order_data.get("totalPrice") or order_data.get("orderAmount") or 0)
            if total_fiat == 0 and price > 0 and amount > 0:
                total_fiat = price * amount

            # Будуємо synthetic Order для RiskEngine
            counterparty_order = Order(
                id=order_id,
                price=Decimal(str(price)) if price > 0 else Decimal("0"),
                available_amount=Decimal(str(amount)),
                min_limit=Decimal("0"),
                max_limit=Decimal(str(total_fiat)),
                merchant_id=counterparty_id,
                merchant_name=counterparty_name,
                month_order_count=int(order_data.get("recentOrderNum") or 0),
                finish_rate_pct=float(order_data.get("recentExecuteRate") or 0.0),
                exchange=watch.exchange,
                link=f"https://www.bybit.com/uk-UA/p2p/profile/{counterparty_id}/USDT/UAH/item",
                trade_terms="",
                side="buy",  # контрагент купує у нас
            )

            # Спробуємо дістати більше деталей про контрагента через профіль
            if counterparty_id and watch.exchange == "Bybit":
                try:
                    client = BybitP2PClient()
                    client.set_credentials(
                        watch.credentials.get("api_key", ""),
                        watch.credentials.get("api_secret", ""),
                    )
                    async with client:
                        profile = await client.fetch_merchant_profile(counterparty_id)
                    if profile:
                        counterparty_order.month_order_count = int(
                            profile.get("recentOrderNum") or counterparty_order.month_order_count
                        )
                        counterparty_order.finish_rate_pct = float(
                            profile.get("recentExecuteRate") or counterparty_order.finish_rate_pct
                        )
                        counterparty_order.merchant_name = str(
                            profile.get("nickName") or counterparty_order.merchant_name
                        )
                        counterparty_order.is_verified = bool(
                            profile.get("authTag") or profile.get("isVerified")
                        )
                except Exception as e:
                    logger.debug("[MakerAdMonitor] Не вдалось завантажити профіль %s: %s", counterparty_id, e)

            # LLM-аналіз контрагента через RiskEngine
            if self._risk_engine:
                try:
                    await self._risk_engine.analyze_for_spread([counterparty_order])
                except Exception as e:
                    logger.warning("[MakerAdMonitor] RiskEngine analyze error: %s", e)

            # LLM вердикт з БД
            rec = "PENDING"
            reason = ""
            if self._db and counterparty_id:
                try:
                    rec, _, reason, _, _ = await self._db.get_trade_recommendation_full(
                        watch.exchange, counterparty_id
                    )
                except Exception:
                    pass

            # Відправляємо TG-нотифікацію
            if self._notify_cb:
                enriched = {
                    "order_id": order_id,
                    "item_id": str(order_data.get("itemId") or ""),
                    "price": price,
                    "amount_usdt": amount,
                    "total_fiat": total_fiat,
                    "counterparty_id": counterparty_id,
                    "counterparty_name": counterparty_name,
                    "rec": rec,
                    "reason": reason,
                    "status": str(order_data.get("orderStatus") or ""),
                }
                try:
                    await self._notify_cb(
                        watch.chat_id, enriched, counterparty_order, watch.exchange,
                    )
                except Exception as e:
                    logger.error("[MakerAdMonitor] Notify callback error: %s", e)

        except Exception as e:
            logger.error(
                "[MakerAdMonitor] process_incoming_order error [%s]: %s",
                order_id, e, exc_info=True,
            )

    # ─── Seed (завантаження вже відомих order_id при старті) ─────────────────

    async def seed_seen_orders(self) -> int:
        """Завантажує вже відомі order_id з БД щоб не спамити при рестарті."""
        try:
            trades = await self._db.get_active_trades_by_status(
                "PENDING_PAYMENT", "PAID_PENDING_RELEASE",
                "WAITING_BUYER", "WAITING_COUNTERPARTY",
                "COMPLETED", "CANCELLED",
            )
            if trades:
                for t in trades:
                    oid = t.get("order_id", "")
                    if oid and not oid.startswith("PENDING_"):
                        self._seen_order_ids.add(oid)
                logger.info(
                    "[MakerAdMonitor] Seeded %d known order IDs from DB.",
                    len(self._seen_order_ids),
                )
                return len(self._seen_order_ids)
        except Exception as e:
            logger.warning("[MakerAdMonitor] seed_seen_orders error: %s", e)
        return 0


