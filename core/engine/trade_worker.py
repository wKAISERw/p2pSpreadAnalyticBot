import logging
import asyncio
import uuid
from typing import Dict, Any, Optional

from core.storage.merchant_db import MerchantDB
from core.engine.route_executor import RouteExecutor
from core.engine.network_fee_engine import NetworkFeeEngine
from core.engine.ad_repricer import AdRepricer
from infrastructure.http.bybit_p2p_client import BybitP2PClient

logger = logging.getLogger("TradeWorker")


class TradeWorker:
    """
    TradeWorker (Фаза 1: Напівавтомат).

    Відповідає за виконання кроків стратегії (TT, TM, MT, MM).
    1. Перевіряє llm_decision обох контрагентів.
    2. Створює записи trade_session та active_trades у MerchantDB.
    3. Викликає RouteExecutor для створення ордерів (підтримує DRY_RUN).
    4. Оновлює FSM-статуси.
    """

    def __init__(self, db: MerchantDB):
        self._db       = db
        self._executor = RouteExecutor()

    # ════════════════════════════════════════════════════════════════════
    # Стратегія T→T: Taker-Buy + Taker-Sell
    # ════════════════════════════════════════════════════════════════════

    async def execute_tt_route(
        self,
        buy_leg: dict,
        sell_leg: dict,
        amount_usdt: float,
        owner_user_id: Optional[int] = None,
    ) -> bool:
        logger.info(
            f"🚀 Починаю T-T угоду: Купую на {buy_leg['exchange']} по {buy_leg['price']} "
            f"-> Продаю на {sell_leg['exchange']} по {sell_leg['price']} об'єм {amount_usdt}"
        )
        owner_user_id = owner_user_id or 0

        # 1. Фільтрація за LLM-вердиктом
        for adv, exchange, role in [
            (buy_leg.get("merchant_id"), buy_leg["exchange"], "продавець"),
            (sell_leg.get("merchant_id"), sell_leg["exchange"], "покупець"),
        ]:
            if adv:
                rec = await self._db.get_trade_recommendation(exchange, adv)
                if rec == "REJECT":
                    logger.warning(f"⚠️ Скасування TT: {role} {adv} заблоковано LLM (REJECT).")
                    return False

        # 2. Network Fee
        network, network_fee_usdt = NetworkFeeEngine.get_optimal_network(
            buy_leg["exchange"], sell_leg["exchange"]
        )
        if network_fee_usdt >= 999.0:
            logger.error(f"❌ Немає спільних мереж між {buy_leg['exchange']} та {sell_leg['exchange']}.")
            return False

        all_options = NetworkFeeEngine.get_all_options(buy_leg["exchange"], sell_leg["exchange"])
        options_str = " | ".join(f"{n}={f:.2f}$" for n, f in all_options)
        amount_uah  = amount_usdt * buy_leg["price"]
        expected_profit = NetworkFeeEngine.calc_profit(
            amount_usdt, buy_leg["price"], sell_leg["price"], network_fee_usdt
        )
        logger.info(
            f"📊 Оцінка TT: Мережа={network} (fee={network_fee_usdt} USDT). "
            f"Альтернативи: [{options_str}]. Очікуваний профіт = {expected_profit:.2f} UAH"
        )
        if expected_profit <= 0:
            logger.warning(f"⚠️ Відмова від TT: нульовий або від'ємний профіт ({expected_profit}).")
            return False

        # 3. Створення сесії в БД
        # ФІКС #1: передаємо buy_exchange щоб MT/MM другі ноги знали звідки витягувати
        route_type = "CROSS" if network_fee_usdt > 0 else "INTRA"
        session_id = await self._db.create_trade_session(
            strategy="TT", route_type=route_type, network=network,
            network_fee=network_fee_usdt, gross_profit=expected_profit,
            buy_exchange=buy_leg["exchange"],
        )

        buy_creds  = await self._build_credentials(buy_leg["exchange"], owner_user_id)
        sell_creds = await self._build_credentials(sell_leg["exchange"], owner_user_id)

        # 4. Нога 1 (BUY)
        logger.info("👉 Записую Leg 1 (BUY) у БД...")
        buy_trade_id = await self._db.create_active_trade(
            session_id=session_id, strategy="TT", leg="BUY", route_type=route_type,
            network=network, network_fee=0, owner_user_id=owner_user_id,
            exchange=buy_leg["exchange"], order_id=f"PENDING_BUY_{uuid.uuid4().hex[:8]}",
            ad_id=buy_leg["ad_id"], asset="USDT", fiat="UAH",
            price=buy_leg["price"], amount=amount_usdt, fiat_amount=amount_uah,
            status="PENDING_CREATION",
        )
        await self._db.update_trade_session(session_id, "BUY_IN_PROGRESS", buy_leg_id=buy_trade_id)

        buy_result = await self._executor.execute_taker_order(
            exchange=buy_leg["exchange"], action="BUY", ad_id=buy_leg["ad_id"],
            fiat_amount=amount_uah, price=buy_leg["price"], credentials=buy_creds,
        )
        if not buy_result["success"]:
            logger.error(f"❌ Помилка Ноги 1 (BUY): {buy_result['error']}")
            await self._db.update_active_trade_status(buy_trade_id, "FAILED")
            await self._db.update_trade_session(session_id, "FAILED")
            return False

        logger.info(f"✅ Успіх Лег 1 (BUY). ID ордера: {buy_result['order_id']}")
        await self._db.update_active_trade_order_id(buy_trade_id, buy_result["order_id"])
        await self._db.update_active_trade_status(buy_trade_id, "PENDING_PAYMENT")
        # TODO: тут FSM очікує підтвердження оплати. Наразі для DRY_RUN — симулюємо миттєво.
        await self._db.update_active_trade_status(buy_trade_id, "BUY_COMPLETED")

        # 5. Нога 2 (SELL)
        logger.info("👉 Записую Leg 2 (SELL) у БД...")
        sell_trade_id = await self._db.create_active_trade(
            session_id=session_id, strategy="TT", leg="SELL", route_type=route_type,
            network=network, network_fee=network_fee_usdt, owner_user_id=owner_user_id,
            exchange=sell_leg["exchange"], order_id=f"PENDING_SELL_{uuid.uuid4().hex[:8]}",
            ad_id=sell_leg["ad_id"], asset="USDT", fiat="UAH",
            price=sell_leg["price"], amount=amount_usdt - network_fee_usdt,
            fiat_amount=(amount_usdt - network_fee_usdt) * sell_leg["price"],
            status="SELL_PENDING",
        )
        await self._db.update_trade_session(session_id, "SELL_IN_PROGRESS", sell_leg_id=sell_trade_id)

        sell_result = await self._executor.execute_taker_order(
            exchange=sell_leg["exchange"], action="SELL", ad_id=sell_leg["ad_id"],
            fiat_amount=(amount_usdt - network_fee_usdt) * sell_leg["price"],
            price=sell_leg["price"], credentials=sell_creds,
        )
        if not sell_result["success"]:
            logger.error(f"❌ Помилка Ноги 2 (SELL): {sell_result['error']}")
            await self._db.update_active_trade_status(sell_trade_id, "FAILED")
            await self._db.update_trade_session(session_id, "FAILED")
            return False

        logger.info(f"✅ Успіх Лег 2 (SELL). ID ордера: {sell_result['order_id']}")
        await self._db.update_active_trade_order_id(sell_trade_id, sell_result["order_id"])
        await self._db.update_active_trade_status(sell_trade_id, "COMPLETED")
        await self._db.update_trade_session(session_id, "COMPLETED")
        logger.info("🎉 TT Маршрут завершено успішно (БД оновлено)!")
        return True

    # ════════════════════════════════════════════════════════════════════
    # Стратегія T→M: Taker-Buy + Maker-Sell (Блок 7)
    # ════════════════════════════════════════════════════════════════════

    async def execute_tm_route(
        self,
        buy_leg: dict,
        sell_exchange: str,
        amount_usdt: float,
        min_margin: float = 0.003,
        min_order_uah: float = 500.0,
        owner_user_id: Optional[int] = None,
    ) -> bool:
        owner_user_id = owner_user_id or 0
        logger.info(
            f"[TM] Старт T→M: Buy(Taker) на {buy_leg['exchange']} @ {buy_leg['price']} "
            f"→ Sell(Maker) на {sell_exchange}, об'єм {amount_usdt} USDT"
        )

        # 1. LLM фільтр для buy-контрагента
        buy_adv = buy_leg.get("merchant_id")
        if buy_adv:
            rec = await self._db.get_trade_recommendation(buy_leg["exchange"], buy_adv)
            if rec == "REJECT":
                logger.warning(f"[TM] Скасування: продавець {buy_adv} заблоковано LLM (REJECT).")
                return False

        # 2. Мережа
        network, network_fee = NetworkFeeEngine.get_optimal_network(buy_leg["exchange"], sell_exchange)
        if network_fee >= 999.0:
            logger.error(f"[TM] Немає спільних мереж між {buy_leg['exchange']} та {sell_exchange}")
            return False

        # ФІКС #7: route_type залежить від реальної fee, не хардкод
        route_type     = "CROSS" if network_fee > 0 else "INTRA"
        amount_uah_buy = amount_usdt * buy_leg["price"]
        min_sell_price = buy_leg["price"] * (1 + min_margin) + (network_fee / max(amount_usdt, 1))
        sell_amount    = amount_usdt - network_fee

        all_opts  = NetworkFeeEngine.get_all_options(buy_leg["exchange"], sell_exchange)
        opts_str  = " | ".join(f"{n}={f:.2f}$" for n, f in all_opts)
        logger.info(
            f"[TM] Мережа={network} fee={network_fee} USDT | "
            f"Альтернативи: [{opts_str}] | MinSellPrice={min_sell_price:.4f}"
        )

        # 3. Credentials
        # ФІКС #3: _build_credentials для ВСІХ бірж (включно з Binance session cookies)
        buy_creds  = await self._build_credentials(buy_leg["exchange"], owner_user_id)
        sell_creds = await self._build_credentials(sell_exchange, owner_user_id)

        # 4. Сесія в БД
        # ФІКС #1: buy_exchange зберігається
        session_id = await self._db.create_trade_session(
            strategy="TM", route_type=route_type, network=network,
            network_fee=network_fee, gross_profit=0,
            buy_exchange=buy_leg["exchange"],
        )

        # 5. Taker-Buy (Нога 1)
        buy_trade_id = await self._db.create_active_trade(
            session_id=session_id, strategy="TM", leg="BUY", route_type=route_type,
            network=network, network_fee=0, owner_user_id=owner_user_id,
            exchange=buy_leg["exchange"], order_id=f"PENDING_BUY_{uuid.uuid4().hex[:8]}",
            ad_id=buy_leg["ad_id"], asset="USDT", fiat="UAH",
            price=buy_leg["price"], amount=amount_usdt, fiat_amount=amount_uah_buy,
            status="PENDING_CREATION",
        )
        await self._db.update_trade_session(session_id, "BUY_IN_PROGRESS", buy_leg_id=buy_trade_id)

        buy_result = await self._executor.execute_taker_order(
            exchange=buy_leg["exchange"], action="BUY", ad_id=buy_leg["ad_id"],
            fiat_amount=amount_uah_buy, price=buy_leg["price"], credentials=buy_creds,
        )
        if not buy_result["success"]:
            logger.error(f"[TM] Помилка Ноги 1 (BUY): {buy_result['error']}")
            await self._db.update_active_trade_status(buy_trade_id, "FAILED")
            await self._db.update_trade_session(session_id, "FAILED")
            return False

        await self._db.update_active_trade_order_id(buy_trade_id, buy_result["order_id"])
        await self._db.update_active_trade_status(buy_trade_id, "BUY_COMPLETED")
        logger.info(f"[TM] ✅ Нога 1 виконана: {buy_result['order_id']}")

        # 6. Maker-Sell (Нога 2)
        ad_result = await self._executor.create_maker_ad(
            exchange=sell_exchange, action="SELL",
            price=min_sell_price, amount_usdt=sell_amount,
            min_order_uah=min_order_uah, credentials=sell_creds,
        )
        if not ad_result["success"]:
            logger.error(f"[TM] Помилка розміщення Maker SELL: {ad_result['error']}")
            await self._db.update_active_trade_status(buy_trade_id, "SELL_PENDING")
            await self._db.update_trade_session(session_id, "FAILED")
            return False

        sell_ad_id   = ad_result["ad_id"]
        sell_trade_id = await self._db.create_active_trade(
            session_id=session_id, strategy="TM", leg="SELL", route_type=route_type,
            network=network, network_fee=network_fee, owner_user_id=owner_user_id,
            exchange=sell_exchange, order_id=f"MAKER_SELL_{uuid.uuid4().hex[:8]}",
            ad_id=sell_ad_id, asset="USDT", fiat="UAH",
            price=min_sell_price, amount=sell_amount,
            fiat_amount=sell_amount * min_sell_price, status="WAITING_BUYER",
        )
        await self._db.update_active_trade_order_id(sell_trade_id, sell_ad_id)
        await self._db.update_trade_session(session_id, "SELL_IN_PROGRESS", sell_leg_id=sell_trade_id)
        logger.info(f"[TM] 📢 Maker SELL оголошення розміщено: {sell_ad_id}")

        # 7. Запуск AdRepricer у фоні
        repricer = AdRepricer(
            session_id=session_id, sell_ad_id=sell_ad_id, exchange=sell_exchange,
            buy_price=buy_leg["price"], amount_usdt=sell_amount,
            network_fee=network_fee, min_margin=min_margin,
        )

        async def _fetch_book_top(exc: str, ad: str, _exc=sell_exchange) -> Optional[float]:
            try:
                if _exc == "Bybit":
                    client = BybitP2PClient()
                    async with client:
                        return await client.fetch_p2p_book_top(
                            fiat="UAH", asset="USDT", side=0, exclude_ad_id=ad
                        )
            except Exception as e:
                logger.debug(f"[TM] fetch_book_top error: {e}")
            return None

        async def _update_ad_price(exc: str, ad: str, price: float, _creds=sell_creds) -> bool:
            return await self._executor.update_maker_ad_price(exc, ad, price, _creds)

        asyncio.create_task(
            repricer.watch(_fetch_book_top, _update_ad_price, self._db),
            name=f"repricer_tm_{session_id}",
        )
        logger.info(
            f"[TM] ✅ T→M маршрут запущено! Сесія #{session_id}. "
            f"Чекаємо покупця на оголошення {sell_ad_id} @ {min_sell_price:.4f} UAH."
        )
        return True

    # ════════════════════════════════════════════════════════════════════
    # Стратегія M→T: Maker-Buy + Taker-Sell (Блок 7)
    # ════════════════════════════════════════════════════════════════════

    async def execute_mt_route(
        self,
        buy_exchange: str,
        buy_price: float,
        sell_leg: dict,
        amount_usdt: float,
        min_order_uah: float = 500.0,
        owner_user_id: Optional[int] = None,
    ) -> bool:
        owner_user_id = owner_user_id or 0
        logger.info(
            f"[MT] Старт M→T: Buy(Maker) на {buy_exchange} @ {buy_price} "
            f"→ Sell(Taker) на {sell_leg['exchange']}, об'єм {amount_usdt} USDT"
        )

        sell_adv = sell_leg.get("merchant_id")
        if sell_adv:
            rec = await self._db.get_trade_recommendation(sell_leg["exchange"], sell_adv)
            if rec == "REJECT":
                logger.warning(f"[MT] Скасування: покупець {sell_adv} заблоковано LLM (REJECT).")
                return False

        network, network_fee = NetworkFeeEngine.get_optimal_network(buy_exchange, sell_leg["exchange"])
        if network_fee >= 999.0:
            logger.error(f"[MT] Немає спільних мереж між {buy_exchange} та {sell_leg['exchange']}")
            return False

        route_type = "CROSS" if network_fee > 0 else "INTRA"

        # ФІКС #3: _build_credentials замість get_credentials (підтримка Binance cookies)
        buy_creds  = await self._build_credentials(buy_exchange, owner_user_id)
        sell_creds = await self._build_credentials(sell_leg["exchange"], owner_user_id)

        ad_result = await self._executor.create_maker_ad(
            exchange=buy_exchange, action="BUY",
            price=buy_price, amount_usdt=amount_usdt,
            min_order_uah=min_order_uah, credentials=buy_creds,
        )
        if not ad_result["success"]:
            logger.error(f"[MT] Помилка розміщення Maker BUY: {ad_result['error']}")
            return False

        buy_ad_id = ad_result["ad_id"]
        # ФІКС #1: buy_exchange зберігається в сесії
        session_id = await self._db.create_trade_session(
            strategy="MT", route_type=route_type, network=network,
            network_fee=network_fee, gross_profit=0,
            buy_exchange=buy_exchange,
        )
        buy_trade_id = await self._db.create_active_trade(
            session_id=session_id, strategy="MT", leg="BUY", route_type=route_type,
            network=network, network_fee=0, owner_user_id=owner_user_id,
            exchange=buy_exchange, order_id=buy_ad_id, ad_id=buy_ad_id,
            asset="USDT", fiat="UAH", price=buy_price, amount=amount_usdt,
            fiat_amount=amount_usdt * buy_price, status="WAITING_COUNTERPARTY",
        )
        await self._db.update_trade_session(session_id, "BUY_IN_PROGRESS", buy_leg_id=buy_trade_id)
        logger.info(f"[MT] 📢 Maker BUY оголошення розміщено: {buy_ad_id}. Чекаємо продавця...")
        return True

    async def execute_mt_sell_leg(
        self,
        session_id: int,
        sell_leg: dict,
        amount_usdt: float,
        owner_user_id: Optional[int] = None,
    ) -> bool:
        """
        Виконується після того, як Maker-Buy нога закрилась (продавець прийшов).
        Запускає Taker-Sell (Нога 2 для M→T стратегії).
        """
        owner_user_id = owner_user_id or 0

        # ФІКС #1: buy_exchange беремо з БД — більше немає "__any__"
        session = await self._db.get_trade_session(session_id)
        if not session:
            logger.error(f"[MT] Сесія #{session_id} не знайдена в БД!")
            return False

        real_buy_exchange = session.get("buy_exchange") or sell_leg["exchange"]
        network, network_fee = NetworkFeeEngine.get_optimal_network(real_buy_exchange, sell_leg["exchange"])
        sell_amount  = amount_usdt - network_fee
        fiat_amount  = sell_amount * sell_leg["price"]
        route_type   = "CROSS" if network_fee > 0 else "INTRA"

        sell_creds = await self._build_credentials(sell_leg["exchange"], owner_user_id)
        logger.info(f"[MT] Запускаю Taker-Sell ногу для сесії #{session_id}")

        sell_trade_id = await self._db.create_active_trade(
            session_id=session_id, strategy="MT", leg="SELL", route_type=route_type,
            network=network, network_fee=network_fee, owner_user_id=owner_user_id,
            exchange=sell_leg["exchange"], order_id=f"PENDING_SELL_{uuid.uuid4().hex[:8]}",
            ad_id=sell_leg["ad_id"], asset="USDT", fiat="UAH",
            price=sell_leg["price"], amount=sell_amount, fiat_amount=fiat_amount,
            status="SELL_PENDING",
        )
        await self._db.update_trade_session(session_id, "SELL_IN_PROGRESS", sell_leg_id=sell_trade_id)

        sell_result = await self._executor.execute_taker_order(
            exchange=sell_leg["exchange"], action="SELL", ad_id=sell_leg["ad_id"],
            fiat_amount=fiat_amount, price=sell_leg["price"], credentials=sell_creds,
        )
        if not sell_result["success"]:
            logger.error(f"[MT] Помилка Taker-Sell: {sell_result['error']}")
            await self._db.update_active_trade_status(sell_trade_id, "FAILED")
            await self._db.update_trade_session(session_id, "FAILED")
            return False

        await self._db.update_active_trade_order_id(sell_trade_id, sell_result["order_id"])
        await self._db.update_active_trade_status(sell_trade_id, "COMPLETED")
        await self._db.update_trade_session(session_id, "COMPLETED")
        logger.info(f"[MT] ✅ M→T маршрут завершено! Сесія #{session_id}")
        return True

    # ════════════════════════════════════════════════════════════════════
    # Стратегія M→M: Maker-Buy + Maker-Sell (Блок 7)
    # ════════════════════════════════════════════════════════════════════

    async def execute_mm_route(
        self,
        buy_exchange: str,
        buy_price: float,
        sell_exchange: str,
        amount_usdt: float,
        min_margin: float = 0.003,
        min_order_uah: float = 500.0,
        owner_user_id: Optional[int] = None,
    ) -> bool:
        owner_user_id = owner_user_id or 0
        logger.info(
            f"[MM] Старт M→M: Buy(Maker) на {buy_exchange} @ {buy_price} "
            f"→ Sell(Maker) на {sell_exchange}, об'єм {amount_usdt} USDT"
        )

        network, network_fee = NetworkFeeEngine.get_optimal_network(buy_exchange, sell_exchange)
        if network_fee >= 999.0:
            logger.error(f"[MM] Немає спільних мереж між {buy_exchange} та {sell_exchange}")
            return False

        route_type     = "CROSS" if network_fee > 0 else "INTRA"
        all_opts       = NetworkFeeEngine.get_all_options(buy_exchange, sell_exchange)
        opts_str       = " | ".join(f"{n}={f:.2f}$" for n, f in all_opts)
        min_sell_price = buy_price * (1 + min_margin) + (network_fee / max(amount_usdt, 1))
        sell_amount    = amount_usdt - network_fee

        logger.info(
            f"[MM] Мережа={network} fee={network_fee} USDT | "
            f"Альтернативи: [{opts_str}] | MinSellPrice={min_sell_price:.4f}"
        )

        buy_creds = await self._build_credentials(buy_exchange, owner_user_id)

        buy_ad_result = await self._executor.create_maker_ad(
            exchange=buy_exchange, action="BUY",
            price=buy_price, amount_usdt=amount_usdt,
            min_order_uah=min_order_uah, credentials=buy_creds,
        )
        if not buy_ad_result["success"]:
            logger.error(f"[MM] Помилка розміщення Maker BUY: {buy_ad_result['error']}")
            return False

        buy_ad_id = buy_ad_result["ad_id"]
        # ФІКС #1: buy_exchange зберігається в сесії
        session_id = await self._db.create_trade_session(
            strategy="MM", route_type=route_type, network=network,
            network_fee=network_fee, gross_profit=0,
            buy_exchange=buy_exchange,
        )
        buy_trade_id = await self._db.create_active_trade(
            session_id=session_id, strategy="MM", leg="BUY", route_type=route_type,
            network=network, network_fee=0, owner_user_id=owner_user_id,
            exchange=buy_exchange, order_id=buy_ad_id, ad_id=buy_ad_id,
            asset="USDT", fiat="UAH", price=buy_price, amount=amount_usdt,
            fiat_amount=amount_usdt * buy_price, status="WAITING_COUNTERPARTY",
        )
        await self._db.update_trade_session(session_id, "BUY_IN_PROGRESS", buy_leg_id=buy_trade_id)
        logger.info(
            f"[MM] 📢 Maker BUY розміщено: {buy_ad_id} @ {buy_price} UAH. "
            f"Чекаємо продавця... (після закриття → запустити execute_mm_sell_leg)"
        )
        return True

    async def execute_mm_sell_leg(
        self,
        session_id: int,
        sell_exchange: str,
        amount_usdt: float,
        buy_price: float,
        min_margin: float = 0.003,
        min_order_uah: float = 500.0,
        owner_user_id: Optional[int] = None,
    ) -> bool:
        """
        Друга нога M→M: після того як Maker-Buy закрилась,
        ставимо власне Maker-Sell оголошення і запускаємо AdRepricer.
        """
        owner_user_id = owner_user_id or 0

        # ФІКС #1: buy_exchange беремо з БД — більше немає "__any__"
        session = await self._db.get_trade_session(session_id)
        if not session:
            logger.error(f"[MM] Сесія #{session_id} не знайдена в БД!")
            return False

        real_buy_exchange = session.get("buy_exchange") or sell_exchange
        network, network_fee = NetworkFeeEngine.get_optimal_network(real_buy_exchange, sell_exchange)
        route_type     = "CROSS" if network_fee > 0 else "INTRA"
        sell_amount    = amount_usdt - network_fee
        min_sell_price = buy_price * (1 + min_margin) + (network_fee / max(amount_usdt, 1))

        sell_creds = await self._build_credentials(sell_exchange, owner_user_id)
        logger.info(f"[MM] Launching Maker-Sell for session #{session_id} @ min {min_sell_price:.4f}")

        sell_ad_result = await self._executor.create_maker_ad(
            exchange=sell_exchange, action="SELL",
            price=min_sell_price, amount_usdt=sell_amount,
            min_order_uah=min_order_uah, credentials=sell_creds,
        )
        if not sell_ad_result["success"]:
            logger.error(f"[MM] Помилка Maker SELL: {sell_ad_result['error']}")
            await self._db.update_trade_session(session_id, "FAILED")
            return False

        sell_ad_id = sell_ad_result["ad_id"]
        sell_trade_id = await self._db.create_active_trade(
            session_id=session_id, strategy="MM", leg="SELL", route_type=route_type,
            network=network, network_fee=network_fee, owner_user_id=owner_user_id,
            exchange=sell_exchange, order_id=sell_ad_id, ad_id=sell_ad_id,
            asset="USDT", fiat="UAH", price=min_sell_price, amount=sell_amount,
            fiat_amount=sell_amount * min_sell_price, status="WAITING_BUYER",
        )
        await self._db.update_trade_session(session_id, "SELL_IN_PROGRESS", sell_leg_id=sell_trade_id)
        logger.info(f"[MM] 📢 Maker SELL розміщено: {sell_ad_id} @ {min_sell_price:.4f}")

        repricer = AdRepricer(
            session_id=session_id, sell_ad_id=sell_ad_id, exchange=sell_exchange,
            buy_price=buy_price, amount_usdt=sell_amount,
            network_fee=network_fee, min_margin=min_margin,
        )

        async def _fetch_book_top(exc: str, ad: str, _exc=sell_exchange) -> Optional[float]:
            try:
                if _exc == "Bybit":
                    client = BybitP2PClient()
                    async with client:
                        return await client.fetch_p2p_book_top(
                            fiat="UAH", asset="USDT", side=0, exclude_ad_id=ad
                        )
            except Exception as e:
                logger.debug(f"[MM] fetch_book_top error: {e}")
            return None

        async def _update_ad_price(exc: str, ad: str, price: float, _creds=sell_creds) -> bool:
            return await self._executor.update_maker_ad_price(exc, ad, price, _creds)

        asyncio.create_task(
            repricer.watch(_fetch_book_top, _update_ad_price, self._db),
            name=f"repricer_mm_{session_id}",
        )
        logger.info(f"[MM] ✅ M→M Sell-нога запущена. AdRepricer активний для сесії #{session_id}.")
        return True

    # ════════════════════════════════════════════════════════════════════
    # Службові методи
    # ════════════════════════════════════════════════════════════════════

    async def _build_credentials(self, exchange: str, user_id: int) -> dict:
        """
        Блок 1.5: Універсальний збирач credentials.
        ФІКС #3: Binance отримує session headers+cookies з auth_sessions,
                 а не API key/secret — що і потрібно для Session Hijack.
        """
        if exchange == "Binance":
            headers, cookies, updated_at = await self._db.get_auth_session(
                exchange="Binance", user_id=user_id
            )
            if not headers:
                logger.warning("[TradeWorker] Binance auth session недоступна — запустіть SessionManager")
            return {"headers": headers, "cookies": cookies}
        else:
            return await self._db.get_credentials(exchange=exchange, user_id=user_id) or {}
