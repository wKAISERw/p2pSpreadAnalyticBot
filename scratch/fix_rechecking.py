"""
Скрипт для діагностики та виправлення застряглих RECHECKING записів в БД.
Застрягнути може якщо LLM завершив роботу але TTL _recent_calls вже вичерпався,
і наступний цикл знову позначив мерчанта RECHECKING, але schedule() повернув False
(черга переповнена) — тоді RECHECKING залишається назавжди.
"""
import sqlite3
import time
import glob
import os

db_paths = glob.glob("data/*.db") + glob.glob("*.db")
print("Знайдені БД:", db_paths)

STUCK_THRESHOLD_SECONDS = 300  # Вважаємо застряглим якщо > 5 хвилин в RECHECKING

for db_path in db_paths:
    try:
        con = sqlite3.connect(db_path)
        con.row_factory = sqlite3.Row

        cur = con.execute(
            "SELECT exchange, merchant_id, merchant_name, trade_recommendation, verdict, updated_at "
            "FROM merchant_verdict WHERE trade_recommendation = 'RECHECKING'"
        )
        rows = cur.fetchall()

        if rows:
            print(f"\n[{db_path}] Застряглі RECHECKING мерчанти:")
            stuck = []
            for r in rows:
                age = time.time() - (r["updated_at"] or 0)
                print(f"  {r['merchant_name']} [{r['exchange']}] verdict={r['verdict']} age={age:.0f}s")
                if age > STUCK_THRESHOLD_SECONDS:
                    stuck.append((r["exchange"], r["merchant_id"], r["verdict"]))

            if stuck:
                print(f"\n  → Виправляємо {len(stuck)} застряглих записів...")
                for exchange, mid, verdict in stuck:
                    # Відновлюємо trade_recommendation з вердикту
                    derive = {"OK": "APPROVE", "SUSPICIOUS": "CONDITIONAL", "BLOCK": "REJECT"}
                    new_rec = derive.get(verdict, "CONDITIONAL")
                    con.execute(
                        "UPDATE merchant_verdict SET trade_recommendation = ? WHERE exchange = ? AND merchant_id = ?",
                        (new_rec, exchange, mid)
                    )
                    print(f"  ✅ {mid} [{exchange}]: RECHECKING → {new_rec} (з вердикту {verdict})")
                con.commit()
                print("  Зміни збережено.")
            else:
                print("  Всі RECHECKING — свіжі (< 5 хв), виправлення не потрібне.")
        else:
            print(f"[{db_path}] Чисто — RECHECKING записів немає.")

        con.close()
    except Exception as e:
        print(f"Помилка {db_path}: {e}")

print("\nГотово.")
