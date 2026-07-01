# core/merchant_db.py
# =============================================================================
# БОЙОВА ВЕРСІЯ v1.0  (Крок 1: WAL + канонічна версія)
# =============================================================================
"""
Асинхронна SQLite база мерчантів (aiosqlite).

Задачі:
- кеш вердиктів по merchant_id
- risk_score та лічильники звернень
- глобальний blacklist
- review cache для Binance / Bybit / OKX

WAL (Write-Ahead Logging):
- вмикається при старті через PRAGMA journal_mode=WAL
- захищає від database locked при паралельних async читаннях/записах
- особливо важливо коли LLMWorkerPool і ReviewFetcher пишуть одночасно
"""

from __future__ import annotations
import re
import hashlib
import logging
import time
import uuid
from pathlib import Path
from typing import Optional
import aiohttp  # 🚀 ДОДАЙ ЦЕЙ РЯДОК СЮДИ
import aiosqlite
from core.utils.crypto import encrypt, decrypt

logger = logging.getLogger("MerchantDB")

DB_PATH = Path("data/merchants.db")

TTL = {
    "OK": 43_200,
    "SUSPICIOUS": 43_200,
    "BLOCK": 259_200,
    "UNKNOWN": 1_800,
}
DEFAULT_TTL = 43_200

VERDICT_SCORE = {
    "OK": 0,
    "SUSPICIOUS": 30,
    "BLOCK": 100,
    "UNKNOWN": 10,
}

LLM_SOURCES = {"groq", "gemini", "llm"}


def hash_terms(trade_terms: str) -> str:
    # 🚀 Викидаємо цифри (таймштампи), пунктуацію та емодзі.
    # Тепер зміна "Оновлено о 20:15" не створить новий виклик LLM.
    import re
    normalized = re.sub(r'[\d\W_]+', '', (trade_terms or "").lower())
    return hashlib.md5(normalized.encode()).hexdigest()


