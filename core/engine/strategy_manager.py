import asyncio
import logging
from typing import Dict, Optional
from core.storage.merchant_db import MerchantDB
from core.engine.ad_repricer import AdRepricer
from core.engine.maker_ad_monitor import MakerAdMonitor
from infrastructure.http.bybit_p2p_client import BybitP2PClient

logger = logging.getLogger("StrategyManager")

class StrategyManager:
    """
    Керує життєвим циклом активних торгових стратегій (наприклад, T→M, M→T, M→M).
    - Зберігає всі запущені asyncio.Task для AdRepricer.
    - Дозволяє зупиняти/скасовувати конкретні задачі (наприклад, при скасуванні угоди користувачем).
    - Hydration: при запуску сервера піднімає "покинуті" активні сесії з БД та відновлює їх фонову роботу.
    """

    def __init__(self, db: MerchantDB, maker_monitor: MakerAdMonitor, route_executor):
        self._db = db
        self._maker_monitor = maker_monitor
        self._executor = route_executor
        
        # session_id -> { "repricer": AdRepricer, "task": asyncio.Task }
        self._active_repricers: Dict[int, dict] = {}
        
    async def hydrate_active_trades(self) -> int:
        """
        Відновлює моніторинг для всіх активних мейкер-ордерів з БД при старті сервера.
        Повертає кількість відновлених сесій.
        """
        logger.info("[StrategyManager] Починаю Hydration активних мейкерів...")
        restored_count = 0
        try:
            # Шукаємо сесії де ми є мейкером і очікуємо (наприклад, стоїмо в стакані)
            trades = await self._db.get_active_trades_by_status(
                "WAITING_BUYER", "WAITING_COUNTERPARTY"
            )
            
            for trade in trades:
                session_id = trade.get("session_id")
                strategy = trade.get("strategy")
                exchange = trade.get("exchange")
                ad_id = trade.get("ad_id")
                price = trade.get("price", 0.0)
                amount = trade.get("amount", 0.0)
                network_fee = trade.get("network_fee", 0.0)
                user_id = trade.get("owner_user_id", 0)
                
                if not ad_id or not session_id or not exchange:
                    continue
                    
                logger.info(f"[Hydration] Знайдено покинутий мейкер {ad_id} (Session #{session_id}, {exchange}). Відновлюю...")
                
                # Запускаємо монітор нових ордерів на це оголошення
                await self._maker_monitor.start_watching(
                    user_id=user_id,
                    chat_id=user_id, # Якщо в базі був, тут спрощено
                    exchange=exchange,
                    ad_id=ad_id
                )
                
                # Відновлюємо AdRepricer
                repricer = AdRepricer(
                    session_id=session_id, 
                    sell_ad_id=ad_id, 
                    exchange=exchange,
                    buy_price=price * 0.99, # Зразкова для розрахунку маржі, якщо не збережена оригінальна
                    amount_usdt=amount,
                    network_fee=network_fee, 
                    min_margin=0.003
                )

                creds = await self._db.get_credentials(exchange, user_id) or {}
                
                async def _fetch_book_top(exc: str, ad: str, _exc=exchange) -> Optional[float]:
                    try:
                        if _exc == "Bybit":
                            client = BybitP2PClient()
                            async with client:
                                return await client.fetch_p2p_book_top(
                                    fiat="UAH", asset="USDT", side=0, exclude_ad_id=ad
                                )
                    except Exception as e:
                        logger.debug(f"[Hydration] fetch_book_top error: {e}")
                    return None

                async def _update_ad_price(exc: str, ad: str, target: float, _creds=creds) -> bool:
                    return await self._executor.update_maker_ad_price(exc, ad, target, _creds)

                task = asyncio.create_task(
                    repricer.watch(_fetch_book_top, _update_ad_price, self._db),
                    name=f"repricer_hydrated_{session_id}"
                )
                
                self.register_repricer(session_id, repricer, task)
                restored_count += 1
                
        except Exception as e:
            logger.error(f"[StrategyManager] Hydration error: {e}", exc_info=True)
            
        logger.info(f"[StrategyManager] Hydration завершено. Відновлено {restored_count} воркерів.")
        return restored_count

    def register_repricer(self, session_id: int, repricer: AdRepricer, task: asyncio.Task) -> None:
        """Зберігає посилання на активний AdRepricer."""
        if session_id in self._active_repricers:
            self.stop_repricer(session_id) # На всяк випадок зупиняємо старий
            
        self._active_repricers[session_id] = {
            "repricer": repricer,
            "task": task
        }
        logger.info(f"[StrategyManager] Зареєстровано репрайсер для сесії #{session_id}")

    def stop_repricer(self, session_id: int) -> bool:
        """Зупиняє цикл оновлення ціни для сесії."""
        if session_id in self._active_repricers:
            data = self._active_repricers.pop(session_id)
            data["repricer"].stop()
            if not data["task"].done():
                data["task"].cancel()
            logger.info(f"[StrategyManager] Репрайсер для сесії #{session_id} скасовано.")
            return True
        return False

    async def cancel_route(self, session_id: int, user_id: int) -> bool:
        """
        Скасування торгового маршруту користувачем.
        1. Разово зупиняє репрайсер.
        2. Надсилає API запит на зняття (закриття) мейкер-оголошення.
        3. Зупиняє MakerAdMonitor.
        """
        logger.info(f"[StrategyManager] Користувач #{user_id} скасовує маршрут #{session_id}")
        
        self.stop_repricer(session_id)
        
        # TODO: Додати отримання ad_id та exchange з БД та зняття через route_executor
        
        # self._maker_monitor.remove_ad(user_id, ad_id)
        
        # update session status to CANCELLED
        await self._db.update_trade_session(session_id, "CANCELLED")
        
        return True
