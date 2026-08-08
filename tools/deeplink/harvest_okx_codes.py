"""Разовий масовий збір `shareCode` для всіх мерчантів OKX, яких бачив сканер.

Навіщо. Нативна картка OKX (`okx://exchange/merchanthome.com?shareCode=`)
підтверджена на пристрої, але код видає лише приватний
`/v3/c2c/merchant/share`. Раніше це означало, що кнопка залежить від живої
сесії й мовчки деградує у webview, щойно сесія помре.

Ключова властивість: **код сталий**. Він не протухає разом із сесією, якою
його дістали. Тому достатньо зібрати його ОДИН раз на мерчанта і покласти в
`okx_share_codes` — далі кнопка відкриває нативну картку вічно й без будь-якої
авторизації.

Тобто дія користувача стає разовою, а не ритуальною.

Запуск (з кореня проєкту, коли сесія OKX жива):
    python -m tools.deeplink.harvest_okx_codes            # усі відомі мерчанти
    python -m tools.deeplink.harvest_okx_codes --limit 20 # спробувати на кількох
    python -m tools.deeplink.harvest_okx_codes --only 04b439b395
"""

from __future__ import annotations

import argparse
import asyncio
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

DELAY = 1.2          # пауза між запитами, щоб не сипати в біржу
STOP_AFTER_FAILS = 8  # поспіль — далі очевидно, що сесія не тягне


async def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=0, help="взяти лише N мерчантів")
    ap.add_argument("--only", default="", help="один конкретний merchant_id")
    ap.add_argument("--user-id", type=int, default=1115620363)
    ap.add_argument("--refresh", action="store_true",
                    help="перезібрати навіть ті, що вже в кеші")
    args = ap.parse_args()

    sys.stdout.reconfigure(encoding="utf-8")

    from core.storage.merchant_db import MerchantDB
    import bot.okx_share as oks

    db = MerchantDB()
    await db.start()

    if args.only:
        ids = [args.only]
    else:
        async with db._db.execute(
            "SELECT DISTINCT merchant_id FROM merchant_reviews WHERE exchange='OKX' "
            "AND merchant_id IS NOT NULL AND merchant_id <> '' "
            "UNION "
            "SELECT DISTINCT merchant_id FROM merchant_snapshots WHERE exchange='OKX' "
            "AND merchant_id IS NOT NULL AND merchant_id <> ''"
        ) as cur:
            ids = [r[0] for r in await cur.fetchall()]

    if not args.refresh:
        keep = []
        for m in ids:
            if not await oks.load_cached_code(db, m):
                keep.append(m)
        skipped = len(ids) - len(keep)
        ids = keep
        if skipped:
            print(f"вже в кеші, пропускаю: {skipped}")

    if args.limit:
        ids = ids[:args.limit]

    print(f"до збору: {len(ids)} мерчантів\n")
    if not ids:
        await db.stop()
        return 0

    ok = fails = streak = 0
    t0 = time.time()
    for i, mid in enumerate(ids, 1):
        oks.clear_cache()          # інакше негативний кеш глушить наступні спроби
        code = await oks.get_share_code("profile", mid, db, args.user_id)
        if code:
            ok += 1
            streak = 0
            print(f"[{i}/{len(ids)}] {mid} -> {code}")
        else:
            fails += 1
            streak += 1
            print(f"[{i}/{len(ids)}] {mid} -> немає")
            if streak >= STOP_AFTER_FAILS:
                print(f"\n{streak} невдач поспіль — зупиняюсь. "
                      f"Найімовірніше сесія OKX не має доступу до merchant/share.")
                break
        await asyncio.sleep(DELAY)

    print(f"\nзібрано: {ok}, без коду: {fails}, час: {time.time()-t0:.0f} с")
    if ok:
        print("Коди збережені в okx_share_codes — вони більше не потребують сесії.")
    await db.stop()
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
