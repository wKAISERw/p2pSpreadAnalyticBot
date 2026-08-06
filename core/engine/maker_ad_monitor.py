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
from collections import OrderedDict
from dataclasses import dataclass
from decimal import Decimal
from typing import Callable, Awaitable, Optional

from exchanges.base import Order
from infrastructure.http.bybit_p2p_client import BybitP2PClient
from core.storage.merchant_db import MerchantDB
from core.utils.tasks import spawn

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
    # Біржі, для яких реально є клієнт полінгу вхідних ордерів.
    SUPPORTED_EXCHANGES = frozenset({"Bybit"})

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
        # FIFO-кеш побачених ордерів. Саме OrderedDict, а не set: set.pop()
        # викидає ДОВІЛЬНИЙ елемент, тобто міг викинути щойно доданий order_id —
        # і наступний polling через 15с слав по ньому повторну нотифікацію.
        self._seen_order_ids: OrderedDict[str, None] = OrderedDict()
        self._global_poll_task: Optional[asyncio.Task] = None

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
        # Полінг реалізований лише через BybitP2PClient. Раніше сюди пролазила
        # будь-яка біржа, і монітор тихо опитував Bybit чужими ключами —
        # моніторинг «працював», не бачачи жодного ордера.
        if exchange not in self.SUPPORTED_EXCHANGES:
            logger.warning(
                "[MakerAdMonitor] %s не підтримується (є лише %s) — "
                "оголошення %s для user %d моніторитись не буде",
                exchange, ", ".join(sorted(self.SUPPORTED_EXCHANGES)), ad_id, user_id,
            )
            return

        if user_id in self._watches:
            watch = self._watches[user_id]
            if watch.exchange != exchange:
                logger.warning(
                    "[MakerAdMonitor] user %d вже моніторить %s — оголошення %s "
                    "на %s проігноровано (один монітор на юзера)",
                    user_id, watch.exchange, ad_id, exchange,
                )
                return
            watch.ad_ids.add(ad_id)
            logger.info(
                "[MakerAdMonitor] Додано ad %s для user %d (всього: %d)",
                ad_id, user_id, len(watch.ad_ids),
            )
            return

        # Креденшли ТІЛЬКИ цього юзера (для власника — з фолбеком на легасі
        # слот). Раніше тут був безумовний фолбек на user_id=0, тобто монітор
        # опитував Bybit ключами власника від імені чужого юзера.
        creds = await self._db.get_credentials_for_user(exchange, user_id) or {}

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
        self._watches[user_id] = watch
        logger.info(
            "[MakerAdMonitor] Запущено моніторинг для user %d | exchange=%s | ad=%s",
            user_id, exchange, ad_id,
        )
        
        if self._global_poll_task is None or self._global_poll_task.done():
            self._global_poll_task = asyncio.create_task(
                self._global_poll_loop(), name="maker_global_poll"
            )

    def remove_ad(self, user_id: int, ad_id: str) -> None:
        """Прибирає оголошення з моніторингу."""
        watch = self._watches.get(user_id)
        if not watch:
            return
        watch.ad_ids.discard(ad_id)
        if not watch.ad_ids:
            self._watches.pop(user_id, None)
            logger.info("[MakerAdMonitor] Зупинено монітор для user %d", user_id)

    def stop_all(self) -> None:
        """Зупиняє всі активні монітори."""
        self._watches.clear()
        if self._global_poll_task and not self._global_poll_task.done():
            self._global_poll_task.cancel()
        logger.info("[MakerAdMonitor] Всі монітори зупинено.")

    def _mark_seen(self, order_id: str) -> None:
        """Позначає ордер обробленим і витісняє найстаріші записи (FIFO)."""
        self._seen_order_ids[order_id] = None
        self._seen_order_ids.move_to_end(order_id)
        while len(self._seen_order_ids) > self.MAX_ORDERS_CACHE:
            self._seen_order_ids.popitem(last=False)

    # ─── Внутрішній цикл ────────────────────────────────────────────────────

    async def _global_poll_loop(self) -> None:
        """Єдиний глобальний цикл опитування для масштабованості."""
        logger.info("[MakerAdMonitor] Глобальний цикл опитування запущено.")
        while True:
            try:
                if not self._watches:
                    await asyncio.sleep(self.POLL_INTERVAL)
                    continue
                    
                tasks = []
                for watch in list(self._watches.values()):
                    tasks.append(self._poll_user(watch))
                
                await asyncio.gather(*tasks, return_exceptions=True)
                
            except asyncio.CancelledError:
                logger.info("[MakerAdMonitor] Глобальний цикл зупинено.")
                break
            except Exception as e:
                logger.error(f"[MakerAdMonitor] Глобальна помилка циклу: {e}", exc_info=True)
                
            await asyncio.sleep(self.POLL_INTERVAL)

    async def _poll_user(self, watch: UserWatch) -> None:
        """Опитує Bybit API для конкретного юзера (одна ітерація)."""
        from state import state
        if not state.stats.get("internet_connected", True):
            logger.debug("[MakerAdMonitor] Poll skipped: internet is down")
            return

        client = BybitP2PClient()
        client.set_credentials(
            watch.credentials.get("api_key", ""),
            watch.credentials.get("api_secret", ""),
        )

        try:
            async with client:
                orders = await client.get_pending_orders()

            if not orders:
                return

            for order_data in orders:
                order_id = str(order_data.get("id") or order_data.get("orderId") or "")
                if not order_id:
                    continue

                if order_id in self._seen_order_ids:
                    continue

                item_id = str(order_data.get("itemId") or order_data.get("adId") or "")
                if item_id and watch.ad_ids and item_id not in watch.ad_ids:
                    continue

                status = str(order_data.get("orderStatus") or "")
                if status not in ("10", "20", ""):
                    self._mark_seen(order_id)
                    continue

                self._mark_seen(order_id)

                logger.info(
                    "[MakerAdMonitor] 🔔 Новий вхідний ордер! user=%d order=%s item=%s",
                    watch.user_id, order_id, item_id,
                )

                spawn(
                    self._process_incoming_order(watch, order_data, order_id),
                    f"maker_process_{order_id}",
                    logger_=logger,
                )

        except Exception as e:
            logger.error(
                "[MakerAdMonitor] Помилка polling user %d: %s",
                watch.user_id, e
            )

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
                        self._mark_seen(oid)
                logger.info(
                    "[MakerAdMonitor] Seeded %d known order IDs from DB.",
                    len(self._seen_order_ids),
                )
                return len(self._seen_order_ids)
        except Exception as e:
            logger.warning("[MakerAdMonitor] seed_seen_orders error: %s", e)
        return 0


