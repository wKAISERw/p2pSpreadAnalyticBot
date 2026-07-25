# dashboard.py
import sqlite3
import os
from pathlib import Path
from collections import Counter

PROJECT_ROOT = Path(__file__).parent.parent

# Будуємо абсолютні шляхи
DB_PATH = PROJECT_ROOT / "data" / "merchants.db"
LOG_PATH = PROJECT_ROOT / "logs" / "llm_decisions.log"
def print_header(title: str):
    print(f"\n{'='*50}")
    print(f" 📊 {title}")
    print(f"{'='*50}")

def generate_report():
    if not DB_PATH.exists():
        print(f"❌ База даних {DB_PATH} не знайдена!")
        return

    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()

    print_header("ЗАГАЛЬНА СТАТИСТИКА БАЗИ (merchant_verdict)")
    cur.execute("SELECT verdict, COUNT(*) FROM merchant_verdict GROUP BY verdict")
    total_verdicts = 0
    for verdict, count in cur.fetchall():
        icon = "🚫" if verdict == "BLOCK" else "✅" if verdict == "OK" else "⚠️"
        print(f"  {icon} {verdict:<12}: {count} мерчантів")
        total_verdicts += count
    print(f"  --------------------------------")
    print(f"  Всього в базі: {total_verdicts} мерчантів")

    print_header("ТОП ПРИЧИНИ БЛОКУВАНЬ (Risk Types)")
    cur.execute("""
        SELECT risk_type, COUNT(*) 
        FROM merchant_verdict 
        WHERE verdict = 'BLOCK' 
        GROUP BY risk_type 
        ORDER BY 2 DESC 
        LIMIT 10
    """)
    for risk, count in cur.fetchall():
        print(f"  🔸 {risk:<18}: {count} разів")

    print_header("ДЖЕРЕЛА БЛОКУВАНЬ (Хто знайшов скам?)")
    cur.execute("SELECT source, COUNT(*) FROM block_log GROUP BY source ORDER BY 2 DESC")
    for source, count in cur.fetchall():
        src_name = source if source else "regex"
        print(f"  🤖 {src_name:<15}: {count} блокувань")

    print_header("РУЧНИЙ ФІДБЕК З TELEGRAM (Кнопки)")
    cur.execute("SELECT reason, COUNT(*) FROM global_blacklist WHERE source = 'manual_tg' GROUP BY reason")
    manual_blocks = cur.fetchall()
    if manual_blocks:
        for reason, count in manual_blocks:
            print(f"  👆 {reason:<35}: {count} кліків")
    else:
        print("  Поки що немає кліків по кнопках у Telegram.")

    print_header("РОБОТА LLM (llm_decisions.log)")
    if LOG_PATH.exists():
        groq_count = 0
        gemini_count = 0
        with open(LOG_PATH, "r", encoding="utf-8") as f:
            for line in f:
                if "groq" in line:
                    groq_count += 1
                elif "gemini" in line:
                    gemini_count += 1
        print(f"  ⚡ Запитів до Groq   : {groq_count}")
        print(f"  ⚡ Запитів до Gemini : {gemini_count}")
    else:
        print("  Файл логів LLM ще не створено.")

    conn.close()
    print("\n✅ Звіт згенеровано успішно.\n")

if __name__ == "__main__":
    generate_report()