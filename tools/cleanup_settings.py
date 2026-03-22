import sqlite3
import os

# Автоматично знаходимо корінь проекту (на рівень вище від папки tools)
base_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
db_path = os.path.join(base_dir, 'data', 'merchants.db')

print(f"Підключаюсь до БД: {db_path}")

# Підключаємось по абсолютному шляху
conn = sqlite3.connect(db_path)

# Видаляємо старі глобальні ключі
conn.execute("DELETE FROM bot_settings WHERE key IN ('working_capital_uah', 'min_spread_pct')")
conn.commit()

# Перевіряємо, що залишилося
rows = conn.execute("SELECT key, value FROM bot_settings").fetchall()
print("Залишилось в bot_settings:")
for r in rows:
    print(f"  {r[0]} = {r[1]}")

conn.close()
print("Done")