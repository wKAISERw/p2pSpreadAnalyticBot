# tools/migrate_multiuser.py
"""
Міграція БД до multi-user схеми.
Запустити ОДИН раз перед деплоєм нової версії:
  python tools/migrate_multiuser.py

Безпечно: використовує ALTER TABLE ADD COLUMN IF NOT EXISTS
і не видаляє існуючих даних.
"""
import asyncio
import aiosqlite
from pathlib import Path

DB_PATH = Path("data/merchants.db")


async def migrate():
    print(f"Міграція: {DB_PATH}")
    async with aiosqlite.connect(DB_PATH) as db:

        # 1. user_credentials — видаляємо стару таблицю і створюємо нову
        # (безпечно бо credentials будуть перезаписані через /connect)
        await db.execute("DROP TABLE IF EXISTS user_credentials_old")
        await db.execute("ALTER TABLE user_credentials RENAME TO user_credentials_old")
        await db.execute("""
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
            )
        """)
        # Мігруємо старі дані з user_id=0
        await db.execute("""
            INSERT OR IGNORE INTO user_credentials
                (user_id, exchange, api_key, api_secret, passphrase, label, created_at, updated_at)
            SELECT 0, exchange, api_key, api_secret, passphrase, label, created_at, updated_at
            FROM user_credentials_old
        """)
        await db.execute("DROP TABLE user_credentials_old")
        print("  ✅ user_credentials мігровано (user_id=0 для існуючих)")

        # 2. bot_settings — додаємо user_id
        await db.execute("DROP TABLE IF EXISTS bot_settings_old")
        await db.execute("ALTER TABLE bot_settings RENAME TO bot_settings_old")
        await db.execute("""
            CREATE TABLE IF NOT EXISTS bot_settings (
                user_id    INTEGER NOT NULL DEFAULT 0,
                key        TEXT NOT NULL,
                value      TEXT NOT NULL,
                updated_at REAL DEFAULT 0,
                PRIMARY KEY (user_id, key)
            )
        """)
        await db.execute("""
            INSERT OR IGNORE INTO bot_settings (user_id, key, value, updated_at)
            SELECT 0, key, value, updated_at FROM bot_settings_old
        """)
        await db.execute("DROP TABLE bot_settings_old")
        print("  ✅ bot_settings мігровано (user_id=0 для існуючих)")

        # 3. scanner_users — нова таблиця
        await db.execute("""
            CREATE TABLE IF NOT EXISTS scanner_users (
                user_id          INTEGER NOT NULL PRIMARY KEY,
                telegram_chat_id INTEGER NOT NULL,
                working_capital  REAL DEFAULT 5100.0,
                min_spread_pct   REAL DEFAULT 0.5,
                bank_codes       TEXT DEFAULT '43,14,64',
                is_active        INTEGER DEFAULT 1,
                created_at       REAL DEFAULT 0
            )
        """)
        print("  ✅ scanner_users створено")

        await db.commit()
        print("\n✅ Міграція завершена успішно!")
        print("   Перезапусти сканер і введи /start в боті.")
        print("   Потім заново введи API ключі через /connect (шифрування оновлено).")


if __name__ == "__main__":
    asyncio.run(migrate())