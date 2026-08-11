#!/usr/bin/env python3
# tools/risk_reset.py
"""
Скидання кешу вердиктів — щоб зміни в движку застосувались одразу.

Вердикт живе в `merchant_verdict` і перечитується з кешу, доки не мине TTL
(до трьох діб) або не зміняться умови. Після зміни сигналів це означає, що
новий розподіл проступатиме поволі: половина мерчантів ще носитиме рішення,
ухвалене старою логікою.

**Не видаляє рядки.** Ставить `updated_at = 0`, і `get_verdict` починає
віддавати None — тобто мерчанта перевірять заново. Це важливо: у рядку
лишається `risk_score` з його згасанням історії, і мерчант, який учора був
BLOCK, не стає «чистим з нуля». Видалення знищило б і це.

    python tools/risk_reset.py                  # показати, що буде (нічого не змінює)
    python tools/risk_reset.py --apply          # скинути все
    python tools/risk_reset.py --apply --keep-blocks
    python tools/risk_reset.py --apply --exchange Binance

Перед записом робиться копія бази поруч із оригіналом. Вимкнути можна
`--no-backup`, але без причини цього робити не варто.

⚠️ Після скидання кожен мерчант піде на повторний аналіз, а це виклики до
   LLM. При 1290 мерчантах і лімітах Gemini у 500 запитів на добу черга
   розтягнеться на кілька діб — сплеск не миттєвий, але відчутний. Якщо
   квота тісна, скидайте частинами: спершу `--keep-blocks`, потім решту.
"""
from __future__ import annotations

import argparse
import shutil
import sqlite3
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DB_PATH = ROOT / "data" / "merchants.db"


def _counts(db: sqlite3.Connection, where: str, params: tuple) -> dict:
    rows = db.execute(
        f"SELECT verdict, COUNT(*) FROM merchant_verdict WHERE {where} GROUP BY 1",
        params,
    ).fetchall()
    return {v or "—": n for v, n in rows}


def main() -> int:
    ap = argparse.ArgumentParser(description="Скидання кешу вердиктів ріск-енджину")
    ap.add_argument("--db", default=str(DB_PATH))
    ap.add_argument("--apply", action="store_true", help="справді записати (без цього — суха проба)")
    ap.add_argument("--keep-blocks", action="store_true",
                    help="не чіпати BLOCK — перевірити спершу решту")
    ap.add_argument("--exchange", help="лише одна біржа")
    ap.add_argument("--no-backup", action="store_true", help="не робити копію бази")
    args = ap.parse_args()

    path = Path(args.db)
    if not path.exists():
        print(f"Бази немає: {path}")
        return 1

    where, params = "1=1", []
    if args.keep_blocks:
        where += " AND verdict != 'BLOCK'"
    if args.exchange:
        where += " AND exchange = ?"
        params.append(args.exchange)
    # Уже скинуті рядки чіпати вдруге нема сенсу.
    where += " AND COALESCE(updated_at, 0) != 0"

    db = sqlite3.connect(str(path))
    try:
        total = db.execute("SELECT COUNT(*) FROM merchant_verdict").fetchone()[0]
        affected = _counts(db, where, tuple(params))
        n = sum(affected.values())

        print(f"База:      {path}")
        print(f"Вердиктів: {total}")
        print(f"Під скидання: {n}" + (f"  ({', '.join(f'{k}={v}' for k, v in sorted(affected.items()))})" if n else ""))
        if args.keep_blocks:
            print("           BLOCK лишається як є")
        if args.exchange:
            print(f"           тільки {args.exchange}")

        if not n:
            print("\nНічого скидати.")
            return 0

        if not args.apply:
            print("\nЦе суха проба. Щоб записати — додайте --apply")
            return 0

        if not args.no_backup:
            stamp = time.strftime("%Y%m%d_%H%M%S")
            backup = path.with_name(f"{path.stem}.before_reset_{stamp}{path.suffix}")
            # Копіюємо через SQLite, а не файлом: у WAL-режимі частина даних
            # лежить у -wal, і звичайний cp дав би неповний знімок.
            with sqlite3.connect(str(backup)) as dst:
                db.backup(dst)
            print(f"\nКопія: {backup.name} ({backup.stat().st_size // 1024} КБ)")

        cur = db.execute(
            f"UPDATE merchant_verdict SET updated_at = 0 WHERE {where}", tuple(params)
        )
        db.commit()
        print(f"Скинуто: {cur.rowcount}")
        print("\nМерчанти підуть на повторний аналіз у міру появи в стакані.")
        print("Стежити: tools/risk_probe.py --verdicts")
    finally:
        db.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
