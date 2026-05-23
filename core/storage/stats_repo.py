# core/storage/stats_repo.py
# Proposals, trade sessions, active trades
from __future__ import annotations
import logging, time
from typing import Optional
import aiosqlite
logger = logging.getLogger(__name__)

class StatsRepo:
    """Proposals, trade sessions, active trades."""

    async def save_proposal(
            self,
            buy_exchange: str, sell_exchange: str,
            buy_merchant: str, sell_merchant: str,
            spread_pct: float, profit_uah: float, deal_amount: float,
            route_type: str, buy_bank: str, sell_bank: str,
            was_sent: bool = False,
    ) -> None:
        """Зберігає пропозицію сканера для статистики."""
        if not self._db:
            return
        try:
            await self._db.execute(
                """INSERT INTO scanner_proposals
                   (buy_exchange, sell_exchange, buy_merchant, sell_merchant,
                    spread_pct, profit_uah, deal_amount, route_type,
                    buy_bank, sell_bank, was_sent, created_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (buy_exchange, sell_exchange, buy_merchant, sell_merchant,
                 spread_pct, profit_uah, deal_amount, route_type,
                 buy_bank, sell_bank, 1 if was_sent else 0, time.time()),
            )
            await self._db.commit()
        except Exception as e:
            logger.debug("save_proposal: %s", e)

    async def cleanup_old_proposals(self, retention_days: int = 7) -> int:
        """Видаляє пропозиції старші за retention_days."""
        if not self._db:
            return 0
        cutoff = time.time() - retention_days * 86400
        cursor = await self._db.execute(
            "DELETE FROM scanner_proposals WHERE created_at < ?", (cutoff,)
        )
        await self._db.commit()
        return cursor.rowcount

    async def get_proposals_summary(self, period_days: int = 30) -> dict:
        """Зведена статистика пропозицій сканера."""
        if not self._db:
            return {}
        try:
            since = time.time() - period_days * 86400
            async with self._db.execute(
                    """SELECT COUNT(*)                                      as total,
                              SUM(CASE WHEN was_sent = 1 THEN 1 ELSE 0 END) as sent,
                              COALESCE(AVG(spread_pct), 0)                  as avg_spread,
                              COALESCE(AVG(profit_uah), 0)                  as avg_profit,
                              COALESCE(MAX(spread_pct), 0)                  as max_spread,
                              COALESCE(SUM(profit_uah), 0)                  as total_potential_profit
                       FROM scanner_proposals
                       WHERE created_at >= ?""",
                    (since,)
            ) as cur:
                row = await cur.fetchone()
            if not row or row["total"] == 0:
                return {}
            return {
                "total": row["total"],
                "sent": row["sent"],
                "avg_spread": round(row["avg_spread"], 3),
                "avg_profit": round(row["avg_profit"], 2),
                "max_spread": round(row["max_spread"], 3),
                "total_potential_profit": round(row["total_potential_profit"], 2),
            }
        except Exception as e:
            logger.error("get_proposals_summary: %s", e)
            return {}

    async def get_proposals_by_route(self, period_days: int = 30) -> list[dict]:
        """Топ маршрутів по кількості пропозицій."""
        if not self._db:
            return []
        try:
            since = time.time() - period_days * 86400
            async with self._db.execute(
                    """SELECT buy_exchange || '→' || sell_exchange as route,
                              route_type,
                              COUNT(*)                             as cnt,
                              AVG(spread_pct)                      as avg_spread,
                              AVG(profit_uah)                      as avg_profit
                       FROM scanner_proposals
                       WHERE created_at >= ?
                       GROUP BY route, route_type
                       ORDER BY cnt DESC LIMIT 15""",
                    (since,)
            ) as cur:
                rows = await cur.fetchall()
            return [
                {
                    "route": r["route"],
                    "route_type": r["route_type"],
                    "count": r["cnt"],
                    "avg_spread": round(r["avg_spread"], 3),
                    "avg_profit": round(r["avg_profit"], 2),
                }
                for r in rows
            ]
        except Exception as e:
            logger.error("get_proposals_by_route: %s", e)
            return []

    async def get_proposals_by_day(self, period_days: int = 7) -> list[dict]:
        """Пропозиції по днях."""
        if not self._db:
            return []
        try:
            since = time.time() - period_days * 86400
            async with self._db.execute(
                    """SELECT DATE (created_at, 'unixepoch', 'localtime') as date, COUNT (*) as total, SUM (CASE WHEN was_sent=1 THEN 1 ELSE 0 END) as sent, AVG (spread_pct) as avg_spread
                       FROM scanner_proposals
                       WHERE created_at >= ?
                       GROUP BY date
                       ORDER BY date DESC""",
                    (since,)
            ) as cur:
                rows = await cur.fetchall()
            return [
                {
                    "date": r["date"],
                    "total": r["total"],
                    "sent": r["sent"],
                    "avg_spread": round(r["avg_spread"], 3),
                }
                for r in rows
            ]
        except Exception as e:
            logger.error("get_proposals_by_day: %s", e)
            return []

    # ==========================================
    # ── БЛОК 2: ТОРГОВІ СЕСІЇ (TRADE SESSIONS) ──
    # ==========================================

    async def create_trade_session(self, strategy: str, route_type: str, network: str, network_fee: float,
                                   gross_profit: float, buy_exchange: str = "") -> int:
        """Створює нову глобальну торгову сесію і повертає її ID."""
        if not self._db:
            return 0

        cursor = await self._db.execute(
            """
            INSERT INTO trade_sessions (strategy, route_type, network, network_fee, gross_profit, session_status,
                                        buy_exchange)
            VALUES (?, ?, ?, ?, ?, 'OPEN', ?)
            """,
            (strategy, route_type, network, network_fee, gross_profit, buy_exchange)
        )
        await self._db.commit()
        return cursor.lastrowid

    async def update_trade_session(self, session_id: int, status: str, buy_leg_id: Optional[int] = None,
                                   sell_leg_id: Optional[int] = None) -> None:
        """Оновлює стан торгової сесії."""
        if not self._db:
            return

        fields = ["session_status = ?"]
        values = [status]

        if buy_leg_id is not None:
            fields.append("buy_leg_id = ?")
            values.append(buy_leg_id)
        if sell_leg_id is not None:
            fields.append("sell_leg_id = ?")
            values.append(sell_leg_id)

        if status in ('COMPLETED', 'CANCELLED', 'FAILED'):
            fields.append("completed_at = CURRENT_TIMESTAMP")

        values.append(session_id)

        query = f"UPDATE trade_sessions SET {', '.join(fields)} WHERE id = ?"
        await self._db.execute(query, tuple(values))
        await self._db.commit()

    async def get_trade_session(self, session_id: int) -> dict | None:
        """Отримує дані торгової сесії по ID."""
        if not self._db:
            return None
        async with self._db.execute("SELECT * FROM trade_sessions WHERE id = ?", (session_id,)) as cur:
            row = await cur.fetchone()
        return dict(row) if row else None

    # ==========================================
    # ── БЛОК 3: АКТИВНІ ОРДЕРИ (ACTIVE TRADES) ──
    # ==========================================

    async def create_active_trade(self, session_id: int, strategy: str, leg: str, route_type: str,
                                  network: str, network_fee: float, owner_user_id: Optional[int],
                                  exchange: str, order_id: str, ad_id: str, asset: str, fiat: str,
                                  price: float, amount: float, fiat_amount: float, status: str) -> int:
        """Записує створений ордер (leg) у базу."""
        if not self._db:
            return 0

        cursor = await self._db.execute(
            """
            INSERT INTO active_trades (session_id, strategy, leg, route_type, network, network_fee, owner_user_id,
                                       exchange, order_id, ad_id, asset, fiat, price, amount, fiat_amount, status)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (session_id, strategy, leg, route_type, network, network_fee, owner_user_id,
             exchange, order_id, ad_id, asset, fiat, price, amount, fiat_amount, status)
        )
        await self._db.commit()
        return cursor.lastrowid

    async def update_active_trade_status(self, trade_id: int, new_status: str) -> None:
        """Оновлює статус конкретного ордера (FSM)."""
        if not self._db:
            return

        await self._db.execute(
            "UPDATE active_trades SET status = ?, updated_at = CURRENT_TIMESTAMP WHERE id = ?",
            (new_status, trade_id)
        )
        await self._db.commit()

    async def update_active_trade_order_id(self, trade_id: int, new_order_id: str) -> None:
        """Оновлює order_id після того як біржа його повернула."""
        if not self._db:
            return

        await self._db.execute(
            "UPDATE active_trades SET order_id = ?, updated_at = CURRENT_TIMESTAMP WHERE id = ?",
            (new_order_id, trade_id)
        )
        await self._db.commit()

    async def get_active_trades_by_status(self, *statuses: str) -> list[dict]:
        """
        Блок 6: Повертає усі активні угоди за списком статусів.
        Використовується при старті для відновлення незавершених торгів.
        Приклад: get_active_trades_by_status('PENDING_PAYMENT', 'PAID_PENDING_RELEASE', 'SELL_PENDING')
        """
        if not self._db or not statuses:
            return []

        placeholders = ", ".join("?" for _ in statuses)
        query = f"""
            SELECT at.*, ts.strategy as session_strategy, ts.route_type as session_route_type,
                   ts.network as session_network, ts.network_fee as session_network_fee,
                   ts.gross_profit as session_gross_profit
            FROM active_trades at
            LEFT JOIN trade_sessions ts ON at.session_id = ts.id
            WHERE at.status IN ({placeholders})
            ORDER BY at.created_at ASC
        """
        try:
            async with self._db.execute(query, tuple(statuses)) as cur:
                rows = await cur.fetchall()
            return [dict(row) for row in rows]
        except Exception as e:
            logger.error("get_active_trades_by_status: %s", e)
            return []

    async def get_user_active_trades(self, user_id: int) -> list[dict]:
        """Отримує всі активні поточні угоди користувача, які не закриті кінцевими статусами."""
        if not self._db:
            return []
        try:
            query = """
                    SELECT at.*, ts.strategy as session_strategy
                    FROM active_trades at
                LEFT JOIN trade_sessions ts \
                    ON at.session_id = ts.id
                    WHERE at.owner_user_id = ?
                      AND at.status NOT IN ('COMPLETED' \
                        , 'FAILED' \
                        , 'CANCELLED' \
                        , 'EXPIRED')
                    ORDER BY at.created_at DESC \
                    """
            async with self._db.execute(query, (user_id,)) as cur:
                rows = await cur.fetchall()
            return [dict(row) for row in rows]
        except Exception as e:
            logger.error("get_user_active_trades error: %s", e)
            return []

    async def get_active_repricer_sessions(self) -> list[dict]:
        """
        Блок 6: Повертає всі торгові сесії в статусі SELL_IN_PROGRESS.
        Використовується при старті для відновлення AdRepricer.
        """
        if not self._db:
            return []
        try:
            async with self._db.execute(
                    """
                    SELECT ts.*,
                           at.exchange,
                           at.ad_id,
                           at.price as buy_price,
                           at.amount,
                           at.network_fee
                    FROM trade_sessions ts
                             JOIN active_trades at
                    ON ts.buy_leg_id = at.id
                    WHERE ts.session_status = 'SELL_IN_PROGRESS'
                    ORDER BY ts.created_at ASC
                    """
            ) as cur:
                rows = await cur.fetchall()
            return [dict(row) for row in rows]
        except Exception as e:
            logger.error("get_active_repricer_sessions: %s", e)
            return []

    async def get_recent_trade_sessions(self, limit: int = 5) -> list[dict]:
        """Повертає останні активні торгові сесії для команди /trades."""
        if not self._db:
            return []
        async with self._db.execute(
                "SELECT id, strategy, session_status, network_fee, gross_profit "
                "FROM trade_sessions "
                "WHERE session_status NOT IN ('COMPLETED', 'FAILED', 'CANCELLED') "
                "ORDER BY id DESC LIMIT ?",
                (limit,)
        ) as cur:
            rows = await cur.fetchall()
        return [dict(r) for r in rows]

    # ═══════════════════════════════════════════════════════════════════════
    # BLOCK D: Аналітика + Session Health
    # ═══════════════════════════════════════════════════════════════════════

    async def get_all_blacklist(self) -> list[dict]:
        """Повертає весь чорний список для API та бота."""
        if not self._db:
            return []
        try:
            async with self._db.execute(
                    "SELECT exchange, merchant_id, merchant_name, reason, source, added_at "
                    "FROM global_blacklist ORDER BY added_at DESC"
            ) as cur:
                rows = await cur.fetchall()
            return [dict(r) for r in rows]
        except Exception as e:
            logger.error("get_all_blacklist: %s", e)
            return []

    async def get_all_auth_sessions(self) -> list[dict]:
        """Повертає всі активні auth-сесії з updated_at для health-check."""
        if not self._db:
            return []
        try:
            async with self._db.execute(
                    "SELECT user_id, exchange, updated_at, is_active "
                    "FROM auth_sessions ORDER BY exchange"
            ) as cur:
                rows = await cur.fetchall()
            return [dict(r) for r in rows]
        except Exception as e:
            logger.error("get_all_auth_sessions: %s", e)
            return []

    async def get_session_age(self, exchange: str, user_id: int = 0) -> float:
        """
        Повертає вік сесії в секундах (time.time() - updated_at).
        Якщо сесії немає — повертає float('inf').
        """
        if not self._db:
            return float("inf")
        try:
            async with self._db.execute(
                    "SELECT updated_at FROM auth_sessions "
                    "WHERE user_id=? AND exchange=? AND is_active=1",
                    (user_id, exchange),
            ) as cur:
                row = await cur.fetchone()

            if not row or not row["updated_at"]:
                return float("inf")
            return time.time() - float(row["updated_at"])
        except Exception as e:
            logger.error("get_session_age [%s]: %s", exchange, e)
            return float("inf")

    async def get_completed_trades(
            self, period_days: int = 30, owner_user_id: int = 0
    ) -> list[dict]:
        """
        Повертає завершені угоди за період — для StatsEngine.
        Включає payment_method для аналітики банків.
        """
        if not self._db:
            return []
        try:
            query = """
                    SELECT at.id, \
                           at.session_id, \
                           at.strategy, \
                           at.leg, \
                           at.exchange,
                           at.price, \
                           at.amount, \
                           at.fiat_amount, \
                           at.payment_method,
                           at.counterparty_id, \
                           at.counterparty_name,
                           at.created_at, \
                           at.updated_at,
                           ts.gross_profit, \
                           ts.network, \
                           ts.network_fee
                    FROM active_trades at
                LEFT JOIN trade_sessions ts \
                    ON at.session_id = ts.id
                    WHERE at.status = 'COMPLETED'
                      AND at.created_at >= datetime('now' \
                        , ?) \
                    """
            params = [f"-{period_days} days"]
            if owner_user_id:
                query += " AND at.owner_user_id = ?"
                params.append(owner_user_id)
            query += " ORDER BY at.created_at DESC"
            async with self._db.execute(query, tuple(params)) as cur:
                rows = await cur.fetchall()
            return [dict(r) for r in rows]
        except Exception as e:
            logger.error("get_completed_trades: %s", e)
            return []

    async def update_active_trade_payment_method(
            self, trade_id: int, payment_method: str
    ) -> None:
        """Встановлює метод оплати для конкретної ноги угоди."""
        if not self._db:
            return
        await self._db.execute(
            "UPDATE active_trades SET payment_method = ? WHERE id = ?",
            (payment_method, trade_id),
        )
        await self._db.commit()

    # =============================================================================
    # CARD MANAGEMENT SYSTEM - DAO METHODS
    # =============================================================================

