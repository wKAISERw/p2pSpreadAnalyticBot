# core/storage/card_repo.py
# Cards, bank limits, transactions, reservations, mono integration
from __future__ import annotations
import logging, time, uuid
from typing import Optional
import aiohttp
import aiosqlite
from core.utils.crypto import encrypt, decrypt
logger = logging.getLogger(__name__)

class CardRepo:
    """Cards, limits, transactions, Monobank integration."""

    async def get_user_card_settings(self, user_id: int) -> dict | None:
        if not self._db:
            return None
        async with self._db.execute(
            "SELECT * FROM user_card_settings WHERE user_id=?", (user_id,)
        ) as cur:
            row = await cur.fetchone()
            return dict(row) if row else None

    async def update_user_card_settings(self, user_id: int, settings_dict: dict) -> None:
        """
        Зберігає оновлену конфігурацію карткового модуля для користувача (UPSERT).
        Додано підтримку прапорця відображення в одиночних режимах.
        """
        if not self._db:
            return

        card_output_mode = settings_dict.get("card_output_mode", "inline")
        enable_smart_spoiler = 1 if settings_dict.get("enable_smart_spoiler", True) else 0
        card_detail_level = settings_dict.get("card_detail_level", "full")
        enable_in_single_modes = 1 if settings_dict.get("enable_in_single_modes", False) else 0
        show_balances_breakdown = 1 if settings_dict.get("show_balances_breakdown", True) else 0
        show_transfer_tips = 1 if settings_dict.get("show_transfer_tips", True) else 0
        cold_card_limit = float(settings_dict.get("cold_card_limit", 2000.0))

        await self._db.execute(
            """
            INSERT INTO user_card_settings (user_id, card_output_mode, enable_smart_spoiler, card_detail_level,
                                            enable_in_single_modes, show_balances_breakdown, show_transfer_tips, cold_card_limit)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?) ON CONFLICT(user_id) DO
            UPDATE SET
                card_output_mode = EXCLUDED.card_output_mode,
                enable_smart_spoiler = EXCLUDED.enable_smart_spoiler,
                card_detail_level = EXCLUDED.card_detail_level,
                enable_in_single_modes = EXCLUDED.enable_in_single_modes,
                show_balances_breakdown = EXCLUDED.show_balances_breakdown,
                show_transfer_tips = EXCLUDED.show_transfer_tips,
                cold_card_limit = EXCLUDED.cold_card_limit
            """,
            (user_id, card_output_mode, enable_smart_spoiler, card_detail_level, enable_in_single_modes, show_balances_breakdown, show_transfer_tips, cold_card_limit)
        )
        await self._db.commit()

    async def get_card_transactions_count(self, card_id: str, hours: int = 24) -> int:
        """Повертає сумарну кількість транзакцій (in + out) за останні N годин."""
        if not self._db:
            return 0
        cutoff = time.time() - (hours * 3600)
        async with self._db.execute(
            "SELECT COUNT(id) as cnt FROM card_transactions WHERE card_id=? AND timestamp > ?",
            (card_id, cutoff)
        ) as cur:
            row = await cur.fetchone()
            return int(row["cnt"]) if row and row["cnt"] else 0

    async def lazy_monthly_reset(self, card_id: str) -> None:
        """Перевіряє чи змінився місяць з моменту last_monthly_reset. Якщо так — оновлює таймстемп і статус (зняття заморозки)."""
        if not self._db:
            return
        
        async with self._db.execute("SELECT last_monthly_reset FROM cards WHERE id=?", (card_id,)) as cur:
            row = await cur.fetchone()
            if not row:
                return
            
            last_reset = row["last_monthly_reset"] or 0
            import datetime
            now_dt = datetime.datetime.now()
            
            if last_reset > 0:
                last_dt = datetime.datetime.fromtimestamp(last_reset)
                if last_dt.year == now_dt.year and last_dt.month == now_dt.month:
                    return  # Все ще той самий місяць
            
            # Місяць змінився (або це перший раз), оновлюємо
            await self._db.execute(
                "UPDATE cards SET last_monthly_reset=?, status='active' WHERE id=? AND status='frozen_funds'",
                (now_dt.timestamp(), card_id)
            )
            if last_reset == 0:
                 await self._db.execute(
                    "UPDATE cards SET last_monthly_reset=? WHERE id=?",
                    (now_dt.timestamp(), card_id)
                )
            await self._db.commit()

    async def get_user_bank_limits(self, user_id: int, bank_name: str) -> dict | None:
        if not self._db:
            return None
        async with self._db.execute(
            "SELECT * FROM user_bank_limits WHERE user_id=? AND bank_name=?",
            (user_id, bank_name)
        ) as cur:
            row = await cur.fetchone()
            return dict(row) if row else None

    async def set_user_bank_limit(self, user_id: int, bank_name: str, field: str, value: float) -> None:
        """Оновлює одне поле лімітів банку. Створює рядок з дефолтами якщо його немає."""
        if not self._db:
            return
        allowed = {
            "daily_out_max", "daily_in_max", "monthly_out_max", "monthly_in_max",
            "max_single_tx_out", "max_single_tx_in", "max_tx_per_day", "cooldown_hours"
        }
        if field not in allowed:
            return
        # Upsert: insert default row if missing
        await self._db.execute(
            "INSERT OR IGNORE INTO user_bank_limits (user_id, bank_name) VALUES (?, ?)",
            (user_id, bank_name)
        )
        await self._db.execute(
            f"UPDATE user_bank_limits SET {field}=? WHERE user_id=? AND bank_name=?",
            (value, user_id, bank_name)
        )
        await self._db.commit()

    async def get_card_effective_limits(self, card_id: str, owner_id: int = None, bank_name: str = None) -> dict:
        """Returns the effective limits for a card, merging global bank limits with local overrides.
        
        Priority: card-local override > global bank limit > hardcoded default.
        If owner_id/bank_name are not provided, they are fetched from the card row.
        """
        defaults = {
            "daily_out_max": 150000.0, "daily_in_max": 150000.0,
            "monthly_out_max": 400000.0, "monthly_in_max": 400000.0,
            "max_single_tx_out": 29999.0, "max_single_tx_in": 29999.0,
            "max_tx_per_day": 15, "cooldown_hours": 24
        }
        if not self._db:
            return defaults

        # Resolve owner_id/bank_name if not passed
        if owner_id is None or bank_name is None:
            async with self._db.execute(
                "SELECT owner_id, bank_name, is_custom_limits, limits_override_json FROM cards WHERE id=?",
                (card_id,)
            ) as cur:
                row = await cur.fetchone()
            if not row:
                return defaults
            owner_id = row["owner_id"]
            bank_name = row["bank_name"]
            is_custom = row["is_custom_limits"]
            override_json = row["limits_override_json"] or "{}"
        else:
            async with self._db.execute(
                "SELECT is_custom_limits, limits_override_json FROM cards WHERE id=?",
                (card_id,)
            ) as cur:
                row = await cur.fetchone()
            is_custom = row["is_custom_limits"] if row else 0
            override_json = (row["limits_override_json"] if row else None) or "{}"

        # Layer 1: global bank limits
        global_limits = await self.get_user_bank_limits(owner_id, bank_name)
        result = {**defaults}
        if global_limits:
            for k in defaults:
                if k in global_limits and global_limits[k] is not None:
                    result[k] = global_limits[k]

        # Layer 2: card-local overrides (if enabled)
        if is_custom:
            import json
            try:
                overrides = json.loads(override_json)
            except (json.JSONDecodeError, TypeError):
                overrides = {}
            for k, v in overrides.items():
                if k in result and v is not None:
                    result[k] = v

        return result

    async def update_card_limit_override(self, card_id: str, field: str, value: float) -> None:
        """Update a single limit field for a specific card (local override)."""
        if not self._db:
            return
        allowed = {
            "daily_out_max", "daily_in_max", "monthly_out_max", "monthly_in_max",
            "max_single_tx_out", "max_single_tx_in", "max_tx_per_day", "cooldown_hours"
        }
        if field not in allowed:
            return

        import json
        async with self._db.execute(
            "SELECT limits_override_json FROM cards WHERE id=?", (card_id,)
        ) as cur:
            row = await cur.fetchone()
        if not row:
            return
        try:
            overrides = json.loads(row["limits_override_json"] or "{}")
        except (json.JSONDecodeError, TypeError):
            overrides = {}
        overrides[field] = value

        await self._db.execute(
            "UPDATE cards SET limits_override_json=?, is_custom_limits=1 WHERE id=?",
            (json.dumps(overrides), card_id)
        )
        await self._db.commit()

    async def toggle_card_custom_limits(self, card_id: str, enable: bool) -> None:
        """Toggle custom limits on/off for a card. When disabled, global limits apply."""
        if not self._db:
            return
        await self._db.execute(
            "UPDATE cards SET is_custom_limits=? WHERE id=?",
            (1 if enable else 0, card_id)
        )
        await self._db.commit()

    async def add_card(self, card_data: dict) -> None:
        if not self._db:
            return
        fields = list(card_data.keys())
        placeholders = ",".join(["?"] * len(fields))
        values = tuple(card_data.values())
        query = f"INSERT INTO cards ({','.join(fields)}) VALUES ({placeholders})"
        await self._db.execute(query, values)
        await self._db.commit()

    async def update_card(self, card_id: str, updates: dict) -> None:
        if not self._db or not updates:
            return
        fields = [f"{k}=?" for k in updates.keys()]
        values = list(updates.values()) + [card_id]
        query = f"UPDATE cards SET {','.join(fields)} WHERE id=?"
        await self._db.execute(query, tuple(values))
        await self._db.commit()

    async def get_cards(self, owner_id: int, bank_name: str = None, status: str = None) -> list[dict]:
        if not self._db:
            return []
        query = "SELECT * FROM cards WHERE owner_id=?"
        params = [owner_id]
        if bank_name:
            query += " AND bank_name=?"
            params.append(bank_name)
        if status:
            query += " AND status=?"
            params.append(status)
        
        async with self._db.execute(query, tuple(params)) as cur:
            rows = await cur.fetchall()
            return [dict(r) for r in rows]

    async def update_card_balance(self, card_id: str, new_balance: float) -> None:
        if not self._db:
            return
        now = time.time()
        await self._db.execute(
            "UPDATE cards SET balance=?, balance_updated_at=? WHERE id=?",
            (new_balance, now, card_id)
        )
        await self._db.commit()

    async def get_rolling_used(self, card_id: str, direction: str, hours: int = 24) -> float:
        """Повертає суму транзакцій за останні N годин по напрямку 'in' або 'out'."""
        if not self._db:
            return 0.0
        cutoff = time.time() - (hours * 3600)
        async with self._db.execute(
            "SELECT SUM(amount) as total FROM card_transactions "
            "WHERE card_id=? AND direction=? AND timestamp > ?",
            (card_id, direction, cutoff)
        ) as cur:
            row = await cur.fetchone()
            return float(row["total"]) if row and row["total"] else 0.0

    async def reserve_card_amount(
        self, order_id: str, owner_id: int, target_bank: str, direction: str,
        total_amount: float, expected_window_minutes: int, split_strategy: list[dict],
        trade_session_id: int = None
    ) -> bool:
        """
        Атомарно створює order та записує leg(s). 
        split_strategy: [{"card_id": "uuid", "amount": 10000}, ...]
        """
        if not self._db:
            return False
        now = time.time()
        try:
            await self._db.execute("BEGIN TRANSACTION")
            await self._db.execute(
                """
                INSERT INTO card_orders (id, owner_id, trade_session_id, total_amount, target_bank, direction, status, expected_window_minutes, created_at)
                VALUES (?, ?, ?, ?, ?, ?, 'pending', ?, ?)
                """,
                (order_id, owner_id, trade_session_id, total_amount, target_bank, direction, expected_window_minutes, now)
            )
            for leg in split_strategy:
                await self._db.execute(
                    "INSERT INTO card_order_legs (order_id, card_id, amount, leg_status) VALUES (?, ?, ?, 'pending')",
                    (order_id, leg["card_id"], leg["amount"])
                )
            await self._db.commit()
            return True
        except Exception as e:
            await self._db.rollback()
            logger.error("reserve_card_amount failed: %s", e)
            return False

    async def confirm_transaction(
        self, card_id: str, amount: float, direction: str, type_str: str, 
        linked_order_id: str = None, source: str = "manual", true_balance: float = None
    ) -> None:
        """
        Логує транзакцію та оновлює баланс. Якщо true_balance передано (Mono Webhook) — 
        використовуємо його. Інакше розраховуємо на основі існуючого.
        """
        if not self._db:
            return
        now = time.time()
        tx_id = str(uuid.uuid4())
        try:
            await self._db.execute("BEGIN TRANSACTION")
            
            # 1. Запис транзакції
            await self._db.execute(
                """
                INSERT INTO card_transactions (id, card_id, amount, direction, type, linked_order_id, source, timestamp)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (tx_id, card_id, amount, direction, type_str, linked_order_id, source, now)
            )
            
            # 2. Оновлення балансу (фактичний або розрахунковий)
            if true_balance is not None:
                await self._db.execute(
                    "UPDATE cards SET balance=?, balance_updated_at=?, last_tx_timestamp=? WHERE id=?",
                    (true_balance, now, now, card_id)
                )
            else:
                sign = "+" if direction == "in" else "-"
                await self._db.execute(
                    f"UPDATE cards SET balance=balance {sign} ?, balance_updated_at=?, last_tx_timestamp=? WHERE id=?",
                    (amount, now, now, card_id)
                )
                
            # 3. Оновлення статусу leg якщо це робоча транзакція
            if linked_order_id:
                await self._db.execute(
                    "UPDATE card_order_legs SET leg_status='completed' WHERE order_id=? AND card_id=?",
                    (linked_order_id, card_id)
                )

            # 4. Авто-cooldown при 95% денного ліміту (C7) — використовує локальні ліміти якщо задано
            limits = await self.get_card_effective_limits(card_id)
            if limits:
                    daily_max = limits.get(f"daily_{direction}_max", 150000.0)
                    cooldown_hours = limits.get("cooldown_hours", 24)
                    # Підрахунок rolling used за 24г (включаючи щойно записану TX)
                    cutoff_24h = now - (24 * 3600)
                    async with self._db.execute(
                        "SELECT SUM(amount) as total FROM card_transactions WHERE card_id=? AND direction=? AND timestamp > ?",
                        (card_id, direction, cutoff_24h)
                    ) as cur2:
                        row2 = await cur2.fetchone()
                        used = float(row2["total"]) if row2 and row2["total"] else 0.0
                    if daily_max > 0 and used / daily_max >= 0.95:
                        cooldown_until = now + (cooldown_hours * 3600)
                        await self._db.execute(
                            "UPDATE cards SET cooldown_until=? WHERE id=? AND cooldown_until < ?",
                            (cooldown_until, card_id, cooldown_until)
                        )
                        logger.info("Auto-cooldown set for card %s: %.0f/%.0f (%.0f%%)", card_id[-4:], used, daily_max, used/daily_max*100)

            await self._db.commit()
        except Exception as e:
            await self._db.rollback()
            logger.error("confirm_transaction failed: %s", e)

    async def release_expired_reservations(self) -> int:
        """
        Звільняє завислі leg-и ордерів (timeout) та маркує ордер відповідно.
        Викликається з циклу сканера. Повертає кількість звільнених legs.
        """
        if not self._db:
            return 0
        now = time.time()
        count = 0
        try:
            await self._db.execute("BEGIN TRANSACTION")
            
            # Знаходимо pending orders, чиї вікна (у хвилинах) спливли (з 5-хвилинним буфером)
            async with self._db.execute(
                """
                SELECT id, expected_window_minutes, created_at 
                FROM card_orders 
                WHERE status IN ('pending', 'partially_completed')
                """
            ) as cur:
                orders = await cur.fetchall()
                
            for order in orders:
                expire_time = order["created_at"] + (order["expected_window_minutes"] * 60) + 300 # +5 mins buffer
                if now > expire_time:
                    # Timeout legs
                    async with self._db.execute(
                        "UPDATE card_order_legs SET leg_status='timeout' WHERE order_id=? AND leg_status='pending'",
                        (order["id"],)
                    ) as upd_cur:
                        if upd_cur.rowcount > 0:
                            count += upd_cur.rowcount
                            # Оновлюємо статус самого ордера
                            await self._db.execute(
                                "UPDATE card_orders SET status='canceled' WHERE id=? AND status='pending'",
                                (order["id"],)
                            )
            await self._db.commit()
            return count
        except Exception as e:
            await self._db.rollback()
            logger.error("release_expired_reservations failed: %s", e)
            return 0

    async def get_card_mono_settings(self, card_id: str) -> dict:
        if not self._db:
            return {}
        async with self._db.execute(
            """SELECT mono_x_token_encrypted as x_token_encrypted, mono_webhook_secret as webhook_secret,
                      COALESCE(mono_tracker_enabled, 1) as tracker_enabled,
                      COALESCE(mono_tracker_mode, 'INCOME') as tracker_mode,
                      COALESCE(mono_tracker_fields, '{"amount":1,"sender":1,"comment":1,"time":1,"card":1,"balance":1,"p2p":1}') as tracker_fields
               FROM cards WHERE id=?""", (card_id,)
        ) as cur:
            row = await cur.fetchone()
            return dict(row) if row else {}

    async def update_mono_tracker_config(self, card_id: str, enabled: Optional[int] = None, mode: Optional[str] = None, fields: Optional[dict] = None) -> None:
        if not self._db:
            return
        import json
        updates = []
        params = []
        if enabled is not None:
            updates.append("mono_tracker_enabled = ?")
            params.append(enabled)
        if mode is not None:
            updates.append("mono_tracker_mode = ?")
            params.append(mode)
        if fields is not None:
            updates.append("mono_tracker_fields = ?")
            params.append(json.dumps(fields))
        if not updates:
            return
        params.append(card_id)
        sql = f"UPDATE cards SET {', '.join(updates)} WHERE id=?"
        await self._db.execute(sql, tuple(params))
        await self._db.commit()

    async def save_card_mono_settings(self, card_id: str, x_token_encrypted: str, webhook_secret: str) -> None:
        if not self._db:
            return
        await self._db.execute(
            """
            UPDATE cards 
            SET mono_x_token_encrypted=?, mono_webhook_secret=?
            WHERE id=?
            """,
            (x_token_encrypted, webhook_secret, card_id)
        )
        await self._db.commit()

    async def update_card_mono_account(self, card_id: str, mono_account_id: str) -> None:
        if not self._db:
            return
        await self._db.execute("UPDATE cards SET mono_account_id=? WHERE id=?", (mono_account_id, card_id))
        await self._db.commit()

    async def get_card_by_mono_account(self, mono_account_id: str) -> dict:
        if not self._db:
            return None
        async with self._db.execute("SELECT * FROM cards WHERE mono_account_id=? AND status='active'", (mono_account_id,)) as cur:
            row = await cur.fetchone()
            return dict(row) if row else None

    async def find_pending_order_for_card(self, card_id: str, amount: float) -> dict:
        """Шукає активний card_order_legs для даної картки з точною сумою."""
        if not self._db:
            return None
        # Ми шукаємо серед pending legs
        async with self._db.execute(
            """
            SELECT o.id, o.direction 
            FROM card_order_legs l
            JOIN card_orders o ON l.order_id = o.id
            WHERE l.card_id = ? AND l.amount = ? AND l.leg_status = 'pending' AND o.status = 'pending'
            ORDER BY o.created_at ASC LIMIT 1
            """,
            (card_id, amount)
        ) as cur:
            row = await cur.fetchone()
            return dict(row) if row else None



    async def get_card_report_stats(self, card_id: str) -> dict:
        if not self._db:
            return {}
        cutoff_today = time.time() - (24 * 3600)
        cutoff_month = time.time() - (30 * 24 * 3600)
        
        async with self._db.execute(
            """
            SELECT 
                COUNT(*) as tx_count,
                SUM(CASE WHEN direction='in' THEN amount ELSE 0 END) as in_volume_today,
                SUM(CASE WHEN direction='out' THEN amount ELSE 0 END) as out_volume_today,
                SUM(CASE WHEN direction='in' AND type='work' THEN amount ELSE 0 END) as work_in_today,
                SUM(CASE WHEN direction='out' AND type='work' THEN amount ELSE 0 END) as work_out_today,
                SUM(CASE WHEN direction='in' AND type='personal' THEN amount ELSE 0 END) as personal_in_today,
                SUM(CASE WHEN direction='out' AND type='personal' THEN amount ELSE 0 END) as personal_out_today
            FROM card_transactions
            WHERE card_id=? AND timestamp > ?
            """,
            (card_id, cutoff_today)
        ) as cur:
            row = await cur.fetchone()
            stats = dict(row) if row else {}
            
        async with self._db.execute(
            """
            SELECT 
                SUM(CASE WHEN direction='in' THEN amount ELSE 0 END) as in_volume_month,
                SUM(CASE WHEN direction='out' THEN amount ELSE 0 END) as out_volume_month
            FROM card_transactions
            WHERE card_id=? AND timestamp > ?
            """,
            (card_id, cutoff_month)
        ) as cur:
            row = await cur.fetchone()
            if row:
                stats.update(dict(row))
                
        for k in stats:
            if stats[k] is None:
                stats[k] = 0.0 if "volume" in k or "work" in k or "personal" in k else 0
                
        return stats

    async def force_refresh_mono_balance(self, card_id: str) -> Optional[float]:
        """
        Прямий костиль-запит до API Монобанку для актуалізації балансу картки.
        Викликається примусово, коли вебхуки не працюють на локалці.
        """
        if not self._db:
            return None

        # 1. Дістаємо зашифрований токен та account_id з бази
        async with self._db.execute(
                "SELECT mono_x_token_encrypted, mono_account_id, owner_id FROM cards WHERE id=?",
                (card_id,)
        ) as cur:
            row = await cur.fetchone()
            if not row or not row["mono_x_token_encrypted"] or not row["mono_account_id"]:
                return None

        try:
            # Дешифруємо токен (у тебе в merchant_db.py для цього використовується decrypt)
            x_token = decrypt(row["mono_x_token_encrypted"])
            account_id = row["mono_account_id"]

            # 2. Стукаємось в API Монобанку за свіжими даними
            url = "https://api.monobank.ua/personal/client-info"
            headers = {"X-Token": x_token}

            async with aiohttp.ClientSession() as session:
                async with session.get(url, headers=headers, timeout=10) as resp:
                    if resp.status != 200:
                        logger.error(f"Mono API returned status {resp.status}")
                        return None
                    data = await resp.json()

            # 3. Шукаємо потрібний рахунок серед масиву акаунтів
            accounts = data.get("accounts", [])
            for acc in accounts:
                if acc.get("id") == account_id:
                    # Баланс Моно повертає в копійках (int), переводимо в гривні
                    true_balance = float(acc.get("balance", 0)) / 100.0

                    # 4. Оновлюємо баланс прямо в базі даних
                    await self.update_card_balance(card_id, true_balance)
                    logger.info(f"🔄 Свіжий баланс для карти {card_id} успішно стягнуто: {true_balance} ₴")
                    return true_balance

        except Exception as e:
            logger.error(f"Помилка примусового оновлення балансу Моно: {e}")
            return None

    async def get_feature_status(self, user_id: int, feature_key: str) -> bool:
            if not self._db: return False
            async with self._db.execute(
                    "SELECT is_enabled FROM user_features WHERE user_id=? AND feature_key=?",
                    (user_id, feature_key)
            ) as cur:
                row = await cur.fetchone()
                return bool(row["is_enabled"]) if row else False

    async def toggle_feature_status(self, user_id: int, feature_key: str) -> bool:
            if not self._db: return False
            current = await self.get_feature_status(user_id, feature_key)
            new_state = 0 if current else 1
            await self._db.execute(
                "INSERT INTO user_features (user_id, feature_key, is_enabled) "
                "VALUES (?, ?, ?) ON CONFLICT(user_id, feature_key) DO UPDATE SET is_enabled=?",
                (user_id, feature_key, new_state, new_state)
            )
            await self._db.commit()

    async def get_user_auto_capital(self, user_id: int, allowed_banks: list[str] | set[str] | None = None) -> float:
        """
        Calculates the user's maximum available capital based on the sum of
        available balances/limits on all active and healthy connected cards.
        If allowed_banks is provided, calculates capital per-bank (grouping by bank)
        and returns the max single bank's capital.
        """
        if not self._db:
            return 0.0
            
        # Get all active cards
        cards = await self.get_cards(user_id, status="active")
        if not cards:
            return 0.0

        # Normalization map for banks
        name_map = {
            "43": "monobank", "mono": "monobank", "monobank": "monobank", "моно": "monobank", "монобанк": "monobank",
            "14": "privatbank", "pb": "privatbank", "privat": "privatbank", "privatbank": "privatbank", "приват": "privatbank", "приватбанк": "privatbank",
            "64": "pumb", "pumb": "pumb", "пумб": "pumb",
            "48": "a-bank", "abank": "a-bank", "a-bank": "a-bank", "абанк": "a-bank", "а-банк": "a-bank",
            "553": "izibank", "izi": "izibank", "izibank": "izibank", "ізі": "izibank", "ізібанк": "izibank",
            "328": "sense", "sense": "sense", "sensebank": "sense", "сенс": "sense", "сенсбанк": "sense"
        }
        
        def normalize_bank_name(name: str) -> str:
            if not name:
                return ""
            name_low = str(name).strip().lower()
            return name_map.get(name_low, name_low)

        if allowed_banks is not None:
            allowed_banks_norm = {normalize_bank_name(b) for b in allowed_banks}
        else:
            allowed_banks_norm = None
            
        # Get user settings for card warmup limit
        settings = await self.get_user_card_settings(user_id)
        cold_card_limit = float(settings.get("cold_card_limit", 2000.0)) if settings else 2000.0

        bank_capitals = {}  # bank_norm -> float
        now = time.time()
        
        for card in cards:
            # 1. Cooldown check
            if card.get("cooldown_until", 0) > now:
                continue
                
            bank_norm = normalize_bank_name(card.get("bank_name", ""))
            if allowed_banks_norm is not None and bank_norm not in allowed_banks_norm:
                continue
                
            card_id = card["id"]
            
            # 2. Get limits (default to buy/out limits since capital is buy budget)
            limits = await self.get_card_effective_limits(card_id)
            max_tx = limits.get("max_tx_per_day", 15)
            daily_out = limits.get("daily_out_max", 150000.0)
            monthly_out = limits.get("monthly_out_max", 400000.0)
            
            # 3. Daily tx count check
            tx_count = await self.get_card_transactions_count(card_id, hours=24)
            if tx_count >= max_tx:
                continue
                
            # 4. Rolling used limits
            used_daily = await self.get_rolling_used(card_id, "out", hours=24)
            used_monthly = await self.get_rolling_used(card_id, "out", hours=24*30)
            
            avail_daily = max(0.0, daily_out - used_daily)
            avail_monthly = max(0.0, monthly_out - used_monthly)
            
            # Money we can actually spend from this card: bounded by balance and limits
            card_avail = min(
                float(card.get("balance", 0.0)),
                avail_daily,
                avail_monthly
            )
            
            # Get pending out amount for this card
            pending_out = 0.0
            async with self._db.execute(
                """
                SELECT SUM(l.amount) as total
                FROM card_order_legs l
                JOIN card_orders o ON l.order_id = o.id
                WHERE l.card_id = ? AND l.leg_status = 'pending' AND o.direction = 'out'
                """,
                (card_id,)
            ) as cur:
                row = await cur.fetchone()
                if row and row["total"]:
                    pending_out = float(row["total"])
                    
            card_avail = max(0.0, card_avail - pending_out)
            
            if card_avail > 0:
                # Check warmup limits
                tx_count_total, last_tx_ts = await self.get_card_warmth_stats(card_id)
                warmup_limit = self.get_card_warmup_limit(card, tx_count_total, last_tx_ts, now, cold_card_limit)
                if warmup_limit is not None:
                    card_avail = min(card_avail, warmup_limit)

                if card_avail > 0:
                    bank_capitals[bank_norm] = bank_capitals.get(bank_norm, 0.0) + card_avail
                
        if allowed_banks_norm is not None:
            return max(bank_capitals.values()) if bank_capitals else 0.0
        else:
            return sum(bank_capitals.values())

    async def get_card_warmth_stats(self, card_id: str) -> tuple[int, float]:
        """
        Returns (total_tx_count, last_tx_timestamp) from card_transactions table.
        If no transactions exist, returns (0, 0.0).
        """
        if not self._db:
            return 0, 0.0
        async with self._db.execute(
            "SELECT COUNT(id) as cnt, MAX(timestamp) as last_ts FROM card_transactions WHERE card_id=?",
            (card_id,)
        ) as cur:
            row = await cur.fetchone()
            if row:
                cnt = int(row["cnt"]) if row["cnt"] is not None else 0
                last_ts = float(row["last_ts"]) if row["last_ts"] is not None else 0.0
                return cnt, last_ts
        return 0, 0.0

    def get_card_warmup_limit(self, card: dict, tx_count_total: int, last_tx_ts: float, now: float, cold_card_limit: float = 2000.0) -> float | None:
        """
        Returns the warmup limit for a card if it is not warm, or None if it is warm.
        """
        is_warm = bool(card.get("is_warmed_up", 0))
        if is_warm:
            return None

        if cold_card_limit <= 0:
            return None

        # Auto-warmup rule: 10+ transactions and active within last 30 days
        if tx_count_total >= 10 and (now - last_tx_ts) <= 30 * 86400:
            return None

        # Determine warmup limits scaled proportionally to base limit
        if tx_count_total <= 2:
            return cold_card_limit
        elif tx_count_total <= 5:
            return 2.5 * cold_card_limit
        elif tx_count_total <= 9:
            return 5.0 * cold_card_limit
        else:
            # tx_count_total >= 10 but dormant (>30 days inactive)
            return 2.5 * cold_card_limit
