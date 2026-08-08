# core/storage/user_repo.py
# User registration, credentials, auth sessions, settings, sniper rules
from __future__ import annotations
import logging, time
from typing import Optional
import aiosqlite
from core.utils.crypto import encrypt, decrypt
logger = logging.getLogger(__name__)

# Слот user_id=0 — це системний/легасі слот власника інсталяції, а не
# "спільний". Раніше на нього мовчки падали і креденшли, і сесії будь-якого
# юзера — тобто чужа людина торгувала ключами власника, а фонові задачі ходили
# під кукі випадкового юзера. Тепер до слота 0 прирівнюється тільки адмін.
OWNER_SLOT = 0


def _owner_user_id() -> int:
    """Telegram id власника інсталяції (ADMIN_ID, інакше TELEGRAM_CHAT_ID)."""
    try:
        from config import settings
        return int(getattr(settings, "admin_id", 0) or getattr(settings, "telegram_chat_id", 0) or 0)
    except Exception:
        return 0


def _is_owner(user_id: int) -> bool:
    owner = _owner_user_id()
    return bool(owner) and int(user_id) == owner


# Режими, які вміє сканер. Порядок фіксований: у такому вигляді вони
# показуються в меню і в такому ж обходяться в конвеєрі.
SCANNER_MODES = ("SPREAD", "TAKER_BUY", "TAKER_SELL", "MAKER_BUY", "MAKER_SELL")


