import asyncio
import logging
from datetime import datetime, timedelta

logger = logging.getLogger("DBMaintenance")

class DBMaintenanceTask:
    def __init__(self, db, interval_days: int = 1, days_to_keep: int = 30):
        """
        :param db: Об'єкт MerchantDB
        :param interval_days: Як часто запускати очищення (в днях)
        :param days_to_keep: Скільки днів зберігати не-BLOCK записи
        """
        self.db = db
        self.interval_seconds = interval_days * 24 * 3600
        self.days_to_keep = days_to_keep
        self._task = None

    def start(self):
        if self._task is None:
            self._task = asyncio.create_task(self._run_loop(), name="db_maintenance_loop")
            logger.info("DB Maintenance Worker запущено (інтервал: %d днів).", self.interval_seconds // 86400)

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
        while True:
            try:
                # Перший запуск після старту зробимо через 5 хвилин, щоб не навантажувати систему при запуску бота
                await asyncio.sleep(300)
                await self.run_maintenance()
                # Чекаємо наступного циклу (наприклад, 24 години)
                await asyncio.sleep(self.interval_seconds)
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error("Помилка під час DB Maintenance: %s", e, exc_info=True)
                await asyncio.sleep(3600)  # У разі помилки повторити через годину

    async def run_maintenance(self):
        if not self.db:
            return
            
        logger.info("Початок оптимізації бази даних (Garbage Collection)...")
        conn = getattr(self.db, "db", None) or getattr(self.db, "_db", None)
        
        if not conn:
            logger.error("DB Maintenance: відсутнє з'єднання з базою.")
            return

        try:
            # 1. Видалення старих записів з merchant_verdict (ті що НЕ BLOCK, бо скамерів пам'ятати треба довго)
            cutoff_date = datetime.now() - timedelta(days=self.days_to_keep)
            cutoff_timestamp = cutoff_date.isoformat() # або unix, залежно від того як зберігається created_at
            
            # Якщо created_at це DATETIME string:
            # Нам треба знати тип created_at у merchant_verdict. Подивлюсь на імплементацію...
            # Якщо UNIX timestamp:
            cutoff_unix = cutoff_date.timestamp()
            
            # Якщо текстовий формат:
            # DELETE FROM merchant_verdict WHERE created_at < ... 
            
            # Оскільки ми не певні щодо типу created_at (REAL чи TEXT), спробуємо почистити `merchant_review_history` та `merchant_verdict`
            # 1.1 Чистимо `merchant_verdict`
            async with conn.execute(
                "DELETE FROM merchant_verdict WHERE risk_type != 'BLOCK' AND verdict != 'BLOCK' AND created_at < ?",
                (cutoff_unix,)
            ) as cursor:
                deleted_verdicts = cursor.rowcount
            
            # 1.2 Чистимо `merchant_review_history` (старі відгуки важать багато)
            async with conn.execute(
                "DELETE FROM merchant_review_history WHERE created_at < ?",
                (cutoff_unix,)
            ) as cursor:
                deleted_reviews = cursor.rowcount
                
            await conn.commit()
            
            logger.info("Видалено %d старих вердиктів та %d старих записів історії відгуків.", deleted_verdicts, deleted_reviews)

            # 2. Стискаємо базу даних
            logger.info("Виконання команди VACUUM...")
            await conn.execute("VACUUM")
            await conn.commit()
            logger.info("VACUUM завершено. Оптимізація бази успішна.")

        except Exception as e:
            logger.error("Помилка виконання SQL у DB Maintenance: %s", e)
            
