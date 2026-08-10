import asyncio
import logging
import time
from datetime import datetime, timedelta

logger = logging.getLogger("DBMaintenance")


class DBMaintenanceTask:
    """
    Єдиний власник обслуговування БД.

    Два рівні:
      * щогодини — легкий prune: старі снапшоти стакану і старі пропозиції
        сканера. Дешево, тримає таблиці в розумному розмірі.
      * раз на добу — важкий GC: чистка не-BLOCK вердиктів та історії
        відгуків, далі VACUUM.

    Раніше легкий prune жив окремим циклом усередині scanner.py
    (`_db_maintenance_loop`), а важкий — тут. Два незалежні планувальники на
    одну базу, обидва стартували з різних місць і нічого один про одного
    не знали. Тепер точка входу одна — main.py.
    """

    def __init__(
        self,
        db,
        interval_days: int = 1,
        days_to_keep: int = 30,
        prune_interval_hours: float = 1.0,
        snapshot_max_age_hours: int = 24,
        proposals_retention_days: int = 7,
    ):
        """
        :param db: Об'єкт MerchantDB
        :param interval_days: Як часто робити важкий GC + VACUUM
        :param days_to_keep: Скільки днів зберігати не-BLOCK записи
        :param prune_interval_hours: Як часто робити легкий prune
        :param snapshot_max_age_hours: Вік снапшотів стакану для видалення
        :param proposals_retention_days: Скільки днів тримати пропозиції сканера
        """
        self.db = db
        self.interval_seconds = interval_days * 24 * 3600
        self.days_to_keep = days_to_keep
        self.prune_interval_seconds = prune_interval_hours * 3600
        self.snapshot_max_age_hours = snapshot_max_age_hours
        self.proposals_retention_days = proposals_retention_days
        self._task = None

    def start(self):
        if self._task is None:
            self._task = asyncio.create_task(self._run_loop(), name="db_maintenance_loop")
            logger.info(
                "DB Maintenance Worker запущено (prune кожні %.0fг, GC+VACUUM кожні %d днів).",
                self.prune_interval_seconds / 3600,
                self.interval_seconds // 86400,
            )

    async def stop(self):
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
            self._task = None
            logger.info("DB Maintenance Worker зупинено.")

    async def _run_loop(self):
        # Перший запуск через 5 хвилин, щоб не навантажувати систему на старті.
        await asyncio.sleep(300)
        last_heavy = 0.0
        while True:
            try:
                await self.prune_hot_tables()

                if time.monotonic() - last_heavy >= self.interval_seconds:
                    await self.run_maintenance()
                    last_heavy = time.monotonic()

                await asyncio.sleep(self.prune_interval_seconds)
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error("Помилка під час DB Maintenance: %s", e, exc_info=True)
                await asyncio.sleep(3600)  # У разі помилки повторити через годину

    async def prune_hot_tables(self) -> None:
        """Легка чистка: снапшоти стакану, пропозиції сканера, зависли резерви."""
        if not self.db:
            return
        try:
            deleted = await self.db.prune_snapshots(max_age_hours=self.snapshot_max_age_hours)
            if deleted > 0:
                logger.info("🧹 DB Maintenance: видалено %d старих снапшотів", deleted)

            prop_deleted = await self.db.cleanup_old_proposals(
                retention_days=self.proposals_retention_days
            )
            if prop_deleted > 0:
                logger.info("🧹 DB Maintenance: видалено %d старих пропозицій", prop_deleted)

            # Звільнення протермінованих резервів на картках.
            # Докстрінг release_expired_reservations роками стверджував
            # "Викликається з циклу сканера", але викликався він лише з тестів —
            # тобто зарезервовані legs висіли в 'pending' вічно і назавжди
            # з'їдали доступний ліміт картки в card_matching_engine.
            released = await self.db.release_expired_reservations()
            if released > 0:
                logger.info("🧹 DB Maintenance: звільнено %d протермінованих резервів карток", released)

            # Журнал відмов карткового модуля — службовий, тримаємо два тижні.
            # Тижневий зріз статистики читається з нього, глибше нікому не треба.
            rej_deleted = await self.db.prune_rejection_log(retention_days=14)
            if rej_deleted > 0:
                logger.info("🧹 DB Maintenance: видалено %d старих записів причин відмов", rej_deleted)
        except asyncio.CancelledError:
            raise
        except Exception as e:
            logger.error("Помилка легкої чистки БД: %s", e)

    async def run_maintenance(self):
        """Важкий GC: старі вердикти, історія відгуків, VACUUM."""
        if not self.db:
            return

        logger.info("Початок оптимізації бази даних (Garbage Collection)...")
        conn = getattr(self.db, "_db", None)

        if not conn:
            logger.error("DB Maintenance: відсутнє з'єднання з базою.")
            return

        try:
            async def _table_has_column(table_name: str, column_name: str) -> bool:
                async with conn.execute(f"PRAGMA table_info({table_name})") as cur:
                    rows = await cur.fetchall()
                return any((r[1] == column_name) for r in rows)

            cutoff_unix = (datetime.now() - timedelta(days=self.days_to_keep)).timestamp()

            # 1. merchant_verdict — крім BLOCK (скамерів пам'ятаємо довго)
            verdict_ts_col = None
            for candidate in ("created_at", "updated_at"):
                if await _table_has_column("merchant_verdict", candidate):
                    verdict_ts_col = candidate
                    break

            deleted_verdicts = 0
            if verdict_ts_col:
                query = (
                    "DELETE FROM merchant_verdict "
                    "WHERE risk_type != 'BLOCK' AND verdict != 'BLOCK' "
                    f"AND {verdict_ts_col} < ?"
                )
                async with conn.execute(query, (cutoff_unix,)) as cursor:
                    deleted_verdicts = cursor.rowcount
            else:
                logger.warning(
                    "DB Maintenance: merchant_verdict не має created_at/updated_at, "
                    "пропускаю очистку цієї таблиці."
                )

            # 2. merchant_review_history — старі відгуки важать багато
            reviews_ts_col = None
            for candidate in ("created_at", "recorded_at", "updated_at"):
                if await _table_has_column("merchant_review_history", candidate):
                    reviews_ts_col = candidate
                    break

            deleted_reviews = 0
            if reviews_ts_col:
                query = f"DELETE FROM merchant_review_history WHERE {reviews_ts_col} < ?"
                async with conn.execute(query, (cutoff_unix,)) as cursor:
                    deleted_reviews = cursor.rowcount
            else:
                logger.warning(
                    "DB Maintenance: merchant_review_history не має "
                    "created_at/recorded_at/updated_at, пропускаю очистку."
                )

            await conn.commit()
            logger.info(
                "Видалено %d старих вердиктів та %d старих записів історії відгуків.",
                deleted_verdicts, deleted_reviews,
            )

            # 3. Стискаємо базу окремим з'єднанням — див. _vacuum().
            await self._vacuum()

        except asyncio.CancelledError:
            raise
        except Exception as e:
            logger.error("Помилка виконання SQL у DB Maintenance: %s", e)

    async def _vacuum(self) -> None:
        """
        Стиснення бази на ОКРЕМОМУ з'єднанні, з обов'язковим вичерпанням
        курсорів.

        Раніше VACUUM виконувався на спільному з'єднанні застосунку і падав
        **щоразу** — в error.log це видно від червня до серпня 2026 рівно
        одним рядком:

            cannot VACUUM - SQL statements in progress

        Спокуса пояснити це конкурентністю (мовляв, хтось тримає курсор)
        хибна: причина статична й відома ще з моменту старту процесу.
        SQLite забороняє VACUUM, поки на з'єднанні лишається хоч один
        **незавершений** statement, а незавершеним рахується будь-який, що
        повертає рядки і чий курсор не вичерпали. У `MerchantDB.start()`
        таких два:

            PRAGMA journal_mode=WAL     → повертає 'wal'
            PRAGMA busy_timeout=10000   → повертає 10000

        Курсор від них не закривають, тож вони висять на спільному з'єднанні
        весь час життя процесу. Виміряно окремо: `PRAGMA synchronous`,
        `foreign_keys` і `cache_size` рядків не повертають і VACUUM не
        блокують, а `journal_mode`, `busy_timeout` і звичайний `SELECT 1`
        блокують.

        Тому тут два запобіжники одразу:
          * власне з'єднання — на ньому немає чужих курсорів;
          * кожен PRAGMA вичерпується через `async with … fetchall()`.

        `isolation_level=None` вимикає неявний BEGIN драйвера — інакше
        отримали б другу помилку, `cannot VACUUM from within a transaction`.

        Checkpoint WAL перед стисненням потрібен, бо сторінки, які ще лежать
        у -wal, у VACUUM не беруть участі.
        """
        import aiosqlite

        path = getattr(self.db, "_path", None)
        if not path:
            logger.warning("VACUUM пропущено: невідомий шлях до бази.")
            return

        size_before = 0
        try:
            size_before = path.stat().st_size
        except OSError:
            pass

        logger.info("Виконання команди VACUUM...")
        vac_start = time.monotonic()
        try:
            async with aiosqlite.connect(str(path), isolation_level=None) as vac:
                # fetchall() тут не за даними, а щоб statement завершився:
                # невичерпаний курсор — і є та сама "SQL statements in progress".
                for pragma in ("PRAGMA busy_timeout=30000", "PRAGMA wal_checkpoint(TRUNCATE)"):
                    async with vac.execute(pragma) as cur:
                        await cur.fetchall()
                await vac.execute("VACUUM")
        except Exception as e:
            # Невдале стиснення не має валити решту обслуговування: чистка
            # вище вже відпрацювала і закомічена.
            logger.error("VACUUM не виконано: %s", e)
            return

        elapsed = time.monotonic() - vac_start
        try:
            size_after = path.stat().st_size
            freed = size_before - size_after
            logger.info(
                "VACUUM завершено за %.1fs: %.1f MB → %.1f MB (звільнено %.1f MB).",
                elapsed, size_before / 1048576, size_after / 1048576, freed / 1048576,
            )
        except OSError:
            logger.info("VACUUM завершено за %.1fs.", elapsed)