def _parse_scanner_modes(raw: str | None, fallback_mode: str | None) -> list[str]:
    """
    Набір активних режимів користувача.

    Колонка scanner_modes з'явилась пізніше за scanner_mode, тому в старих
    рядках вона порожня — там єдиним режимом лишається той, що був. Завдяки
    цьому міграція не потрібна: перший же запис через меню чи API заповнить
    новий формат, а до того все працює як раніше.

    Порожній результат неможливий: користувач без жодного режиму не
    отримував би нічого і виглядав би як зламаний, тож падаємо на SPREAD.
    """
    known = set(SCANNER_MODES)
    modes = [m.strip().upper() for m in (raw or "").split(",") if m.strip()]
    modes = [m for m in modes if m in known]

    if not modes:
        single = (fallback_mode or "").strip().upper()
        modes = [single] if single in known else ["SPREAD"]

    # Порядок як у SCANNER_MODES, без дублів.
    return [m for m in SCANNER_MODES if m in set(modes)]


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

    async def get_credentials_for_user(self, exchange: str, user_id: int) -> dict | None:
        """
        Креденшли конкретного юзера — з фолбеком на слот власника ТІЛЬКИ для
        самого власника.

        Раніше в цьому місці стояв безумовний фолбек на user_id=0:

            creds = await db.get_credentials(exchange, user_id) or {}
            if not creds.get("api_key"):
                creds = await db.get_credentials(exchange, 0) or {}

        Через це будь-який юзер, не підключивши свої ключі, створював
        оголошення і угоди на акаунті власника. Окремо гірше те, що при
        зіпсованому ENCRYPTION_KEY decrypt() повертає порожній рядок — тобто
        фолбек спрацьовував ще й для тих, у кого ключі насправді Є.
        """
        creds = await self.get_credentials(exchange, user_id) or {}
        if creds.get("api_key"):
            return creds

        if _is_owner(user_id):
            legacy = await self.get_credentials(exchange, OWNER_SLOT) or {}
            if legacy.get("api_key"):
                logger.debug("Креденшли %s взято з легасі-слота власника", exchange)
                return legacy

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

            if not row and user_id == OWNER_SLOT:
                # Фонові задачі (review_fetcher, risk_engine) просять сесію під
                # слотом 0, а букмарклет зберігає її під реальним telegram id
                # власника. Тому для слота 0 підхоплюємо сесію ВЛАСНИКА.
                #
                # Раніше тут бралася "будь-яка остання активна сесія для цієї
                # біржі" — тобто фоновий фетчер ходив під кукі випадкового
                # юзера: його акаунт, його рейт-ліміти, його ризик бану.
                owner = _owner_user_id()
                if owner:
                    async with self._db.execute(
                            "SELECT headers_json, cookies_json, updated_at FROM auth_sessions "
                            "WHERE user_id=? AND exchange=? AND is_active=1",
                            (owner, exchange),
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
        """
        Позначає сесію як протухшу (is_active=0).

        Слот 0 гасить сесію власника (слот 0 + його реальний telegram id) —
        саме ту, під якою працюють фонові задачі. Раніше тут стояло
        `WHERE exchange=?` без user_id, тобто перший же AuthError у фетчера
        відгуків розлогінював УСІХ юзерів одразу.
        """
        if not self._db:
            return False
        try:
            if user_id == OWNER_SLOT:
                targets = [OWNER_SLOT]
                owner = _owner_user_id()
                if owner:
                    targets.append(owner)
                placeholders = ",".join("?" * len(targets))
                await self._db.execute(
                    f"UPDATE auth_sessions SET is_active=0 "
                    f"WHERE exchange=? AND user_id IN ({placeholders})",
                    (exchange, *targets),
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
                              COALESCE(scanner_modes, '')                    as scanner_modes,
                              COALESCE(price_range_json, '{}')               as price_range_json,
                              COALESCE(mode_bank_overrides_json, '{}')       as mode_bank_overrides_json,
                              COALESCE(maker_buy_price, 0.0)                 as maker_buy_price,
                              COALESCE(target_margin, 0.005)                 as target_margin,
                              COALESCE(sniper_rules, '[]')                   as sniper_rules,
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
                              COALESCE(taker_buy_price_from, 0.0)    AS taker_buy_price_from,
                             -- ── BUY BALANCE / AUTO-SCALE ──────────────────────────
                             -- Читаються в taker_scanner і в меню, але роками не
                             -- потрапляли в SELECT: меню показувало дефолт, сканер
                             -- поводився як CARD_ENFORCED незалежно від налаштування.
                              COALESCE(buy_balance_mode, 'CARD_ENFORCED') AS buy_balance_mode,
                              COALESCE(buy_auto_scale_down, 1)       AS buy_auto_scale_down,
                              COALESCE(buy_auto_scale_up, 1)         AS buy_auto_scale_up
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
                r_dict = dict(row)
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
                    "scanner_modes": _parse_scanner_modes(
                        row["scanner_modes"], row["scanner_mode"]
                    ),
                    "price_range": _json.loads(row["price_range_json"] or "{}"),
                    "mode_bank_overrides": _json.loads(row["mode_bank_overrides_json"] or "{}"),
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
                    # `or 1` тут був би багом: збережений 0 (авто-скейл ВИМКНЕНО) —
                    # це валідне значення, а не "порожньо". COALESCE у SELECT
                    # уже підставив дефолт для NULL.
                    "buy_balance_mode": r_dict.get("buy_balance_mode") or "CARD_ENFORCED",
                    "buy_auto_scale_down": int(r_dict.get("buy_auto_scale_down", 1)),
                    "buy_auto_scale_up": int(r_dict.get("buy_auto_scale_up", 1)),
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
                              COALESCE(scanner_modes, '')                    as scanner_modes,
                              COALESCE(price_range_json, '{}')               as price_range_json,
                              COALESCE(mode_bank_overrides_json, '{}')       as mode_bank_overrides_json,
                              COALESCE(maker_buy_price, 0.0)                 as maker_buy_price,
                              COALESCE(target_margin, 0.005)                 as target_margin,
                              COALESCE(sniper_rules, '[]')                   as sniper_rules,
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
                              COALESCE(taker_buy_price_from, 0.0)    AS taker_buy_price_from,
                             -- ── BUY BALANCE / AUTO-SCALE ──────────────────────────
                             -- Читаються в taker_scanner і в меню, але роками не
                             -- потрапляли в SELECT: меню показувало дефолт, сканер
                             -- поводився як CARD_ENFORCED незалежно від налаштування.
                              COALESCE(buy_balance_mode, 'CARD_ENFORCED') AS buy_balance_mode,
                              COALESCE(buy_auto_scale_down, 1)       AS buy_auto_scale_down,
                              COALESCE(buy_auto_scale_up, 1)         AS buy_auto_scale_up
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
            r_dict = dict(row)
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
                "scanner_modes": _parse_scanner_modes(
                    row["scanner_modes"], row["scanner_mode"]
                ),
                "price_range": _json.loads(row["price_range_json"] or "{}"),
                "mode_bank_overrides": _json.loads(row["mode_bank_overrides_json"] or "{}"),
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
                "buy_balance_mode": r_dict.get("buy_balance_mode") or "CARD_ENFORCED",
                "buy_auto_scale_down": int(r_dict.get("buy_auto_scale_down", 1)),
                "buy_auto_scale_up": int(r_dict.get("buy_auto_scale_up", 1)),
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
        """
        Пише снапшот відгуків — ТІЛЬКИ якщо лічильники змінилися.

        Раніше це був сліпий INSERT + commit на кожному аналізі кожного
        кандидата спреду. На бойовій базі це дало 67 209 рядків на 434
        мерчантів, а рекордсмен мав 8 447 снапшотів з одними й тими самими
        цифрами. Плюс окремий fsync на спільному з'єднанні — тобто кожен такий
        запис підвішував усі інші запити застосунку.

        Для тренду має значення лише ЗМІНА, тож дублікати не несуть інформації.
        """
        if not self._db:
            return

        async with self._db.execute(
            "SELECT positive_count, negative_count FROM merchant_review_history "
            "WHERE exchange=? AND merchant_id=? ORDER BY recorded_at DESC LIMIT 1",
            (exchange, merchant_id),
        ) as cur:
            last = await cur.fetchone()

        if last and int(last["positive_count"]) == int(pos) and int(last["negative_count"]) == int(neg):
            return  # нічого не змінилось — писати нічого

        await self._db.execute(
            "INSERT INTO merchant_review_history (exchange, merchant_id, positive_count, negative_count, neg_pct, recorded_at) VALUES (?, ?, ?, ?, ?, ?)",
            (exchange, merchant_id, pos, neg, neg_pct, time.time())
        )
        await self._db.commit()

    async def get_review_trend(self, exchange: str, merchant_id: str, days: int = 7) -> dict:
        """
        Тренд негативу за N днів: різниця між першим і останнім снапшотом.

        Читає все вікно одним запитом. Пробував розбити на два точкових
        (ASC LIMIT 1 + DESC LIMIT 1) — на бойових даних вийшло на 0.02 мс
        ПОВІЛЬНІШЕ: індекс робить скан дешевим, а зайвий await через aiosqlite
        коштує більше за самі рядки. Кількість рядків тримає під контролем
        дедуп у save_review_snapshot.
        """
        if not self._db:
            return {"trend": "stable", "delta": 0.0}
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

    async def update_taker_buy_amount(self, user_id: int, new_amount: float) -> None:
        if not self._db:
            return
        await self._db.execute("UPDATE scanner_users SET taker_buy_amount = ? WHERE user_id = ?", (new_amount, user_id))
        await self._db.commit()

    async def update_buy_balance_mode(self, user_id: int, mode: Optional[str] = None, scale_down: Optional[int] = None, scale_up: Optional[int] = None) -> None:
        if not self._db:
            return
        updates = []
        params = []
        if mode is not None:
            updates.append("buy_balance_mode = ?")
            params.append(mode)
        if scale_down is not None:
            updates.append("buy_auto_scale_down = ?")
            params.append(scale_down)
        if scale_up is not None:
            updates.append("buy_auto_scale_up = ?")
            params.append(scale_up)
        if not updates:
            return
        params.append(user_id)
        sql = f"UPDATE scanner_users SET {', '.join(updates)} WHERE user_id = ?"
        await self._db.execute(sql, tuple(params))
        await self._db.commit()