class MerchantDB:
    def __init__(self, db_path: Path = DB_PATH):
        self._path = db_path
        self._db: Optional[aiosqlite.Connection] = None

    async def start(self) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._db = await aiosqlite.connect(str(self._path))
        self._db.row_factory = aiosqlite.Row

        # WAL: захист від database locked при паралельних async операціях.
        # LLMWorkerPool (2 воркери) + ReviewFetcher пишуть одночасно —
        # без WAL можливі помилки при конкурентному доступі.
        await self._db.execute("PRAGMA journal_mode=WAL")
        await self._db.execute("PRAGMA synchronous=NORMAL")  # безпечно + швидше
        await self._db.execute("PRAGMA cache_size=-65536")  # 64 MB кеш
        await self._db.execute("PRAGMA foreign_keys=ON")
        await self._db.commit()

        # Check if sent_alerts has the correct primary key, if not, drop it (it's just a 30 min cache)
        try:
            async with self._db.execute("PRAGMA table_info(sent_alerts)") as cur:
                rows = await cur.fetchall()
            pk_cols = [r["name"] for r in rows if r["pk"] > 0]
            if pk_cols and len(pk_cols) < 4:
                logger.info("Migrating sent_alerts table: dropping old PK constraint")
                await self._db.execute("DROP TABLE sent_alerts")
                await self._db.commit()
        except Exception as e:
            logger.warning("Error checking/dropping sent_alerts table: %s", e)

        await self._init_schema()
        
        # Apply migrations for new columns safely
        try:
            await self._db.execute("ALTER TABLE cards ADD COLUMN card_number TEXT")
        except Exception: pass
        try:
            await self._db.execute("ALTER TABLE cards ADD COLUMN mono_x_token_encrypted TEXT")
        except Exception: pass
        try:
            await self._db.execute("ALTER TABLE cards ADD COLUMN mono_webhook_secret TEXT")
        except Exception: pass
        try:
            await self._db.execute("ALTER TABLE cards ADD COLUMN is_warmed_up INTEGER DEFAULT 0")
        except Exception: pass
        try:
            await self._db.execute("ALTER TABLE scanner_proposals ADD COLUMN user_id INTEGER DEFAULT 0")
        except Exception: pass
        await self._db.commit()
        await self._db.execute("""
                               CREATE TABLE IF NOT EXISTS user_features
                               (
                                   user_id
                                   INTEGER,
                                   feature_key
                                   TEXT,
                                   is_enabled
                                   INTEGER
                                   DEFAULT
                                   0,
                                   PRIMARY
                                   KEY
                               (
                                   user_id,
                                   feature_key
                               )
                                   );
                               """)
        await self._db.commit()
        # 🚀 АВТОМАТИЧНА МІГРАЦІЯ: Додаємо нові колонки конфігурації виводу карт
        # Якщо колонки вже є — блок просто пропустить їх, якщо немає — м'яко накотить у SQLite.
        import sqlite3

        columns_to_add = [
            ("card_output_mode", "TEXT DEFAULT 'inline'"),
            ("enable_smart_spoiler", "INTEGER DEFAULT 1"),
            ("card_detail_level", "TEXT DEFAULT 'full'"),
            ("enable_in_single_modes", "INTEGER DEFAULT 0"),
            ("show_balances_breakdown", "INTEGER DEFAULT 1"),
            ("show_transfer_tips", "INTEGER DEFAULT 1")
        ]

        for col_name, col_type in columns_to_add:
            try:
                await self._db.execute(f"ALTER TABLE user_card_settings ADD COLUMN {col_name} {col_type};")
                logging.getLogger("MerchantDB").info(f"✨ Міграція: додано колонку {col_name} в user_card_settings")
            except sqlite3.OperationalError as e:
                # Якщо помилка каже, що колонка вже існує — просто ігноруємо її
                if "duplicate column name" in str(e).lower():
                    pass
                else:
                    logging.getLogger("MerchantDB").error(f"🔥 Помилка міграції поля {col_name}: {e}")

        await self._db.commit()
        logger.info("MerchantDB запущено (WAL): %s", self._path)

    async def stop(self) -> None:
        if self._db:
            await self._db.close()
        logger.info("MerchantDB зупинено")

    async def _init_schema(self) -> None:
        await self._db.executescript("""
                                     CREATE TABLE IF NOT EXISTS merchant_verdict
                                     (
                                         exchange
                                         TEXT
                                         NOT
                                         NULL,
                                         merchant_id
                                         TEXT
                                         NOT
                                         NULL,
                                         merchant_name
                                         TEXT,
                                         terms_hash
                                         TEXT,
                                         verdict
                                         TEXT
                                         DEFAULT
                                         'UNKNOWN',
                                         risk_type
                                         TEXT,
                                         reason
                                         TEXT,
                                         risk_score
                                         INTEGER
                                         DEFAULT
                                         0,
                                         llm_calls_count
                                         INTEGER
                                         DEFAULT
                                         0,
                                         save_count
                                         INTEGER
                                         DEFAULT
                                         0,
                                         updated_at
                                         REAL
                                         DEFAULT
                                         0,
                                         PRIMARY
                                         KEY
                                     (
                                         exchange,
                                         merchant_id
                                     )
                                         );

                                     CREATE TABLE IF NOT EXISTS global_blacklist
                                     (
                                         exchange
                                         TEXT
                                         NOT
                                         NULL,
                                         merchant_id
                                         TEXT
                                         NOT
                                         NULL,
                                         merchant_name
                                         TEXT,
                                         reason
                                         TEXT,
                                         source
                                         TEXT,
                                         added_at
                                         REAL
                                         DEFAULT
                                         0,
                                         PRIMARY
                                         KEY
                                     (
                                         exchange,
                                         merchant_id
                                     )
                                         );

                                     CREATE TABLE IF NOT EXISTS block_log
                                     (
                                         exchange
                                         TEXT
                                         NOT
                                         NULL,
                                         merchant_id
                                         TEXT
                                         NOT
                                         NULL,
                                         merchant_name
                                         TEXT,
                                         verdict
                                         TEXT,
                                         risk_type
                                         TEXT,
                                         reason
                                         TEXT,
                                         source
                                         TEXT,
                                         logged_at
                                         REAL
                                         DEFAULT
                                         0
                                     );

                                     CREATE TABLE IF NOT EXISTS merchant_reviews
                                     (
                                         exchange
                                         TEXT
                                         NOT
                                         NULL,
                                         merchant_id
                                         TEXT
                                         NOT
                                         NULL,
                                         positive_count
                                         INTEGER
                                         DEFAULT
                                         0,
                                         negative_count
                                         INTEGER
                                         DEFAULT
                                         0,
                                         neutral_count
                                         INTEGER
                                         DEFAULT
                                         0,
                                         bad_texts_json
                                         TEXT
                                         DEFAULT
                                         '[]',
                                         updated_at
                                         REAL
                                         DEFAULT
                                         0,
                                         PRIMARY
                                         KEY
                                     (
                                         exchange,
                                         merchant_id
                                     )
                                         );

                                     /* 🚀 ДОДАЄМО ТАБЛИЦЮ СНАПШОТІВ */
                                     CREATE TABLE IF NOT EXISTS merchant_snapshots
                                     (
                                         exchange
                                         TEXT
                                         NOT
                                         NULL,
                                         merchant_id
                                         TEXT
                                         NOT
                                         NULL,
                                         merchant_name
                                         TEXT,
                                         side
                                         TEXT,
                                         price
                                         REAL,
                                         min_limit
                                         REAL,
                                         max_limit
                                         REAL,
                                         order_count
                                         INTEGER,
                                         finish_rate
                                         REAL,
                                         is_verified
                                         INTEGER,
                                         terms_hash
                                         TEXT,
                                         recorded_at
                                         REAL
                                         NOT
                                         NULL
                                     );
                                    CREATE TABLE IF NOT EXISTS merchant_review_history (
                                            id INTEGER PRIMARY KEY AUTOINCREMENT,
                                            exchange TEXT NOT NULL,
                                            merchant_id TEXT NOT NULL,
                                            positive_count INTEGER DEFAULT 0,
                                            negative_count INTEGER DEFAULT 0,
                                            neg_pct REAL DEFAULT 0.0,
                                            recorded_at REAL NOT NULL
                                        );
                                    CREATE INDEX IF NOT EXISTS idx_rev_hist ON merchant_review_history(exchange, merchant_id, recorded_at);
                                     CREATE INDEX IF NOT EXISTS idx_verdict_lookup
                                         ON merchant_verdict (exchange, merchant_id);

                                     /* 🚀 ІНДЕКСИ ДЛЯ СНАПШОТІВ */
                                     CREATE INDEX IF NOT EXISTS idx_snap_lookup
                                         ON merchant_snapshots (exchange, merchant_id, recorded_at);

                                     CREATE INDEX IF NOT EXISTS idx_snap_ts
                                         ON merchant_snapshots (recorded_at);

                                     /* 🚀 Для find_digital_twins — без цього індексу full table scan */
                                     CREATE INDEX IF NOT EXISTS idx_snap_name
                                         ON merchant_snapshots (merchant_name, exchange, recorded_at);

                                     /* 🚀 Для get_best_sell_price — фільтрація по side */
                                     CREATE INDEX IF NOT EXISTS idx_snap_side
                                         ON merchant_snapshots (side, exchange, recorded_at);

                                     -- API credentials (зашифровані Fernet)
                                     -- user_id: Telegram user_id (0 = legacy/single-user)
                                     CREATE TABLE IF NOT EXISTS user_credentials (
                                         user_id     INTEGER NOT NULL DEFAULT 0,
                                         exchange    TEXT NOT NULL,
                                         api_key     TEXT NOT NULL,
                                         api_secret  TEXT NOT NULL,
                                         passphrase  TEXT DEFAULT '',
                                         label       TEXT DEFAULT '',
                                         created_at  REAL DEFAULT 0,
                                         updated_at  REAL DEFAULT 0,
                                         PRIMARY KEY (user_id, exchange)
                                     );

                                     -- Runtime overrides від UI бота
                                     -- user_id: 0 = глобальні (single-user), >0 = персональні
                                     CREATE TABLE IF NOT EXISTS bot_settings (
                                         user_id    INTEGER NOT NULL DEFAULT 0,
                                         key        TEXT NOT NULL,
                                         value      TEXT NOT NULL,
                                         updated_at REAL DEFAULT 0,
                                         PRIMARY KEY (user_id, key)
                                     );

                                     -- Підписники сканера (multi-user)
                                     CREATE TABLE IF NOT EXISTS scanner_users (
                                         user_id          INTEGER NOT NULL PRIMARY KEY,
                                         telegram_chat_id INTEGER NOT NULL,
                                         working_capital  REAL DEFAULT 5100.0,
                                         min_spread_pct   REAL DEFAULT 0.5,
                                         max_spread_pct   REAL DEFAULT 0.0,
                                         spread_strategy  TEXT DEFAULT 'min',
                                         bank_codes       TEXT DEFAULT '43,14,64',
                                         is_active        INTEGER DEFAULT 1,
                                         created_at       REAL DEFAULT 0
                                     );
                                     -- Сесії з браузера (перехоплені Headers та Cookies)
                                     CREATE TABLE IF NOT EXISTS auth_sessions (
                                         user_id      INTEGER NOT NULL DEFAULT 0,
                                         exchange     TEXT NOT NULL,
                                         headers_json TEXT DEFAULT '{}',
                                         cookies_json TEXT DEFAULT '{}',
                                         updated_at   REAL DEFAULT 0,
                                         PRIMARY KEY (user_id, exchange)
                                     );

                                     -- Таблиця: trade_sessions
                                     CREATE TABLE IF NOT EXISTS trade_sessions (
                                         id              INTEGER PRIMARY KEY AUTOINCREMENT,
                                         strategy        TEXT NOT NULL,
                                         route_type      TEXT NOT NULL,
                                         buy_exchange    TEXT,           -- 🚀 ДОДАНО
                                         buy_leg_id      INTEGER,
                                         sell_leg_id     INTEGER,
                                         session_status  TEXT NOT NULL DEFAULT 'OPEN',
                                         network         TEXT,
                                         network_fee     REAL DEFAULT 0,
                                         gross_profit    REAL,
                                         created_at      DATETIME DEFAULT CURRENT_TIMESTAMP,
                                         completed_at    DATETIME
                                     );

                                     -- Таблиця: active_trades
                                     CREATE TABLE IF NOT EXISTS active_trades (
                                         id               INTEGER PRIMARY KEY AUTOINCREMENT,
                                         session_id       INTEGER REFERENCES trade_sessions(id),
                                         strategy         TEXT,
                                         leg              TEXT,
                                         route_type       TEXT,
                                         network          TEXT,
                                         network_fee      REAL DEFAULT 0,
                                         expires_at       DATETIME,
                                         owner_user_id    INTEGER DEFAULT NULL,
                                         exchange         TEXT,
                                         order_id         TEXT UNIQUE,
                                         ad_id            TEXT,
                                         asset            TEXT,
                                         fiat             TEXT,
                                         price            REAL,
                                         amount           REAL,
                                         fiat_amount      REAL,
                                         counterparty_id  TEXT,
                                         counterparty_name TEXT,
                                         status           TEXT NOT NULL,
                                         created_at       DATETIME DEFAULT CURRENT_TIMESTAMP,
                                         updated_at       DATETIME
                                     );

                                     -- 🚀 Пропозиції сканера (для статистики "що знаходив сканер")
                                     CREATE TABLE IF NOT EXISTS scanner_proposals (
                                         id              INTEGER PRIMARY KEY AUTOINCREMENT,
                                         buy_exchange    TEXT,
                                         sell_exchange   TEXT,
                                         buy_merchant    TEXT,
                                         sell_merchant   TEXT,
                                         spread_pct      REAL,
                                         profit_uah      REAL,
                                         deal_amount     REAL,
                                         route_type      TEXT,
                                         buy_bank        TEXT,
                                         sell_bank       TEXT,
                                         was_sent        INTEGER DEFAULT 0,
                                         user_id         INTEGER DEFAULT 0,
                                         created_at      REAL NOT NULL
                                     );
                                      CREATE INDEX IF NOT EXISTS idx_proposals_ts
                                          ON scanner_proposals(created_at);

                                      -- 🚀 Відправлені алерти (для гасіння дублів та перемальовки)
                                      CREATE TABLE IF NOT EXISTS sent_alerts (
                                          exchange TEXT NOT NULL,
                                          merchant_id TEXT NOT NULL,
                                          chat_id INTEGER NOT NULL,
                                          message_ids_json TEXT NOT NULL, -- JSON list of message IDs
                                          sent_at REAL NOT NULL,
                                          alert_json TEXT NOT NULL,        -- serialized SpreadAlert
                                          display_settings_json TEXT NOT NULL,
                                          is_sniper_match INTEGER DEFAULT 0,
                                          PRIMARY KEY (exchange, merchant_id, chat_id, message_ids_json)
                                      );
                                      CREATE INDEX IF NOT EXISTS idx_sent_alerts_lookup 
                                          ON sent_alerts(exchange, merchant_id, sent_at);

                                     /* =========================================================
                                        БЛОК УПРАВЛІННЯ КАРТКАМИ (CARD MANAGEMENT SYSTEM)
                                        ========================================================= */

                                     CREATE TABLE IF NOT EXISTS user_card_settings (
                                         user_id              INTEGER PRIMARY KEY,
                                         card_module_mode     TEXT DEFAULT 'off',
                                         max_cards_per_order  INTEGER DEFAULT 3,
                                         card_output_mode     TEXT DEFAULT 'inline',
                                         enable_smart_spoiler INTEGER DEFAULT 1,
                                         card_detail_level    TEXT DEFAULT 'full',
                                         enable_in_single_modes INTEGER DEFAULT 0,
                                         show_balances_breakdown INTEGER DEFAULT 1,
                                         show_transfer_tips   INTEGER DEFAULT 1
                                     );

                                     CREATE TABLE IF NOT EXISTS user_bank_limits (
                                         user_id              INTEGER NOT NULL,
                                         bank_name            TEXT NOT NULL,
                                         daily_out_max        REAL DEFAULT 150000.0,
                                         daily_in_max         REAL DEFAULT 150000.0,
                                         monthly_out_max      REAL DEFAULT 400000.0,
                                         monthly_in_max       REAL DEFAULT 400000.0,
                                         max_single_tx_out    REAL DEFAULT 29999.0,
                                         max_single_tx_in     REAL DEFAULT 29999.0,
                                         max_tx_per_day       INTEGER DEFAULT 15,
                                         cooldown_hours       INTEGER DEFAULT 24,
                                         PRIMARY KEY (user_id, bank_name)
                                     );

                                     CREATE TABLE IF NOT EXISTS cards (
                                         id                   TEXT PRIMARY KEY,
                                         owner_id             INTEGER NOT NULL,
                                         bank_name            TEXT NOT NULL,
                                         last_four            TEXT NOT NULL,
                                         label                TEXT,
                                         is_own               INTEGER DEFAULT 1,
                                         category             TEXT DEFAULT 'self',
                                         note                 TEXT,
                                         is_custom_limits     INTEGER DEFAULT 0,
                                         limits_override_json TEXT DEFAULT '{}',
                                         balance              REAL DEFAULT 0.0,
                                         balance_updated_at   REAL DEFAULT 0,
                                         status               TEXT DEFAULT 'active',
                                         cooldown_until       REAL DEFAULT 0,
                                         last_monthly_reset   REAL DEFAULT 0,
                                         created_at           REAL DEFAULT 0,
                                         last_tx_timestamp    REAL DEFAULT 0,
                                         mono_account_id      TEXT,
                                         card_number          TEXT,
                                         mono_x_token_encrypted TEXT,
                                         mono_webhook_secret  TEXT,
                                         is_warmed_up         INTEGER DEFAULT 0
                                     );
                                     CREATE INDEX IF NOT EXISTS idx_cards_owner_status ON cards(owner_id, status);
                                     
                                     CREATE TABLE IF NOT EXISTS user_mono_settings (
                                         user_id              INTEGER PRIMARY KEY,
                                         x_token_encrypted    TEXT,
                                         webhook_secret       TEXT
                                     );

                                     CREATE TABLE IF NOT EXISTS card_orders (
                                         id                       TEXT PRIMARY KEY,
                                         owner_id                 INTEGER NOT NULL,
                                         trade_session_id         INTEGER,
                                         total_amount             REAL NOT NULL,
                                         target_bank              TEXT NOT NULL,
                                         direction                TEXT NOT NULL,
                                         status                   TEXT DEFAULT 'pending',
                                         completed_amount         REAL DEFAULT 0.0,
                                         expected_window_minutes  INTEGER DEFAULT 30,
                                         created_at               REAL DEFAULT 0
                                     );
                                     CREATE INDEX IF NOT EXISTS idx_card_orders_status ON card_orders(status);

                                     CREATE TABLE IF NOT EXISTS card_order_legs (
                                         id                       INTEGER PRIMARY KEY AUTOINCREMENT,
                                         order_id                 TEXT NOT NULL,
                                         card_id                  TEXT NOT NULL,
                                         amount                   REAL NOT NULL,
                                         leg_status               TEXT DEFAULT 'pending',
                                         FOREIGN KEY(order_id) REFERENCES card_orders(id) ON DELETE CASCADE,
                                         FOREIGN KEY(card_id) REFERENCES cards(id) ON DELETE CASCADE
                                     );

                                     CREATE TABLE IF NOT EXISTS card_transactions (
                                         id                       TEXT PRIMARY KEY,
                                         card_id                  TEXT NOT NULL,
                                         amount                   REAL NOT NULL,
                                         direction                TEXT NOT NULL,
                                         type                     TEXT DEFAULT 'work',
                                         linked_order_id          TEXT,
                                         source                   TEXT DEFAULT 'manual',
                                         timestamp                REAL DEFAULT 0,
                                         FOREIGN KEY(card_id) REFERENCES cards(id) ON DELETE CASCADE
                                     );
                                     CREATE INDEX IF NOT EXISTS idx_card_txs_time ON card_transactions(card_id, timestamp);
                                     """)

        await self._db.commit()

        await self._ensure_column("cards", "last_tx_timestamp", "REAL DEFAULT 0")
        await self._ensure_column("cards", "mono_account_id", "TEXT")
        
        await self._ensure_column("merchant_verdict", "save_count", "INTEGER DEFAULT 0")
        await self._ensure_column("merchant_verdict", "llm_decision", "TEXT DEFAULT 'UNKNOWN'")
        await self._ensure_column("merchant_verdict", "trade_recommendation", "TEXT DEFAULT 'PENDING'")
        await self._ensure_column("merchant_reviews", "status", "TEXT DEFAULT 'OK'")
        await self._ensure_column("merchant_reviews", "error_reason", "TEXT DEFAULT ''")
        # Міграція колонок scanner_users
        await self._ensure_column("scanner_users", "capital_mode", "TEXT DEFAULT 'manual'")
        await self._ensure_column("scanner_users", "min_amount_uah", "REAL DEFAULT 0.0")
        await self._ensure_column("scanner_users", "merchant_filters_json", "TEXT DEFAULT '{}'")
        await self._ensure_column("scanner_users", "is_alerts_active", "INTEGER DEFAULT 1")
        # 🚀 ДОДАНО: Окремі банки для покупки і продажу
        await self._ensure_column("scanner_users", "buy_bank_codes", "TEXT DEFAULT ''")
        await self._ensure_column("scanner_users", "sell_bank_codes", "TEXT DEFAULT ''")
        # 🚀 ДОДАНО: Персональні фільтри мерчантів per-exchange
        await self._ensure_column("scanner_users", "exchange_merchant_filters_json", "TEXT DEFAULT '{}'")
        # 🚀 ДОДАНО: Галочка показу вижимки ЛЛМ в алерті
        await self._ensure_column("scanner_users", "show_llm_summary", "INTEGER DEFAULT 1")
        # 🔘 Налаштування виводу повідомлень (per-user)
        await self._ensure_column("scanner_users", "show_ai_terms_summary",
                                  "INTEGER DEFAULT 1")  # AI вижимка умов (inline)
        await self._ensure_column("scanner_users", "show_full_terms", "INTEGER DEFAULT 1")  # Повні умови (спойлер)
        await self._ensure_column("scanner_users", "show_ai_logic", "INTEGER DEFAULT 1")  # Логіка AI (спойлер)
        await self._ensure_column("scanner_users", "show_bank_details", "INTEGER DEFAULT 1")  # Деталі банків (спойлер)
        await self._ensure_column("scanner_users", "is_hybrid_routes_enabled",
                                  "INTEGER DEFAULT 0")  # Підтримка кнопок T-M та M-T
        await self._ensure_column("scanner_users", "alert_cooldown", "REAL DEFAULT -1.0")  # Затримка алертів (AUTO = -1.0)
        await self._ensure_column("scanner_users", "group_active_alerts", "INTEGER DEFAULT 1")  # Групування команди /active
        await self._ensure_column("scanner_users", "group_scanner_alerts", "INTEGER DEFAULT 1")  # Групування авто-алертів сканера
        await self._ensure_column("scanner_users", "filter_fop_tov", "TEXT DEFAULT 'hide'")  # Фільтр ФОП/ТОВ (hide/warn/show)
        await self._ensure_column("scanner_users", "filter_banka_jar", "TEXT DEFAULT 'hide'")  # Фільтр Банка/Сейф (hide/warn/show)
        await self._ensure_column("scanner_users", "auto_cooldown_json", "TEXT DEFAULT '{}'")  # Налаштування автоматичного кд (JSON)
        # 🚀 AI вижимка умов мерчанта
        await self._ensure_column("merchant_verdict", "terms_summary", "TEXT DEFAULT ''")
        await self._ensure_column("merchant_verdict", "reviews_analysis", "TEXT DEFAULT ''")
        # 🔥 ДОДАНО СЕКЦІЮ ДЛЯ ПРОТУХШИХ СЕСІЙ
        await self._ensure_column("auth_sessions", "is_active", "INTEGER DEFAULT 1")
        # 🚀 МІГРАЦІЯ ДЛЯ ТОРГОВИХ СЕСІЙ
        await self._ensure_column("trade_sessions", "buy_exchange", "TEXT")
        # 🚀 BLOCK D: payment_method для аналітики банків
        await self._ensure_column("active_trades", "payment_method", "TEXT DEFAULT ''")
        # 🚀 ФАЗА 1: Режим сканера (SPREAD / TAKER_BUY / TAKER_SELL / MAKER_BUY / MAKER_SELL)
        await self._ensure_column("scanner_users", "scanner_mode", "TEXT DEFAULT 'SPREAD'")
        # 🚀 ФАЗА 2: Фільтр по діапазону ціни для тейкер-режимів
        await self._ensure_column("scanner_users", "price_range_json", "TEXT DEFAULT '{}'")
        # 🚀 ФАЗА 3: Maker — ціна купівлі (MAKER_SELL) + цільова маржа (MAKER_BUY)
        await self._ensure_column("scanner_users", "maker_buy_price", "REAL DEFAULT 0.0")
        await self._ensure_column("scanner_users", "target_margin", "REAL DEFAULT 0.005")
        # 🚀 ФАЗА 4: Taker Sell параметри (сума, ціна, профіт, швидкість)
        await self._ensure_column("scanner_users", "taker_sell_amount", "REAL DEFAULT 0.0")
        await self._ensure_column("scanner_users", "taker_sell_price", "REAL DEFAULT 0.0")
        await self._ensure_column("scanner_users", "taker_sell_profit", "REAL DEFAULT 0.0")
        await self._ensure_column("scanner_users", "taker_sell_speed", "TEXT DEFAULT 'FAST'")
        # ── Нові колонки для рефакторингу TAKER FSM ──────────────────────────
        await self._ensure_column("scanner_users", "taker_sell_exchange", "TEXT DEFAULT ''")
        await self._ensure_column("scanner_users", "taker_sell_min_price", "REAL DEFAULT 0.0")
        await self._ensure_column("scanner_users", "taker_buy_amount", "REAL DEFAULT 0.0")
        await self._ensure_column("scanner_users", "taker_buy_max_price", "REAL DEFAULT 0.0")
        await self._ensure_column("scanner_users", "taker_buy_limit_min", "REAL DEFAULT 0.0")
        await self._ensure_column("scanner_users", "taker_buy_limit_max", "REAL DEFAULT 0.0")
        await self._ensure_column("scanner_users", "taker_buy_speed", "TEXT DEFAULT 'ANY'")

        await self._ensure_column("scanner_users", "taker_buy_price_strategy", "TEXT DEFAULT 'any'")
        await self._ensure_column("scanner_users", "taker_buy_price_from", "REAL DEFAULT 0.0")
        await self._ensure_column("scanner_users", "taker_sell_price_strategy", "TEXT DEFAULT 'roi'")
        await self._ensure_column("scanner_users", "taker_sell_price_to", "REAL DEFAULT 0.0")
        await self._ensure_column("scanner_users", "spread_strategy", "TEXT DEFAULT 'min'")
        await self._ensure_column("scanner_users", "max_spread_pct", "REAL DEFAULT 0.0")
        # 🚀 SNIPER: правила снайпер-моду (JSON масив)
        await self._ensure_column("scanner_users", "sniper_rules", "TEXT DEFAULT '[]'")
        try:
            await self.db.execute("ALTER TABLE snapshots ADD COLUMN created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP")
        except Exception:
            pass  # Колонка вже існує

        try:
            await self.db.execute("ALTER TABLE proposals ADD COLUMN created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP")
        except Exception:
            pass  # Колонка вже існує

    async def _ensure_column(self, table: str, column: str, ddl: str) -> None:
        async with self._db.execute(f"PRAGMA table_info({table})") as cur:
            rows = await cur.fetchall()
        cols = {row["name"] for row in rows}
        if column not in cols:
            await self._db.execute(f"ALTER TABLE {table} ADD COLUMN {column} {ddl}")
            await self._db.commit()

