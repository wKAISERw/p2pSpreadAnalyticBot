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
    Відповідає за виконання кроків стратегії (наразі Taker-Taker).
    1. Перевіряє llm_decision обох контрагентів.
    2. Створює записи trade_session та active_trades у MerchantDB.
    3. Викликає RouteExecutor для створення ордерів (працює DRY RUN).
    4. Оновлює статуси.
    """

    def __init__(self, db: MerchantDB):
        self._db = db
        self._executor = RouteExecutor()

    async def execute_tt_route(self, buy_leg: dict, sell_leg: dict, amount_usdt: float, owner_user_id: Optional[int] = None) -> bool:
        """
        Запускає Taker-Taker ланцюжок.
        buy_leg / sell_leg - це словники з даними оголошення (exchange, ad_id, price, network, merchant_id).
        """
        logger.info(f"🚀 Починаю T-T угоду: Купую на {buy_leg['exchange']} по {buy_leg['price']} -> Продаю на {sell_leg['exchange']} по {sell_leg['price']} об'єм {amount_usdt}")

        buy_adv = buy_leg.get("merchant_id")
        sell_adv = sell_leg.get("merchant_id")
        
        # 1. Фільтрація за LLM-Вердиктом (тільки APPROVED)
        if buy_adv:
            buy_verdict_str = await self._db.get_verdict(buy_leg["exchange"], buy_adv, "")
            if buy_verdict_str != "APPROVED":
                logger.warning(f"⚠️ Скасування TT-маршруту: продавець {buy_adv} не має статусу APPROVED (поточний: {buy_verdict_str}).")
                return False

        if sell_adv:
            sell_verdict_str = await self._db.get_verdict(sell_leg["exchange"], sell_adv, "")
            if sell_verdict_str != "APPROVED":
                logger.warning(f"⚠️ Скасування TT-маршруту: покупець {sell_adv} не має статусу APPROVED (поточний: {sell_verdict_str}).")
                return False

        # 2. Розрахунок Network Fee (Комісії) — Блок 1
        network, network_fee_usdt = NetworkFeeEngine.get_optimal_network(
            buy_leg["exchange"], sell_leg["exchange"]
        )

        if network_fee_usdt >= 999.0:
            logger.error(
                f"❌ Немає спільних мереж між {buy_leg['exchange']} та {sell_leg['exchange']}. "
                f"Угода неможлива."
            )
            return False

        # Показуємо всі доступні мережі як альтернативи
        all_options = NetworkFeeEngine.get_all_options(buy_leg["exchange"], sell_leg["exchange"])
        options_str = " | ".join(f"{n}={f:.2f}$" for n, f in all_options)

        amount_uah = amount_usdt * buy_leg["price"]
        expected_profit = NetworkFeeEngine.calc_profit(
            amount_usdt, buy_leg["price"], sell_leg["price"], network_fee_usdt
        )

        logger.info(
            f"📊 Оцінка TT: Мережа={network} (fee={network_fee_usdt} USDT). "
            f"Альтернативи: [{options_str}]. Очікуваний профіт = {expected_profit:.2f} UAH"
        )

        if expected_profit <= 0:
            logger.warning(f"⚠️ Відмова від TT: Нульовий або від'ємний профіт ({expected_profit}).")
            return False

        # 3. Створення запису в БД 
        logger.info(f"💾 Створення глобальної торгової сесії...")
        route_type = "CROSS" if network_fee_usdt > 0 else "INTRA"
        session_id = await self._db.create_trade_session(
            strategy="TT", route_type=route_type, network=network, 
            network_fee=network_fee_usdt, gross_profit=expected_profit
        )

        
        # Блок 1.5: Отримання Trade Adapters Credentials (Private Execution)
        owner_user_id = owner_user_id or 0
        buy_creds  = await self._build_credentials(buy_leg["exchange"], owner_user_id)
        sell_creds = await self._build_credentials(sell_leg["exchange"], owner_user_id)
        logger.info(f"👉 Записую Leg 1 (BUY) у БД...")
        temp_buy_id = f"PENDING_BUY_{uuid.uuid4().hex[:8]}"
        buy_trade_id = await self._db.create_active_trade(
            session_id=session_id, strategy="TT", leg="BUY", route_type=route_type,
            network=network, network_fee=0, owner_user_id=owner_user_id,
            exchange=buy_leg["exchange"], order_id=temp_buy_id, ad_id=buy_leg["ad_id"],
            asset="USDT", fiat="UAH", price=buy_leg["price"], amount=amount_usdt, 
            fiat_amount=amount_uah, status="PENDING_CREATION"
        )
        await self._db.update_trade_session(session_id, "BUY_IN_PROGRESS", buy_leg_id=buy_trade_id)

        buy_result = await self._executor.execute_taker_order(
            exchange=buy_leg["exchange"], action="BUY", ad_id=buy_leg["ad_id"],
            fiat_amount=amount_uah, price=buy_leg["price"], credentials=buy_creds
        )

        if not buy_result["success"]:
            logger.error(f"❌ Помилка першої ноги (BUY): {buy_result['error']}")
            await self._db.update_active_trade_status(buy_trade_id, "FAILED")
            await self._db.update_trade_session(session_id, "FAILED")
            return False

        logger.info(f"✅ Успіх Лег 1 (BUY). ID ордера: {buy_result['order_id']}")
        
        # Оновлюємо статус в БД
        await self._db.update_active_trade_order_id(buy_trade_id, buy_result['order_id'])
        await self._db.update_active_trade_status(buy_trade_id, "PENDING_PAYMENT")
        
        # TODO: Тут має бути очікування оплати фіату юзером через інший обробник (FSM Wait)
        # Наразі для DRY_RUN ми симулюємо миттєве проходження:
        await self._db.update_active_trade_status(buy_trade_id, "BUY_COMPLETED")

        # 5. Створюємо Другу Ногу (SELL_PENDING)
        logger.info(f"👉 Записую Leg 2 (SELL) у БД...")
        temp_sell_id = f"PENDING_SELL_{uuid.uuid4().hex[:8]}"
        sell_trade_id = await self._db.create_active_trade(
            session_id=session_id, strategy="TT", leg="SELL", route_type=route_type,
            network=network, network_fee=network_fee_usdt, owner_user_id=owner_user_id,
            exchange=sell_leg["exchange"], order_id=temp_sell_id, ad_id=sell_leg["ad_id"],
            asset="USDT", fiat="UAH", price=sell_leg["price"], amount=amount_usdt - network_fee_usdt, 
            fiat_amount=(amount_usdt - network_fee_usdt) * sell_leg["price"], status="SELL_PENDING"
        )
        await self._db.update_trade_session(session_id, "SELL_IN_PROGRESS", sell_leg_id=sell_trade_id)
        
        sell_creds = await self._build_credentials(sell_leg["exchange"], owner_user_id)
        sell_result = await self._executor.execute_taker_order(
            exchange=sell_leg["exchange"], action="SELL", ad_id=sell_leg["ad_id"],
            fiat_amount=(amount_usdt - network_fee_usdt) * sell_leg["price"],
            price=sell_leg["price"], credentials=sell_creds
        )

        if not sell_result["success"]:
            logger.error(f"❌ Помилка другої ноги (SELL): {sell_result['error']}")
            await self._db.update_active_trade_status(sell_trade_id, "FAILED")
            await self._db.update_trade_session(session_id, "FAILED")
            return False

        logger.info(f"✅ Успіх Лег 2 (SELL). ID ордера: {sell_result['order_id']}")
        await self._db.update_active_trade_order_id(sell_trade_id, sell_result['order_id'])
        await self._db.update_active_trade_status(sell_trade_id, "COMPLETED")
        await self._db.update_trade_session(session_id, "COMPLETED")
        logger.info(f"🎉 TT Маршрут завершено успішно (БД оновлено)!")

        return True

    async def _build_credentials(self, exchange: str, user_id: int) -> dict:
        """
        Універсальний метод збирання credentials:
        - Для Bybit/OKX: API Key + Secret з user_credentials
        - Для Binance: headers + cookies з auth_sessions (SessionHijack)
        """
        if exchange == "Binance":
            # Binance потребує перехоплені сесійні з SessionManager
            headers, cookies, updated_at = await self._db.get_auth_session(
                exchange="Binance", user_id=user_id
            )
            if not headers:
                logger.warning("[TradeWorker] Binance auth session недоступна — запустіть SessionManager")
            return {"headers": headers, "cookies": cookies}
        else:
            # Bybit / інші: API Key + Secret
            return await self._db.get_credentials(exchange=exchange, user_id=user_id) or {}
    # ════════════════════════════════════════════════════════════════════

    async def execute_tm_route(
        self,
        buy_leg: dict,          # exchange, ad_id, price, merchant_id — для покупки (Taker)
        sell_exchange: str,     # біржа де ми розмістимо своє оголошення (Maker)
        amount_usdt: float,
        min_margin: float = 0.003,
        min_order_uah: float = 500.0,
        owner_user_id: Optional[int] = None,
    ) -> bool:
        """
        T→M: Купуємо у продавця (Taker), потім САМІ розміщуємо оголошення ПРОДАЖУ (Maker).
        Ціна Maker-оголошення = book_top - 0.01 (але не нижче min_sell_price).
        AdRepricer стежить за ціною в фоні.
        """
        owner_user_id = owner_user_id or 0
        logger.info(
            f"[TM] Старт T→M: Buy(Taker) на {buy_leg['exchange']} @ {buy_leg['price']} "
            f"→ Sell(Maker) на {sell_exchange}, об'єм {amount_usdt} USDT"
        )

        # 1. LLM фільтр (тільки для Taker-buy контрагента)
        buy_adv = buy_leg.get("merchant_id")
        if buy_adv:
            verdict = await self._db.get_verdict(buy_leg["exchange"], buy_adv, "")
            if verdict != "APPROVED":
                logger.warning(f"[TM] Скасування: продавець {buy_adv} не APPROVED ({verdict})")
                return False

        # 2. Вибір мережі
        network, network_fee = NetworkFeeEngine.get_optimal_network(
            buy_leg["exchange"], sell_exchange
        )
        if network_fee >= 999.0:
            logger.error(f"[TM] Немає спільних мереж між {buy_leg['exchange']} та {sell_exchange}")
            return False

        amount_uah_buy = amount_usdt * buy_leg["price"]
        min_sell_price = buy_leg["price"] * (1 + min_margin) + (network_fee / max(amount_usdt, 1))
        sell_amount    = amount_usdt - network_fee

        all_opts = NetworkFeeEngine.get_all_options(buy_leg["exchange"], sell_exchange)
        opts_str = " | ".join(f"{n}={f:.2f}$" for n, f in all_opts)
        logger.info(
            f"[TM] Мережа={network} fee={network_fee} USDT | "
            f"Альтернативи: [{opts_str}] | MinSellPrice={min_sell_price:.4f}"
        )

        # 3. Отримуємо credentials
        buy_creds  = await self._build_credentials(buy_leg["exchange"], owner_user_id)
        sell_creds = await self._build_credentials(sell_exchange, owner_user_id)

        # 4. Створення БД-сесії
        session_id = await self._db.create_trade_session(
            strategy="TM", route_type="CROSS" if network_fee > 0 else "INTRA",
            network=network, network_fee=network_fee, gross_profit=0  # буде відомо після закриття
        )

        # 5. Виконання Taker-Buy (Нога 1)
        import uuid
        temp_buy_id = f"PENDING_BUY_{uuid.uuid4().hex[:8]}"
        buy_trade_id = await self._db.create_active_trade(
            session_id=session_id, strategy="TM", leg="BUY", route_type="CROSS",
            network=network, network_fee=0, owner_user_id=owner_user_id,
            exchange=buy_leg["exchange"], order_id=temp_buy_id, ad_id=buy_leg["ad_id"],
            asset="USDT", fiat="UAH", price=buy_leg["price"], amount=amount_usdt,
            fiat_amount=amount_uah_buy, status="PENDING_CREATION"
        )
        await self._db.update_trade_session(session_id, "BUY_IN_PROGRESS", buy_leg_id=buy_trade_id)

        buy_result = await self._executor.execute_taker_order(
            exchange=buy_leg["exchange"], action="BUY", ad_id=buy_leg["ad_id"],
            fiat_amount=amount_uah_buy, price=buy_leg["price"], credentials=buy_creds
        )
        if not buy_result["success"]:
            logger.error(f"[TM] Помилка Ноги 1 (BUY): {buy_result['error']}")
            await self._db.update_active_trade_status(buy_trade_id, "FAILED")
            await self._db.update_trade_session(session_id, "FAILED")
            return False

        await self._db.update_active_trade_order_id(buy_trade_id, buy_result["order_id"])
        await self._db.update_active_trade_status(buy_trade_id, "BUY_COMPLETED")
        logger.info(f"[TM] ✅ Нога 1 виконана: {buy_result['order_id']}")

        # 6. Розміщення Maker-Sell (Нога 2): ціна = min_sell_price (потім AdRepricer підправить)
        ad_result = await self._executor.create_maker_ad(
            exchange=sell_exchange, action="SELL",
            price=min_sell_price,
            amount_usdt=sell_amount,
            min_order_uah=min_order_uah,
            credentials=sell_creds,
        )
        if not ad_result["success"]:
            logger.error(f"[TM] Помилка розміщення Maker SELL: {ad_result['error']}")
            await self._db.update_active_trade_status(buy_trade_id, "SELL_PENDING")
            await self._db.update_trade_session(session_id, "FAILED")
            return False

        sell_ad_id = ad_result["ad_id"]
        logger.info(f"[TM] 📢 Maker SELL оголошення розміщено: {sell_ad_id}")

        # 7. Запис Ноги 2 у БД
        temp_sell_id = f"MAKER_SELL_{uuid.uuid4().hex[:8]}"
        sell_trade_id = await self._db.create_active_trade(
            session_id=session_id, strategy="TM", leg="SELL", route_type="CROSS",
            network=network, network_fee=network_fee, owner_user_id=owner_user_id,
            exchange=sell_exchange, order_id=temp_sell_id, ad_id=sell_ad_id,
            asset="USDT", fiat="UAH", price=min_sell_price, amount=sell_amount,
            fiat_amount=sell_amount * min_sell_price, status="WAITING_BUYER"
        )
        await self._db.update_active_trade_order_id(sell_trade_id, sell_ad_id)
        await self._db.update_trade_session(session_id, "SELL_IN_PROGRESS", sell_leg_id=sell_trade_id)

        # 8. Запуск AdRepricer у фоні (стежить за ціною)
        repricer = AdRepricer(
            session_id=session_id,
            sell_ad_id=sell_ad_id,
            exchange=sell_exchange,
            buy_price=buy_leg["price"],
            amount_usdt=sell_amount,
            network_fee=network_fee,
            min_margin=min_margin,
        )

        async def _fetch_book_top(exc: str, ad: str) -> Optional[float]:
            try:
                if exc == "Bybit":
                    client = BybitP2PClient()
                    async with client:
                        return await client.fetch_p2p_book_top(
                            fiat="UAH", asset="USDT", side=0, exclude_ad_id=ad
                        )
            except Exception as e:
                logger.debug(f"[TM] fetch_book_top error: {e}")
            return None

        async def _update_ad_price(exc: str, ad: str, price: float) -> bool:
            return await self._executor.update_maker_ad_price(exc, ad, price, sell_creds)

        asyncio.create_task(
            repricer.watch(_fetch_book_top, _update_ad_price, self._db),
            name=f"repricer_tm_{session_id}"
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
        buy_exchange: str,      # де виставляємо своє BUY оголошення
        buy_price: float,       # ціна покупки (задається вручну)
        sell_leg: dict,         # exchange, ad_id, price, merchant_id — продаємо через Taker
        amount_usdt: float,
        min_order_uah: float = 500.0,
        owner_user_id: Optional[int] = None,
    ) -> bool:
        """
        M→T: Виставляємо своє BUY-оголошення (Maker), очікуємо продавця,
        потім одразу купуємо в Taker-режимі на другій біржі.
        """
        owner_user_id = owner_user_id or 0
        logger.info(
            f"[MT] Старт M→T: Buy(Maker) на {buy_exchange} @ {buy_price} "
            f"→ Sell(Taker) на {sell_leg['exchange']}, об'єм {amount_usdt} USDT"
        )

        # 1. LLM фільтр для Taker-Sell контрагента
        sell_adv = sell_leg.get("merchant_id")
        if sell_adv:
            verdict = await self._db.get_verdict(sell_leg["exchange"], sell_adv, "")
            if verdict != "APPROVED":
                logger.warning(f"[MT] Скасування: покупець {sell_adv} не APPROVED ({verdict})")
                return False

        # 2. Вибір мережі
        network, network_fee = NetworkFeeEngine.get_optimal_network(buy_exchange, sell_leg["exchange"])
        if network_fee >= 999.0:
            logger.error(f"[MT] Немає спільних мереж між {buy_exchange} та {sell_leg['exchange']}")
            return False

        buy_creds  = await self._db.get_credentials(buy_exchange, owner_user_id) or {}
        sell_creds = await self._db.get_credentials(sell_leg["exchange"], owner_user_id) or {}

        # 3. Розміщення BUY-оголошення (Maker, чекаємо продавця)
        ad_result = await self._executor.create_maker_ad(
            exchange=buy_exchange, action="BUY",
            price=buy_price, amount_usdt=amount_usdt,
            min_order_uah=min_order_uah, credentials=buy_creds,
        )
        if not ad_result["success"]:
            logger.error(f"[MT] Помилка розміщення Maker BUY: {ad_result['error']}")
            return False

        buy_ad_id = ad_result["ad_id"]
        session_id = await self._db.create_trade_session(
            strategy="MT", route_type="CROSS" if network_fee > 0 else "INTRA",
            network=network, network_fee=network_fee, gross_profit=0
        )

        import uuid
        buy_trade_id = await self._db.create_active_trade(
            session_id=session_id, strategy="MT", leg="BUY", route_type="CROSS",
            network=network, network_fee=0, owner_user_id=owner_user_id,
            exchange=buy_exchange, order_id=buy_ad_id, ad_id=buy_ad_id,
            asset="USDT", fiat="UAH", price=buy_price, amount=amount_usdt,
            fiat_amount=amount_usdt * buy_price, status="WAITING_COUNTERPARTY"
        )
        await self._db.update_trade_session(session_id, "BUY_IN_PROGRESS", buy_leg_id=buy_trade_id)

        logger.info(f"[MT] 📢 Maker BUY оголошення розміщено: {buy_ad_id}. Чекаємо продавця...")
        # NOTE: Коли продавець прийде і угода закриється, TG-бот надішле сигнал
        # і execute_mt_route_sell_leg() виконає Taker-Sell ногу.
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
        sell_creds = await self._db.get_credentials(sell_leg["exchange"], owner_user_id) or {}

        network, network_fee = NetworkFeeEngine.get_optimal_network("__any__", sell_leg["exchange"])
        sell_amount = amount_usdt - network_fee
        fiat_amount = sell_amount * sell_leg["price"]

        logger.info(f"[MT] Запускаю Taker-Sell ногу для сесії #{session_id}")

        import uuid
        temp_sell_id = f"PENDING_SELL_{uuid.uuid4().hex[:8]}"
        sell_trade_id = await self._db.create_active_trade(
            session_id=session_id, strategy="MT", leg="SELL", route_type="CROSS",
            network=network, network_fee=network_fee, owner_user_id=owner_user_id,
            exchange=sell_leg["exchange"], order_id=temp_sell_id, ad_id=sell_leg["ad_id"],
            asset="USDT", fiat="UAH", price=sell_leg["price"], amount=sell_amount,
            fiat_amount=fiat_amount, status="SELL_PENDING"
        )
        await self._db.update_trade_session(session_id, "SELL_IN_PROGRESS", sell_leg_id=sell_trade_id)

        sell_result = await self._executor.execute_taker_order(
            exchange=sell_leg["exchange"], action="SELL", ad_id=sell_leg["ad_id"],
            fiat_amount=fiat_amount, price=sell_leg["price"], credentials=sell_creds
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
        """
        M→M: Виставляємо своє BUY-оголошення (Maker), чекаємо продавця.
        Після закриття — автоматично виставляємо SELL-оголошення (Maker) + AdRepricer.

        Ціна Sell задається як book_top - 0.01 через AdRepricer (але не нижче min_sell_price).
        Ціна Buy задається ВРУЧНУ через аргумент buy_price.
        """
        owner_user_id = owner_user_id or 0
        logger.info(
            f"[MM] Старт M→M: Buy(Maker) на {buy_exchange} @ {buy_price} "
            f"→ Sell(Maker) на {sell_exchange}, об'єм {amount_usdt} USDT"
        )

        # 1. Вибір мережі
        network, network_fee = NetworkFeeEngine.get_optimal_network(buy_exchange, sell_exchange)
        if network_fee >= 999.0:
            logger.error(f"[MM] Немає спільних мереж між {buy_exchange} та {sell_exchange}")
            return False

        all_opts = NetworkFeeEngine.get_all_options(buy_exchange, sell_exchange)
        opts_str = " | ".join(f"{n}={f:.2f}$" for n, f in all_opts)
        min_sell_price = buy_price * (1 + min_margin) + (network_fee / max(amount_usdt, 1))
        sell_amount    = amount_usdt - network_fee

        logger.info(
            f"[MM] Мережа={network} fee={network_fee} USDT | "
            f"Альтернативи: [{opts_str}] | MinSellPrice={min_sell_price:.4f}"
        )

        buy_creds  = await self._db.get_credentials(buy_exchange, owner_user_id) or {}
        sell_creds = await self._db.get_credentials(sell_exchange, owner_user_id) or {}

        # 2. Розміщення Maker-BUY
        buy_ad_result = await self._executor.create_maker_ad(
            exchange=buy_exchange, action="BUY",
            price=buy_price, amount_usdt=amount_usdt,
            min_order_uah=min_order_uah, credentials=buy_creds,
        )
        if not buy_ad_result["success"]:
            logger.error(f"[MM] Помилка розміщення Maker BUY: {buy_ad_result['error']}")
            return False

        buy_ad_id  = buy_ad_result["ad_id"]
        session_id = await self._db.create_trade_session(
            strategy="MM", route_type="CROSS" if network_fee > 0 else "INTRA",
            network=network, network_fee=network_fee, gross_profit=0
        )

        buy_trade_id = await self._db.create_active_trade(
            session_id=session_id, strategy="MM", leg="BUY", route_type="CROSS",
            network=network, network_fee=0, owner_user_id=owner_user_id,
            exchange=buy_exchange, order_id=buy_ad_id, ad_id=buy_ad_id,
            asset="USDT", fiat="UAH", price=buy_price, amount=amount_usdt,
            fiat_amount=amount_usdt * buy_price, status="WAITING_COUNTERPARTY"
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
        sell_creds = await self._build_credentials(sell_exchange, owner_user_id)

        # Визначаємо network_fee з сесії
        network, network_fee = NetworkFeeEngine.get_optimal_network("__any__", sell_exchange)
        sell_amount    = amount_usdt - network_fee
        min_sell_price = buy_price * (1 + min_margin) + (network_fee / max(amount_usdt, 1))

        logger.info(f"[MM] Launching Maker-Sell for session #{session_id} @ min {min_sell_price:.4f}")

        # Розміщуємо Maker-SELL
        sell_ad_result = await self._executor.create_maker_ad(
            exchange=sell_exchange, action="SELL",
            price=min_sell_price, amount_usdt=sell_amount,
            min_order_uah=min_order_uah, credentials=sell_creds,
        )
        if not sell_ad_result["success"]:
            logger.error(f"[MM] Помилка Maker SELL: {sell_ad_result['error']}")
            await self._db.update_trade_session(session_id, "FAILED")
            return False

        sell_ad_id    = sell_ad_result["ad_id"]
        sell_trade_id = await self._db.create_active_trade(
            session_id=session_id, strategy="MM", leg="SELL", route_type="CROSS",
            network=network, network_fee=network_fee, owner_user_id=owner_user_id,
            exchange=sell_exchange, order_id=sell_ad_id, ad_id=sell_ad_id,
            asset="USDT", fiat="UAH", price=min_sell_price, amount=sell_amount,
            fiat_amount=sell_amount * min_sell_price, status="WAITING_BUYER"
        )
        await self._db.update_trade_session(session_id, "SELL_IN_PROGRESS", sell_leg_id=sell_trade_id)

        logger.info(f"[MM] 📢 Maker SELL розміщено: {sell_ad_id} @ {min_sell_price:.4f}")

        # Запускаємо AdRepricer у фоні
        repricer = AdRepricer(
            session_id=session_id, sell_ad_id=sell_ad_id, exchange=sell_exchange,
            buy_price=buy_price, amount_usdt=sell_amount,
            network_fee=network_fee, min_margin=min_margin,
        )

        async def _fetch_book_top(exc: str, ad: str) -> Optional[float]:
            try:
                if exc == "Bybit":
                    client = BybitP2PClient()
                    async with client:
                        return await client.fetch_p2p_book_top(
                            fiat="UAH", asset="USDT", side=0, exclude_ad_id=ad
                        )
            except Exception as e:
                logger.debug(f"[MM] fetch_book_top error: {e}")
            return None

        async def _update_ad_price(exc: str, ad: str, price: float) -> bool:
            return await self._executor.update_maker_ad_price(exc, ad, price, sell_creds)

        asyncio.create_task(
            repricer.watch(_fetch_book_top, _update_ad_price, self._db),
            name=f"repricer_mm_{session_id}"
        )
        logger.info(f"[MM] ✅ M→M Sell-нога запущена. AdRepricer активний для сесії #{session_id}.")
        return True
