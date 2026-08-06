"""
SingleLegExecutor — Блок A: Гнучка одноногова торгівля.

Кнопки "Купити" / "Продати" в алертах виконують одну ногу незалежно.
Підтримує LLM trade_recommendation:
  - REJECT   → відмова
  - CONDITIONAL → amount * 0.5 + попередження
  - APPROVE  → повний об'єм
"""
import logging
import uuid
import asyncio
from typing import Optional

from core.storage.merchant_db import MerchantDB
from core.engine.route_executor import RouteExecutor
from core.engine.order_monitor import OrderMonitor
from core.utils.tasks import spawn

logger = logging.getLogger("SingleLegExecutor")


class SingleLegExecutor:
    """
    Виконує одну ногу (BUY або SELL) незалежно від повного маршруту.
    Використовується з Telegram-алертів (кнопки "Купити" / "Продати").
    """

    def __init__(self, db: MerchantDB, notifier=None):
        self._db = db
        self._executor = RouteExecutor()
        self._monitor = OrderMonitor(db)
        self._notifier = notifier

    async def execute_single_buy(
        self,
        exchange: str,
        ad_id: str,
        price: float,
        amount_usdt: float,
        merchant_id: str,
        owner_user_id: int = 0,
        payment_method: str = "",
    ) -> dict:
        """
        Виконує одну ногу покупки (Taker-Buy).

        1. Перевіряє trade_recommendation (REJECT → відмова, CONDITIONAL → amount * 0.5)
        2. Створює trade_session(strategy='SINGLE_BUY')
        3. Виконує Taker-Buy через RouteExecutor
        4. Запускає OrderMonitor для відстеження

        Returns:
            {"success": bool, "trade_id": int, "order_id": str, "warning": str, "error": str}
        """
        logger.info(
            f"[SINGLE_BUY] Старт: {exchange} ad={ad_id} price={price} "
            f"amount={amount_usdt} USDT merchant={merchant_id}"
        )

        # 1. Перевірка LLM recommendation
        adjusted_amount, warning = await self._check_recommendation(
            exchange, merchant_id, amount_usdt, "продавець"
        )
        if adjusted_amount is None:
            return {
                "success": False,
                "trade_id": 0,
                "order_id": "",
                "warning": "",
                "error": f"🚫 REJECT: Мерчант {merchant_id} заблоковано LLM",
            }

        fiat_amount = adjusted_amount * price

        # 2. Створення trade_session
        session_id = await self._db.create_trade_session(
            strategy="SINGLE_BUY",
            route_type="SINGLE",
            network="N/A",
            network_fee=0,
            gross_profit=0,
            buy_exchange=exchange,
        )

        # 3. Credentials
        creds = await self._build_credentials(exchange, owner_user_id)

        # 4. Створення active_trade
        trade_id = await self._db.create_active_trade(
            session_id=session_id,
            strategy="SINGLE_BUY",
            leg="BUY",
            route_type="SINGLE",
            network="N/A",
            network_fee=0,
            owner_user_id=owner_user_id,
            exchange=exchange,
            order_id=f"PENDING_SBUY_{uuid.uuid4().hex[:8]}",
            ad_id=ad_id,
            asset="USDT",
            fiat="UAH",
            price=price,
            amount=adjusted_amount,
            fiat_amount=fiat_amount,
            status="PENDING_CREATION",
        )

        # 4.1 Зберігаємо payment_method з алерту
        if payment_method:
            await self._db.update_active_trade_payment_method(trade_id, payment_method)

        await self._db.update_trade_session(
            session_id, "BUY_IN_PROGRESS", buy_leg_id=trade_id
        )

        # 5. Виконання ордеру
        result = await self._executor.execute_taker_order(
            exchange=exchange,
            action="BUY",
            ad_id=ad_id,
            fiat_amount=fiat_amount,
            price=price,
            credentials=creds,
        )

        if not result["success"]:
            logger.error(f"[SINGLE_BUY] ❌ Помилка: {result['error']}")
            await self._db.update_active_trade_status(trade_id, "FAILED")
            await self._db.update_trade_session(session_id, "FAILED")
            return {
                "success": False,
                "trade_id": trade_id,
                "order_id": "",
                "warning": warning,
                "error": result["error"],
            }

        order_id = result["order_id"]
        await self._db.update_active_trade_order_id(trade_id, order_id)
        await self._db.update_active_trade_status(trade_id, "PENDING_PAYMENT")

        # 6. Запуск OrderMonitor
        self._monitor.watch_order(
            trade_id=trade_id,
            order_id=order_id,
            exchange=exchange,
            credentials=creds,
            fsm_status="PENDING_PAYMENT",
            on_filled=self._on_single_filled,
            on_paid=self._on_single_paid,
        )

        logger.info(
            f"[SINGLE_BUY] ✅ Ордер створено: #{trade_id} order={order_id} "
            f"amount={adjusted_amount} USDT"
        )
        return {
            "success": True,
            "trade_id": trade_id,
            "order_id": order_id,
            "warning": warning,
            "error": "",
        }

    async def execute_single_sell(
        self,
        exchange: str,
        ad_id: str,
        price: float,
        amount_usdt: float,
        merchant_id: str,
        owner_user_id: int = 0,
        payment_method: str = "",
    ) -> dict:
        """
        Виконує одну ногу продажу (Taker-Sell).

        Returns:
            {"success": bool, "trade_id": int, "order_id": str, "warning": str, "error": str}
        """
        logger.info(
            f"[SINGLE_SELL] Старт: {exchange} ad={ad_id} price={price} "
            f"amount={amount_usdt} USDT merchant={merchant_id}"
        )

        # 1. Перевірка LLM recommendation
        adjusted_amount, warning = await self._check_recommendation(
            exchange, merchant_id, amount_usdt, "покупець"
        )
        if adjusted_amount is None:
            return {
                "success": False,
                "trade_id": 0,
                "order_id": "",
                "warning": "",
                "error": f"🚫 REJECT: Мерчант {merchant_id} заблоковано LLM",
            }

        fiat_amount = adjusted_amount * price

        # 2. Створення trade_session
        session_id = await self._db.create_trade_session(
            strategy="SINGLE_SELL",
            route_type="SINGLE",
            network="N/A",
            network_fee=0,
            gross_profit=0,
            buy_exchange="",
        )

        # 3. Credentials
        creds = await self._build_credentials(exchange, owner_user_id)

        # 4. Створення active_trade
        trade_id = await self._db.create_active_trade(
            session_id=session_id,
            strategy="SINGLE_SELL",
            leg="SELL",
            route_type="SINGLE",
            network="N/A",
            network_fee=0,
            owner_user_id=owner_user_id,
            exchange=exchange,
            order_id=f"PENDING_SSELL_{uuid.uuid4().hex[:8]}",
            ad_id=ad_id,
            asset="USDT",
            fiat="UAH",
            price=price,
            amount=adjusted_amount,
            fiat_amount=fiat_amount,
            status="PENDING_CREATION",
        )

        # 4.1 Зберігаємо payment_method з алерту
        if payment_method:
            await self._db.update_active_trade_payment_method(trade_id, payment_method)

        await self._db.update_trade_session(
            session_id, "SELL_IN_PROGRESS", sell_leg_id=trade_id
        )

        # 5. Виконання ордеру
        result = await self._executor.execute_taker_order(
            exchange=exchange,
            action="SELL",
            ad_id=ad_id,
            fiat_amount=fiat_amount,
            price=price,
            credentials=creds,
        )

        if not result["success"]:
            logger.error(f"[SINGLE_SELL] ❌ Помилка: {result['error']}")
            await self._db.update_active_trade_status(trade_id, "FAILED")
            await self._db.update_trade_session(session_id, "FAILED")
            return {
                "success": False,
                "trade_id": trade_id,
                "order_id": "",
                "warning": warning,
                "error": result["error"],
            }

        order_id = result["order_id"]
        await self._db.update_active_trade_order_id(trade_id, order_id)
        await self._db.update_active_trade_status(trade_id, "SELL_PENDING")

        # 6. Запуск OrderMonitor
        self._monitor.watch_order(
            trade_id=trade_id,
            order_id=order_id,
            exchange=exchange,
            credentials=creds,
            fsm_status="SELL_PENDING",
            on_filled=self._on_single_filled,
            on_paid=self._on_single_paid,
        )

        logger.info(
            f"[SINGLE_SELL] ✅ Ордер створено: #{trade_id} order={order_id} "
            f"amount={adjusted_amount} USDT"
        )
        return {
            "success": True,
            "trade_id": trade_id,
            "order_id": order_id,
            "warning": warning,
            "error": "",
        }

    # ────────────────────────────────────────────────────────────────────
    # Внутрішні хелпери
    # ────────────────────────────────────────────────────────────────────

    async def _check_recommendation(
        self,
        exchange: str,
        merchant_id: str,
        amount: float,
        role: str,
    ) -> tuple[Optional[float], str]:
        """
        Перевіряє LLM trade_recommendation.

        Returns:
            (adjusted_amount, warning_text)
            adjusted_amount = None → REJECT (відмова)
            warning_text — повідомлення для юзера (порожній якщо APPROVE)
        """
        if not merchant_id:
            return amount, ""

        rec, verdict, reason, _, _ = await self._db.get_trade_recommendation_full(
            exchange, merchant_id
        )

        if rec == "REJECT":
            logger.warning(
                f"[SingleLeg] ⛔ REJECT: {role} {merchant_id} [{exchange}] — {reason}"
            )
            return None, ""

        if rec == "CONDITIONAL":
            reduced = round(amount * 0.5, 2)
            warning = (
                f"⚡ CONDITIONAL: {role} {merchant_id} — LLM рекомендує обережність.\n"
                f"Причина: {reason}\n"
                f"Суму знижено з {amount} → {reduced} USDT"
            )
            logger.warning(f"[SingleLeg] {warning}")
            return reduced, warning

        if rec == "PENDING":
            warning = (
                f"🔍 PENDING: LLM ще не перевірив {role} {merchant_id}. "
                f"Торгуємо повний об'єм з обережністю."
            )
            return amount, warning

        # APPROVE
        return amount, ""

    async def _build_credentials(self, exchange: str, user_id: int) -> dict:
        """
        Універсальний збирач credentials (аналогічно до TradeWorker).
        Binance → session headers+cookies, інші → API key/secret.
        """
        if exchange == "Binance":
            headers, cookies, _ = await self._db.get_auth_session(
                exchange="Binance", user_id=user_id
            )
            if not headers:
                logger.warning(
                    "[SingleLeg] Binance auth session недоступна — запустіть SessionManager"
                )
            return {"headers": headers, "cookies": cookies}
        else:
            return await self._db.get_credentials(exchange=exchange, user_id=user_id) or {}

    async def _on_single_paid(
        self, trade_id: int, order_id: str
    ) -> None:
        """Callback коли контрагент оплатив ордер."""
        logger.info(
            f"[SingleLeg] 💸 Ордер #{trade_id} ({order_id}) PAID (оплачено контрагентом)"
        )
        try:
            trade = await self._db.get_active_trade_by_id(trade_id)
            if trade:
                owner_id = trade.get("owner_user_id") or 0
                exchange = trade.get("exchange") or ""
                amount = trade.get("amount") or 0.0
                price = trade.get("price") or 0.0
                fiat_amount = trade.get("fiat_amount") or 0.0
                leg = trade.get("leg") or ""
                
                if self._notifier:
                    msg = (
                        f"💸 <b>Контрагент оплатив ордер!</b>\n\n"
                        f"Угода: <b>#{trade_id}</b>\n"
                        f"Біржа: <b>{exchange}</b>\n"
                        f"Тип: <b>Taker-{leg.capitalize()}</b>\n"
                        f"Сума: <code>{amount:.2f} USDT</code> за курсом <code>{price:.2f}</code> (<code>{fiat_amount:.2f} ₴</code>)\n"
                        f"ID ордера: <code>{order_id}</code>\n\n"
                        f"👉 Будь ласка, перевірте надходження коштів на картку/рахунок та підтвердіть (звільніть активи)."
                    )
                    spawn(self._notifier._send_with_retry(msg, chat_id=owner_id if owner_id else None),
                          "single-leg-notify", logger_=logger)
        except Exception as e:
            logger.error(f"[SingleLeg] Помилка обробки on_paid: {e}")

    async def _on_single_filled(
        self, trade_id: int, order_id: str, data: dict
    ) -> None:
        """Callback коли ордер завершився на біржі."""
        logger.info(
            f"[SingleLeg] ✅ Ордер #{trade_id} ({order_id}) COMPLETED"
        )
        await self._db.update_active_trade_status(trade_id, "COMPLETED")

        # Знаходимо session_id і закриваємо сесію
        try:
            trades = await self._db.get_active_trades_by_status("COMPLETED")
            for t in trades:
                if t.get("id") == trade_id:
                    sid = t.get("session_id")
                    if sid:
                        await self._db.update_trade_session(sid, "COMPLETED")
                    break
        except Exception as e:
            logger.error(f"[SingleLeg] Помилка оновлення сесії: {e}")

