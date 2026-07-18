# core/storage/user_repo.py
# User registration, credentials, auth sessions, settings, sniper rules
from __future__ import annotations
import logging, time
from typing import Optional
import aiosqlite
from core.utils.crypto import encrypt, decrypt
logger = logging.getLogger(__name__)

class UserRepo:
    """Users, credentials, auth sessions, settings."""

    async def save_credentials(
            self,
            exchange: str,
            api_key: str,
            api_secret: str,
            passphrase: str = "",
            label: str = "",
            user_id: int = 0,
    ) -> bool:
        """
        Зберігає API ключі для біржі (зашифровано Fernet).
        user_id=0 → single-user режим (зворотна сумісність).
        user_id>0 → multi-user: ключі прив'язані до конкретного Telegram user.
        """
        if not self._db:
            return False
        import time
        try:
            now = time.time()
            await self._db.execute(
                """INSERT INTO user_credentials
                   (user_id, exchange, api_key, api_secret, passphrase, label, created_at, updated_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?) ON CONFLICT(user_id, exchange) DO
                UPDATE SET
                    api_key = excluded.api_key,
                    api_secret = excluded.api_secret,
                    passphrase = excluded.passphrase,
                    label = excluded.label,
                    updated_at = excluded.updated_at""",
                (
                    user_id,
                    exchange,
                    encrypt(api_key),
                    encrypt(api_secret),
                    encrypt(passphrase) if passphrase else "",
                    label,
                    now, now,
                ),
            )
            await self._db.commit()
            logger.info("Credentials saved for %s (user_id=%d)", exchange, user_id)
            return True
        except Exception as e:
            logger.error("save_credentials [%s]: %s", exchange, e)
            return False

    async def get_credentials(self, exchange: str, user_id: int = 0) -> dict | None:
        """
        Повертає розшифровані credentials для біржі або None.
        user_id=0 → single-user (зворотна сумісність).
        """
        if not self._db:
            return None
        try:
            async with self._db.execute(
                    "SELECT api_key, api_secret, passphrase, label FROM user_credentials WHERE user_id=? AND exchange = ?",
                    (user_id, exchange),
            ) as cur:
                row = await cur.fetchone()
            if not row:
                return None
            return {
                "api_key": decrypt(row["api_key"]),
                "api_secret": decrypt(row["api_secret"]),
                "passphrase": decrypt(row["passphrase"]) if row["passphrase"] else "",
                "label": row["label"] or "",
            }
        except Exception as e:
            logger.error("get_credentials [%s]: %s", exchange, e)
            return None

    async def get_all_credentials(self, user_id: int = 0) -> dict[str, dict]:
        """
        Повертає всі credentials як {exchange: {...}}.
        user_id=0 → single-user режим (зворотна сумісність).
        """
        if not self._db:
            return {}
        try:
            async with self._db.execute(
                    "SELECT exchange, api_key, api_secret, passphrase, label FROM user_credentials WHERE user_id=?",
                    (user_id,),
            ) as cur:
                rows = await cur.fetchall()
            return {
                row["exchange"]: {
                    "api_key": decrypt(row["api_key"]),
                    "api_secret": decrypt(row["api_secret"]),
                    "passphrase": decrypt(row["passphrase"]) if row["passphrase"] else "",
                    "label": row["label"] or "",
                }
                for row in rows
            }
        except Exception as e:
            logger.error("get_all_credentials: %s", e)
            return {}

    async def delete_credentials(self, exchange: str, user_id: int = 0) -> bool:
        """Видаляє credentials для біржі."""
        if not self._db:
            return False
        try:
            await self._db.execute(
                "DELETE FROM user_credentials WHERE user_id=? AND exchange = ?", (user_id, exchange,)
            )
            await self._db.commit()
            logger.info("Credentials deleted for %s", exchange)
            return True
        except Exception as e:
            logger.error("delete_credentials [%s]: %s", exchange, e)
            return False

    async def has_credentials(self, exchange: str, user_id: int = 0) -> bool:
        """Швидка перевірка чи є ключі для біржі."""
        if not self._db:
            return False
        try:
            async with self._db.execute(
                    "SELECT 1 FROM user_credentials WHERE user_id=? AND exchange = ? LIMIT 1", (user_id, exchange,)
            ) as cur:
                return await cur.fetchone() is not None
        except Exception:
            return False

        # ═══════════════════════════════════════════════════════════════════════
        # Browser Auth Sessions (Interceptor)
        # ═══════════════════════════════════════════════════════════════════════

    async def save_auth_session(
            self,
            exchange: str,
            headers_dict: dict,
            cookies_dict: dict,
            user_id: int = 0
    ) -> bool:
        """Зберігає перехоплені браузерні заголовки та кукіси."""
        if not self._db:
            return False

        # 🚀 ФІКС: Не зберігаємо сесію OKX, якщо в ній відсутній обов'язковий заголовок authorization
        if exchange == "OKX":
            headers_lower = {k.lower(): v for k, v in headers_dict.items()}
            if "authorization" not in headers_lower or not headers_lower["authorization"]:
                logger.warning("save_auth_session: Пропуск збереження сесії OKX через відсутність authorization header")
                return False

        import json
        import time
        try:
            now = time.time()
            headers_json = json.dumps(headers_dict, ensure_ascii=False)
            cookies_json = json.dumps(cookies_dict, ensure_ascii=False)

            await self._db.execute(
                """INSERT INTO auth_sessions
                       (user_id, exchange, headers_json, cookies_json, updated_at, is_active)
                   VALUES (?, ?, ?, ?, ?, 1) ON CONFLICT(user_id, exchange) DO
                UPDATE SET
                    headers_json = excluded.headers_json,
                    cookies_json = excluded.cookies_json,
                    updated_at = excluded.updated_at,
                    is_active = 1""",
                (user_id, exchange, headers_json, cookies_json, now),
            )
            await self._db.commit()
            logger.info("Auth session saved for %s (user_id=%d)", exchange, user_id)
            return True
        except Exception as e:
            logger.error("save_auth_session [%s]: %s", exchange, e)
            return False

    async def get_auth_session(self, exchange: str, user_id: int = 0) -> tuple[dict, dict, float]:
        """Повертає (headers_dict, cookies_dict, updated_at). Якщо немає — ({}, {}, 0.0)"""
        if not self._db:
            return {}, {}, 0.0
        import json
        try:
            async with self._db.execute(
                    "SELECT headers_json, cookies_json, updated_at FROM auth_sessions WHERE user_id=? AND exchange=? AND is_active=1",
                    (user_id, exchange),
            ) as cur:
                row = await cur.fetchone()

            if not row:
                # 🚀 ДОДАНО ФОЛБЕК: Якщо для вказаного користувача немає активної сесії (наприклад, для фонових тасок з user_id=0),
                # завантажуємо будь-яку останню активну сесію для цієї біржі
                async with self._db.execute(
                        "SELECT headers_json, cookies_json, updated_at FROM auth_sessions WHERE exchange=? AND is_active=1 ORDER BY updated_at DESC LIMIT 1",
                        (exchange,),
                ) as cur:
                    row = await cur.fetchone()

            if not row:
                return {}, {}, 0.0

            headers = json.loads(row["headers_json"] or "{}")
            cookies = json.loads(row["cookies_json"] or "{}")
            return headers, cookies, float(row["updated_at"])

        except Exception as e:
            logger.error("get_auth_session [%s]: %s", exchange, e)
            return {}, {}, 0.0

    async def invalidate_auth_session(self, exchange: str, user_id: int = 0) -> bool:
        """Позначає сесію як протухшу (is_active=0)."""
        if not self._db:
            return False
        try:
            if user_id == 0:
                # 🚀 ДОДАНО: Якщо анулюємо сесію глобально (user_id=0), анулюємо ВСІ активні сесії для цієї біржі
                await self._db.execute(
                    "UPDATE auth_sessions SET is_active=0 WHERE exchange=?",
                    (exchange,)
                )
            else:
                await self._db.execute(
                    "UPDATE auth_sessions SET is_active=0 WHERE user_id=? AND exchange=?",
                    (user_id, exchange)
                )
            await self._db.commit()
            logger.warning("Auth session invalidated (burnt out) for %s (user_id=%d)", exchange, user_id)
            return True
        except Exception as e:
            logger.error("invalidate_auth_session [%s]: %s", exchange, e)
            return False

    # ═══════════════════════════════════════════════════════════════════════
    # Scanner Users (multi-user)
    # ═══════════════════════════════════════════════════════════════════════

    async def register_user(
            self,
            user_id: int,
            chat_id: int,
            working_capital: float = 5100.0,
            min_spread_pct: float = 0.5,
            bank_codes: list[str] | None = None,
    ) -> bool:
        """Реєструє нового підписника сканера."""
        if not self._db:
            return False
        import time
        try:
            banks_str = ",".join(bank_codes or ["43", "14", "64"])
            await self._db.execute(
                """INSERT INTO scanner_users
                   (user_id, telegram_chat_id, working_capital, min_spread_pct, bank_codes, is_active, created_at)
                   VALUES (?, ?, ?, ?, ?, 1, ?) ON CONFLICT(user_id) DO
                UPDATE SET
                    telegram_chat_id = excluded.telegram_chat_id,
                    is_active = 1""",
                (user_id, chat_id, working_capital, min_spread_pct, banks_str, time.time()),
            )
            await self._db.commit()
            return True
        except Exception as e:
            logger.error("register_user [%d]: %s", user_id, e)
            return False

    async def get_active_users(self) -> list[dict]:
        """Повертає всіх активних підписників для розсилки алертів."""
        if not self._db:
            return []
        try:
            async with self._db.execute(
                    """SELECT user_id,
                              telegram_chat_id,
                              working_capital,
                              COALESCE(capital_mode, 'manual')              as capital_mode,
                              COALESCE(min_amount_uah, 0.0)                  as min_amount_uah,
                               min_spread_pct,
                               COALESCE(spread_strategy, 'min')              as spread_strategy,
                               COALESCE(max_spread_pct, 0.0)                  as max_spread_pct,
                              bank_codes,
                              COALESCE(buy_bank_codes, '')                   as buy_bank_codes,
                              COALESCE(sell_bank_codes, '')                  as sell_bank_codes,
                              COALESCE(merchant_filters_json, '{}')          as merchant_filters_json,
                              COALESCE(exchange_merchant_filters_json, '{}') as exchange_merchant_filters_json,
                              COALESCE(is_alerts_active, 1)                  as is_alerts_active,
                              COALESCE(scanner_mode, 'SPREAD')               as scanner_mode,
                              COALESCE(price_range_json, '{}')               as price_range_json,
                              COALESCE(maker_buy_price, 0.0)                 as maker_buy_price,
                              COALESCE(target_margin, 0.005)                 as target_margin,
                              COALESCE(sniper_rules, '[]')                   as sniper_rules,
                              COALESCE(sniper_rules, '')             AS sniper_rules,
                            -- ── TAKER SELL ────────────────────────────────────────
                              COALESCE(taker_sell_amount, 0.0)       AS taker_sell_amount,
                              COALESCE(taker_sell_price, 0.0)        AS taker_sell_price,
                              COALESCE(taker_sell_exchange, '')      AS taker_sell_exchange,
                              COALESCE(taker_sell_profit, 0.0)       AS taker_sell_profit,
                              COALESCE(taker_sell_min_price, 0.0)    AS taker_sell_min_price,
                              COALESCE(taker_sell_speed, 'ANY')      AS taker_sell_speed,
                              COALESCE(taker_sell_price_strategy, 'roi') AS taker_sell_price_strategy,
                              COALESCE(taker_sell_price_to, 0.0)     AS taker_sell_price_to,
                             -- ── TAKER BUY ─────────────────────────────────────────
                              COALESCE(taker_buy_amount, 0.0)        AS taker_buy_amount,
                              COALESCE(taker_buy_max_price, 0.0)     AS taker_buy_max_price,
                              COALESCE(taker_buy_limit_min, 0.0)     AS taker_buy_limit_min,
                              COALESCE(taker_buy_limit_max, 0.0)     AS taker_buy_limit_max,
                              COALESCE(taker_buy_speed, 'ANY')       AS taker_buy_speed,
                              COALESCE(taker_buy_price_strategy, 'any') AS taker_buy_price_strategy,
                              COALESCE(taker_buy_price_from, 0.0)    AS taker_buy_price_from
                       FROM scanner_users
                       WHERE is_active = 1
                         AND COALESCE(is_alerts_active, 1) = 1"""
            ) as cur:
                rows = await cur.fetchall()
            import json as _json
            from config.banks import BANK_NAMES
            valid_keys = set(BANK_NAMES.keys())
            result = []
            for row in rows:
                general_banks = [c for c in (row["bank_codes"].split(",") if row["bank_codes"] else []) if c in valid_keys]
                buy_codes_raw = row["buy_bank_codes"].strip()
                sell_codes_raw = row["sell_bank_codes"].strip()
                # Fallback: якщо buy/sell порожні — використовуємо загальні
                buy_banks = [c for c in (buy_codes_raw.split(",") if buy_codes_raw else general_banks) if c in valid_keys]
                sell_banks = [c for c in (sell_codes_raw.split(",") if sell_codes_raw else general_banks) if c in valid_keys]
                result.append({
                    "user_id": row["user_id"],
                    "chat_id": row["telegram_chat_id"],
                    "capital": float(row["working_capital"]),
                    "capital_mode": row["capital_mode"] or "manual",
                    "min_amount": float(row["min_amount_uah"]),
                    "min_spread": float(row["min_spread_pct"]),
                    "max_spread": float(row["max_spread_pct"]),
                    "spread_strategy": row["spread_strategy"] or "min",
                    "bank_codes": general_banks,
                    "buy_bank_codes": buy_banks,
                    "sell_bank_codes": sell_banks,
                    "merchant_filters": _json.loads(row["merchant_filters_json"] or "{}"),
                    "exchange_merchant_filters": _json.loads(row["exchange_merchant_filters_json"] or "{}"),
                    "scanner_mode": row["scanner_mode"] or "SPREAD",
                    "price_range": _json.loads(row["price_range_json"] or "{}"),
                    "maker_buy_price": float(row["maker_buy_price"]),
                    "target_margin": float(row["target_margin"]),
                    "sniper_rules": _json.loads(row["sniper_rules"] or "[]"),
                    # ── TAKER SELL ────────────────────────────────────
                    "taker_sell_amount": float(row["taker_sell_amount"]),
                    "taker_sell_price": float(row["taker_sell_price"]),
                    "taker_sell_exchange": row["taker_sell_exchange"],
                    "taker_sell_profit": float(row["taker_sell_profit"]),
                    "taker_sell_min_price": float(row["taker_sell_min_price"]),
                    "taker_sell_speed": row["taker_sell_speed"],
                    "taker_sell_price_strategy": row["taker_sell_price_strategy"],
                    "taker_sell_price_to": float(row["taker_sell_price_to"]),
                    # ── TAKER BUY ─────────────────────────────────────
                    "taker_buy_amount": float(row["taker_buy_amount"]),
                    "taker_buy_max_price": float(row["taker_buy_max_price"]),
                    "taker_buy_limit_min": float(row["taker_buy_limit_min"]),
                    "taker_buy_limit_max": float(row["taker_buy_limit_max"]),
                    "taker_buy_speed": row["taker_buy_speed"],
                    "taker_buy_price_strategy": row["taker_buy_price_strategy"],
                    "taker_buy_price_from": float(row["taker_buy_price_from"]),
                })
            return result
        except Exception as e:
            logger.error("get_active_users: %s", e)
            return []

    async def get_user_by_id(self, user_id: int) -> dict | None:
        """Повертає параметри конкретного підписника за його user_id."""
        if not self._db:
            return None
        try:
            async with self._db.execute(
                    """SELECT user_id,
                              telegram_chat_id,
                              working_capital,
                              COALESCE(capital_mode, 'manual')              as capital_mode,
                              COALESCE(min_amount_uah, 0.0)                  as min_amount_uah,
                               min_spread_pct,
                               COALESCE(spread_strategy, 'min')              as spread_strategy,
                               COALESCE(max_spread_pct, 0.0)                  as max_spread_pct,
                              bank_codes,
                              COALESCE(buy_bank_codes, '')                   as buy_bank_codes,
                              COALESCE(sell_bank_codes, '')                  as sell_bank_codes,
                              COALESCE(merchant_filters_json, '{}')          as merchant_filters_json,
                              COALESCE(exchange_merchant_filters_json, '{}') as exchange_merchant_filters_json,
                              COALESCE(is_alerts_active, 1)                  as is_alerts_active,
                              COALESCE(scanner_mode, 'SPREAD')               as scanner_mode,
                              COALESCE(price_range_json, '{}')               as price_range_json,
                              COALESCE(maker_buy_price, 0.0)                 as maker_buy_price,
                              COALESCE(target_margin, 0.005)                 as target_margin,
                              COALESCE(sniper_rules, '[]')                   as sniper_rules,
                              COALESCE(sniper_rules, '')             AS sniper_rules,
                            -- ── TAKER SELL ────────────────────────────────────────
                              COALESCE(taker_sell_amount, 0.0)       AS taker_sell_amount,
                              COALESCE(taker_sell_price, 0.0)        AS taker_sell_price,
                              COALESCE(taker_sell_exchange, '')      AS taker_sell_exchange,
                              COALESCE(taker_sell_profit, 0.0)       AS taker_sell_profit,
                              COALESCE(taker_sell_min_price, 0.0)    AS taker_sell_min_price,
                              COALESCE(taker_sell_speed, 'ANY')      AS taker_sell_speed,
                              COALESCE(taker_sell_price_strategy, 'roi') AS taker_sell_price_strategy,
                              COALESCE(taker_sell_price_to, 0.0)     AS taker_sell_price_to,
                             -- ── TAKER BUY ─────────────────────────────────────────
                              COALESCE(taker_buy_amount, 0.0)        AS taker_buy_amount,
                              COALESCE(taker_buy_max_price, 0.0)     AS taker_buy_max_price,
                              COALESCE(taker_buy_limit_min, 0.0)     AS taker_buy_limit_min,
                              COALESCE(taker_buy_limit_max, 0.0)     AS taker_buy_limit_max,
                              COALESCE(taker_buy_speed, 'ANY')       AS taker_buy_speed,
                              COALESCE(taker_buy_price_strategy, 'any') AS taker_buy_price_strategy,
                              COALESCE(taker_buy_price_from, 0.0)    AS taker_buy_price_from
                       FROM scanner_users
                       WHERE user_id = ?""",
                    (user_id,),
            ) as cur:
                row = await cur.fetchone()
            if not row:
                return None
            import json as _json
            from config.banks import BANK_NAMES
            valid_keys = set(BANK_NAMES.keys())
            general_banks = [c for c in (row["bank_codes"].split(",") if row["bank_codes"] else []) if c in valid_keys]
            buy_codes_raw = row["buy_bank_codes"].strip()
            sell_codes_raw = row["sell_bank_codes"].strip()
            # Fallback
            buy_banks = [c for c in (buy_codes_raw.split(",") if buy_codes_raw else general_banks) if c in valid_keys]
            sell_banks = [c for c in (sell_codes_raw.split(",") if sell_codes_raw else general_banks) if c in valid_keys]
            return {
                "user_id": row["user_id"],
                "chat_id": row["telegram_chat_id"],
                "capital": float(row["working_capital"]),
                "capital_mode": row["capital_mode"] or "manual",
                "min_amount": float(row["min_amount_uah"]),
                "min_spread": float(row["min_spread_pct"]),
                "max_spread": float(row["max_spread_pct"]),
                "spread_strategy": row["spread_strategy"] or "min",
                "bank_codes": general_banks,
                "buy_bank_codes": buy_banks,
                "sell_bank_codes": sell_banks,
                "merchant_filters": _json.loads(row["merchant_filters_json"] or "{}"),
                "exchange_merchant_filters": _json.loads(row["exchange_merchant_filters_json"] or "{}"),
                "scanner_mode": row["scanner_mode"] or "SPREAD",
                "price_range": _json.loads(row["price_range_json"] or "{}"),
                "maker_buy_price": float(row["maker_buy_price"]),
                "target_margin": float(row["target_margin"]),
                "sniper_rules": _json.loads(row["sniper_rules"] or "[]"),
                # ── TAKER SELL ────────────────────────────────────
                "taker_sell_amount": float(row["taker_sell_amount"]),
                "taker_sell_price": float(row["taker_sell_price"]),
                "taker_sell_exchange": row["taker_sell_exchange"],
                "taker_sell_profit": float(row["taker_sell_profit"]),
                "taker_sell_min_price": float(row["taker_sell_min_price"]),
                "taker_sell_speed": row["taker_sell_speed"],
                "taker_sell_price_strategy": row["taker_sell_price_strategy"],
                "taker_sell_price_to": float(row["taker_sell_price_to"]),
                # ── TAKER BUY ─────────────────────────────────────
                "taker_buy_amount": float(row["taker_buy_amount"]),
                "taker_buy_max_price": float(row["taker_buy_max_price"]),
                "taker_buy_limit_min": float(row["taker_buy_limit_min"]),
                "taker_buy_limit_max": float(row["taker_buy_limit_max"]),
                "taker_buy_speed": row["taker_buy_speed"],
                "taker_buy_price_strategy": row["taker_buy_price_strategy"],
                "taker_buy_price_from": float(row["taker_buy_price_from"]),
            }
        except Exception as e:
            logger.error("get_user_by_id [%d]: %s", user_id, e)
            return None

    async def get_user_display_settings(self, chat_id: int) -> dict:
        """
        Повертає per-user налаштування виводу повідомлень.
        Ключі: show_ai_terms_summary, show_full_terms, show_ai_logic,
               show_bank_details, show_llm_summary, alert_cooldown, group_active_alerts, auto_cooldown_json
        """
        import json
        default_auto_cooldown = {
            "window_seconds": 5.0,
            "tiers": [
                {"threshold": 10, "delay": 0.0},
                {"threshold": 15, "delay": 0.3},
                {"threshold": 20, "delay": 0.8},
                {"threshold": 9999, "delay": 1.5}
            ]
        }
        defaults = {
            "show_ai_terms_summary": True,
            "show_full_terms": True,
            "show_ai_logic": True,
            "show_bank_details": True,
            "show_llm_summary": True,
            "is_hybrid_routes_enabled": False,
            "alert_cooldown": -1.0,
            "group_active_alerts": True,
            "group_scanner_alerts": True,
            "filter_fop_tov": "hide",
            "filter_banka_jar": "hide",
            "auto_cooldown_json": default_auto_cooldown,
            "cryptobot_profile_mode": "chat",
        }
        if not self._db:
            return defaults
        try:
            async with self._db.execute(
                    """SELECT COALESCE(show_ai_terms_summary, 1)    as show_ai_terms_summary,
                              COALESCE(show_full_terms, 1)          as show_full_terms,
                              COALESCE(show_ai_logic, 1)            as show_ai_logic,
                              COALESCE(show_bank_details, 1)        as show_bank_details,
                              COALESCE(show_llm_summary, 1)         as show_llm_summary,
                              COALESCE(is_hybrid_routes_enabled, 0) as is_hybrid_routes_enabled,
                              COALESCE(alert_cooldown, -1.0)        as alert_cooldown,
                              COALESCE(group_active_alerts, 1)      as group_active_alerts,
                              COALESCE(group_scanner_alerts, 1)     as group_scanner_alerts,
                              COALESCE(filter_fop_tov, 'hide')      as filter_fop_tov,
                              COALESCE(filter_banka_jar, 'hide')    as filter_banka_jar,
                              COALESCE(auto_cooldown_json, '{}')    as auto_cooldown_json,
                              COALESCE(cryptobot_profile_mode, 'chat') as cryptobot_profile_mode
                       FROM scanner_users
                       WHERE telegram_chat_id = ?""",
                    (chat_id,),
            ) as cur:
                row = await cur.fetchone()
            if not row:
                return defaults

            # Parse auto_cooldown_json
            raw_json = row["auto_cooldown_json"]
            try:
                auto_cooldown = json.loads(raw_json) if raw_json and raw_json != "{}" else default_auto_cooldown
                if not isinstance(auto_cooldown, dict) or "tiers" not in auto_cooldown:
                    auto_cooldown = default_auto_cooldown
            except Exception:
                auto_cooldown = default_auto_cooldown

            return {
                "show_ai_terms_summary": bool(row["show_ai_terms_summary"]),
                "show_full_terms": bool(row["show_full_terms"]),
                "show_ai_logic": bool(row["show_ai_logic"]),
                "show_bank_details": bool(row["show_bank_details"]),
                "show_llm_summary": bool(row["show_llm_summary"]),
                "is_hybrid_routes_enabled": bool(row["is_hybrid_routes_enabled"]),
                "alert_cooldown": float(row["alert_cooldown"]),
                "group_active_alerts": bool(row["group_active_alerts"]),
                "group_scanner_alerts": bool(row["group_scanner_alerts"]),
                "filter_fop_tov": str(row["filter_fop_tov"]),
                "filter_banka_jar": str(row["filter_banka_jar"]),
                "auto_cooldown_json": auto_cooldown,
                "cryptobot_profile_mode": str(row["cryptobot_profile_mode"]),
            }
        except Exception:
            return defaults

    async def update_user_display_settings(self, chat_id: int, settings_dict: dict) -> None:
        """Оновлює per-user налаштування виводу повідомлень."""
        if not self._db:
            return
        
        show_ai_terms_summary = 1 if settings_dict.get("show_ai_terms_summary", True) else 0
        show_full_terms = 1 if settings_dict.get("show_full_terms", True) else 0
        show_ai_logic = 1 if settings_dict.get("show_ai_logic", True) else 0
        show_bank_details = 1 if settings_dict.get("show_bank_details", True) else 0
        show_llm_summary = 1 if settings_dict.get("show_llm_summary", True) else 0
        is_hybrid_routes_enabled = 1 if settings_dict.get("is_hybrid_routes_enabled", False) else 0
        alert_cooldown = float(settings_dict.get("alert_cooldown", -1.0))
        group_active_alerts = 1 if settings_dict.get("group_active_alerts", True) else 0
        group_scanner_alerts = 1 if settings_dict.get("group_scanner_alerts", True) else 0
        filter_fop_tov = str(settings_dict.get("filter_fop_tov", "hide"))
        filter_banka_jar = str(settings_dict.get("filter_banka_jar", "hide"))
        cryptobot_profile_mode = str(settings_dict.get("cryptobot_profile_mode", "chat"))

        await self._db.execute(
            """UPDATE scanner_users
               SET show_ai_terms_summary = ?,
                   show_full_terms = ?,
                   show_ai_logic = ?,
                   show_bank_details = ?,
                   show_llm_summary = ?,
                   is_hybrid_routes_enabled = ?,
                   alert_cooldown = ?,
                   group_active_alerts = ?,
                   group_scanner_alerts = ?,
                   filter_fop_tov = ?,
                   filter_banka_jar = ?,
                   cryptobot_profile_mode = ?
               WHERE telegram_chat_id = ?""",
            (
                show_ai_terms_summary,
                show_full_terms,
                show_ai_logic,
                show_bank_details,
                show_llm_summary,
                is_hybrid_routes_enabled,
                alert_cooldown,
                group_active_alerts,
                group_scanner_alerts,
                filter_fop_tov,
                filter_banka_jar,
                cryptobot_profile_mode,
                chat_id,
            ),
        )
        await self._db.commit()

    async def update_user_auto_cooldown_json(self, chat_id: int, config: dict) -> None:
        """Оновлює auto_cooldown_json для користувача."""
        if not self._db:
            return
        import json
        json_str = json.dumps(config)
        await self._db.execute(
            "UPDATE scanner_users SET auto_cooldown_json = ? WHERE telegram_chat_id = ?",
            (json_str, chat_id),
        )
        await self._db.commit()


    async def find_digital_twins(self, merchant_name: str, exclude_exchange: str, minutes: int = 15) -> list[dict]:
        """Шукає унікальні стани лімітів двійників за короткий час (дедуплікація на рівні бази)."""
        if not self._db or not merchant_name:
            return []

        since = time.time() - (minutes * 60)

        async with self._db.execute(
                """
                SELECT DISTINCT exchange, min_limit, max_limit
                FROM merchant_snapshots
                WHERE merchant_name = ? COLLATE NOCASE
                  AND exchange!=? AND recorded_at > ?
                """,
                (merchant_name, exclude_exchange, since),
        ) as cur:
            rows = await cur.fetchall()

        return [dict(r) for r in rows]

    async def get_verdict_timestamp(self, exchange: str, merchant_id: str) -> float:
        if not self._db: return 0.0
        try:
            async with self._db.execute(
                    "SELECT updated_at FROM merchant_verdict WHERE exchange=? AND merchant_id=? ORDER BY updated_at DESC LIMIT 1",
                    (exchange, merchant_id),
            ) as cur:
                row = await cur.fetchone()
            return float(row["updated_at"]) if row else 0.0
        except Exception:
            return 0.0

    async def save_review_snapshot(self, exchange: str, merchant_id: str, pos: int, neg: int, neg_pct: float) -> None:
        if not self._db: return
        await self._db.execute(
            "INSERT INTO merchant_review_history (exchange, merchant_id, positive_count, negative_count, neg_pct, recorded_at) VALUES (?, ?, ?, ?, ?, ?)",
            (exchange, merchant_id, pos, neg, neg_pct, time.time())
        )
        await self._db.commit()

    async def get_review_trend(self, exchange: str, merchant_id: str, days: int = 7) -> dict:
        if not self._db: return {"trend": "stable", "delta": 0.0}
        since = time.time() - (days * 86400)
        async with self._db.execute(
                "SELECT neg_pct FROM merchant_review_history WHERE exchange=? AND merchant_id=? AND recorded_at > ? ORDER BY recorded_at ASC",
                (exchange, merchant_id, since)
        ) as cur:
            rows = await cur.fetchall()

        if len(rows) < 2: return {"trend": "stable", "delta": 0.0}
        delta = float(rows[-1]["neg_pct"]) - float(rows[0]["neg_pct"])
        trend = "worsening" if delta > 3.0 else "improving" if delta < -3.0 else "stable"
        return {"trend": trend, "delta": delta}

    async def get_sniper_rules(self, user_id: int) -> list:
        if not self._db: return []
        async with self._db.execute(
                "SELECT sniper_rules FROM scanner_users WHERE user_id = ?", (user_id,)
        ) as cur:
            row = await cur.fetchone()
            if row and row["sniper_rules"]:
                import json
                try:
                    return json.loads(row["sniper_rules"])
                except Exception:
                    pass
            return []

    async def update_sniper_rules(self, user_id: int, rules: list) -> None:
        if not self._db: return
        import json
        rules_str = json.dumps(rules)
        await self._db.execute(
            "UPDATE scanner_users SET sniper_rules = ? WHERE user_id = ?", (rules_str, user_id)
        )
        await self._db.commit()

    async def toggle_hybrid_routes(self, user_id: int) -> bool:
        """Перемикає стан експериментальних гібридних маршрутів для юзера."""
        if not self._db: return False
        try:
            async with self._db.execute(
                    "SELECT COALESCE(is_hybrid_routes_enabled, 0) FROM scanner_users WHERE user_id=?",
                    (user_id,)) as cur:
                row = await cur.fetchone()
            if not row: return False
            new_val = 0 if row[0] else 1
            await self._db.execute("UPDATE scanner_users SET is_hybrid_routes_enabled = ? WHERE user_id = ?",
                                   (new_val, user_id))
            await self._db.commit()
            return bool(new_val)
        except Exception as e:
            logger.error("toggle_hybrid_routes error: %s", e)
            return False

    # ==========================================
    # ── БЛОК 1.5: ПРОПОЗИЦІЇ СКАНЕРА (PROPOSALS) ──
    # ==========================================

    # ==========================================
    # ── БЛОК 1.6: ВИКОРИСТАНІ СУБСИДІЇ (USED SUBSIDIES) ──
    # ==========================================

    async def mark_subsidy_used(
        self, user_id: int, exchange: str, subsidy_type: str = "new_user"
    ) -> bool:
        """Позначає субсидію як використану для user+exchange."""
        if not self._db:
            return False
        try:
            await self._db.execute(
                """INSERT OR REPLACE INTO used_subsidies
                   (user_id, exchange, subsidy_type, used_at)
                   VALUES (?, ?, ?, ?)""",
                (user_id, exchange, subsidy_type, time.time()),
            )
            await self._db.commit()
            logger.info("Subsidy marked used: user=%s exchange=%s type=%s", user_id, exchange, subsidy_type)
            return True
        except Exception as e:
            logger.error("mark_subsidy_used error: %s", e)
            return False

    async def unmark_subsidy_used(
        self, user_id: int, exchange: str, subsidy_type: str = "new_user"
    ) -> bool:
        """Скасовує позначку використаної субсидії (знову стає доступною)."""
        if not self._db:
            return False
        try:
            await self._db.execute(
                "DELETE FROM used_subsidies WHERE user_id=? AND exchange=? AND subsidy_type=?",
                (user_id, exchange, subsidy_type),
            )
            await self._db.commit()
            return True
        except Exception as e:
            logger.error("unmark_subsidy_used error: %s", e)
            return False

    async def get_used_subsidies(self, user_id: int) -> dict[str, list[str]]:
        """Повертає dict {exchange: [subsidy_type, ...]} використаних субсидій."""
        if not self._db:
            return {}
        try:
            async with self._db.execute(
                "SELECT exchange, subsidy_type FROM used_subsidies WHERE user_id=?",
                (user_id,),
            ) as cur:
                rows = await cur.fetchall()
            result: dict[str, list[str]] = {}
            for row in rows:
                ex = row["exchange"] if isinstance(row, aiosqlite.Row) else row[0]
                st = row["subsidy_type"] if isinstance(row, aiosqlite.Row) else row[1]
                result.setdefault(ex, []).append(st)
            return result
        except Exception as e:
            logger.error("get_used_subsidies error: %s", e)
            return {}

    async def is_subsidy_used(
        self, user_id: int, exchange: str, subsidy_type: str = "new_user"
    ) -> bool:
        """Перевіряє чи субсидія вже використана."""
        if not self._db:
            return False
        try:
            async with self._db.execute(
                "SELECT 1 FROM used_subsidies WHERE user_id=? AND exchange=? AND subsidy_type=?",
                (user_id, exchange, subsidy_type),
            ) as cur:
                return (await cur.fetchone()) is not None
        except Exception as e:
            logger.error("is_subsidy_used error: %s", e)
            return False
