"""
startup.py — Блок 6: Відновлення роботи після рестарту.

При старті бота викликайте `await recover_all(db, executor, notify_cb)`.
Бот автоматично:
    1. Відновить усі незавершені торгові угоди (PENDING_PAYMENT, PAID_PENDING_RELEASE, SELL_PENDING).
    2. Перезапустить AdRepricer для всіх сесій зі статусом SELL_IN_PROGRESS.
    3. Сповістить у Telegram про кількість відновлених процесів.
"""
import asyncio
import logging
from typing import Callable, Awaitable, Optional

from core.storage.merchant_db import MerchantDB
from core.engine.ad_repricer import AdRepricer
from core.engine.route_executor import RouteExecutor
from core.engine.order_monitor import OrderMonitor
from infrastructure.http.bybit_p2p_client import BybitP2PClient
from core.utils.tasks import spawn

logger = logging.getLogger("Startup")

NotifyCallback = Optional[Callable[[str], Awaitable[None]]]


async def recover_pending_trades(
    db: MerchantDB,
    notify_cb: NotifyCallback = None,
) -> int:
    """
    Блок 6.1: Відновлення незавершених угод після рестарту.

    Шукає активні угоди в статусах:
        - PENDING_PAYMENT     — очікують оплати від юзера
        - PAID_PENDING_RELEASE — оплачено, очікуємо надходження крипти
        - SELL_PENDING        — чекають запуску другої ноги

    Для кожної такої угоди — логуємо і (майбутнє) відновлюємо FSM-обробник.
    """
    pending_statuses = ("PENDING_PAYMENT", "PAID_PENDING_RELEASE", "SELL_PENDING")
    trades = await db.get_active_trades_by_status(*pending_statuses)

    if not trades:
        logger.info("[Startup] Незавершених угод не знайдено.")
        return 0

    logger.warning(
        f"[Startup] ♻️ Знайдено {len(trades)} незавершених угод: "
        f"{[t.get('id') for t in trades]}"
    )

    for trade in trades:
        logger.info(
            f"[Startup] Угода #{trade.get('id')} | Exchange={trade.get('exchange')} | "
            f"Status={trade.get('status')} | OrderId={trade.get('order_id')} | "
            f"Session={trade.get('session_id')}"
        )
        # TODO (Phase 2): Переприв'язати FSM-обробник до відновленої угоди.

    if notify_cb:
        await notify_cb(
            f"♻️ Відновлення після рестарту:\n"
            f"Знайдено {len(trades)} незавершених угод.\n"
            f"ID сесій: {list({t.get('session_id') for t in trades})}\n\n"
            f"Перевірте статус через /trades та завершіть вручну якщо потрібно."
        )

    return len(trades)


async def recover_active_repricers(
    db: MerchantDB,
    executor: RouteExecutor,
    notify_cb: NotifyCallback = None,
) -> int:
    """
    Блок 6.2: Відновлення AdRepricer-ів для Maker-Sell угод.

    Шукає торгові сесії в статусі SELL_IN_PROGRESS та перезапускає
    AdRepricer.watch() для кожної з них.
    """
    sessions = await db.get_active_repricer_sessions()

    if not sessions:
        logger.info("[Startup] Активних Maker-Sell сесій не знайдено.")
        return 0

    logger.warning(
        f"[Startup] ♻️ Знайдено {len(sessions)} активних Maker-Sell сесій для відновлення."
    )

    repricers_started = 0

    for session in sessions:
        session_id  = session.get("id")
        sell_ad_id  = session.get("ad_id")
        exchange    = session.get("exchange")
        buy_price   = float(session.get("buy_price")   or 0)
        amount      = float(session.get("amount")       or 100)
        network_fee = float(session.get("network_fee") or 0)

        if not sell_ad_id or not exchange:
            logger.warning(f"[Startup] Сесія #{session_id}: відсутній ad_id або exchange, пропускаємо.")
            continue

        logger.info(
            f"[Startup] Відновлення AdRepricer для сесії #{session_id}: "
            f"Exchange={exchange}, Ad={sell_ad_id}, BuyPrice={buy_price}"
        )

        owner_user_id = session.get("owner_user_id") or 0
        creds = await db.get_credentials(exchange=exchange, user_id=owner_user_id) or {}

        repricer = AdRepricer(
            session_id=session_id,
            sell_ad_id=sell_ad_id,
            exchange=exchange,
            buy_price=buy_price,
            amount_usdt=amount,
            network_fee=network_fee,
            notify_cb=notify_cb,
        )

        # ─────────────────────────────────────────────────────────────────────
        # ФІКС #2: Closure Bug — дефолтний аргумент фіксує поточне значення
        # змінних у кожній ітерації циклу, а не захоплює останнє.
        # БЕЗ фіксу: всі 5 репрайсерів отримають API-ключі від ОСТАННЬОЇ сесії.
        # ─────────────────────────────────────────────────────────────────────
        async def _make_fetch_book_top(exc: str, ad: str, _exc=exchange) -> Optional[float]:
            try:
                if _exc == "Bybit":
                    client = BybitP2PClient()
                    async with client:
                        return await client.fetch_p2p_book_top(
                            fiat="UAH", asset="USDT", side=0, exclude_ad_id=ad
                        )
            except Exception as e:
                logger.debug(f"[Startup] fetch_book_top error: {e}")
            return None

        async def _make_update_ad_price(exc: str, ad: str, price: float, _creds=creds) -> bool:
            # ФІКС #2: _creds=creds захоплює поточне значення creds (не останнє з циклу)
            return await executor.update_maker_ad_price(exc, ad, price, _creds)

        spawn(
            repricer.watch(
                fetch_book_top=_make_fetch_book_top,
                update_ad_price=_make_update_ad_price,
                db=db,
            ),
            f"repricer_session_{session_id}",
            logger_=logger,
        )

        repricers_started += 1

    if repricers_started > 0 and notify_cb:
        await notify_cb(
            f"♻️ Відновлено {repricers_started} AdRepricer(ів) після рестарту.\n"
            f"Сесії: {[s.get('id') for s in sessions[:repricers_started]]}"
        )

    return repricers_started


async def recover_all(
    db: MerchantDB,
    executor: RouteExecutor,
    notify_cb: NotifyCallback = None,
    order_monitor: Optional[OrderMonitor] = None,
    on_order_filled=None,
    on_order_expired=None,
) -> dict:
    """
    Головна функція відновлення. Викликати при старті бота.
    Повертає: {"pending_trades": N, "repricers": N, "monitors": N}
    """
    logger.info("[Startup] Починаю відновлення після рестарту...")

    pending   = await recover_pending_trades(db, notify_cb=notify_cb)
    repricers = await recover_active_repricers(db, executor, notify_cb=notify_cb)

    monitors = 0
    if order_monitor:
        monitors = await order_monitor.restore_from_db(
            on_filled=on_order_filled,
            on_expired=on_order_expired,
        )

    logger.info(
        f"[Startup] Відновлення завершено: "
        f"{pending} угод | {repricers} репрайсерів | {monitors} моніторів"
    )

    return {"pending_trades": pending, "repricers": repricers, "monitors": monitors}
