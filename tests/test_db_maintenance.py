# tests/test_db_maintenance.py
"""
VACUUM має справді виконуватись.

З червня по серпень 2026 в error.log щодоби лягав один і той самий рядок:

    Помилка виконання SQL у DB Maintenance: cannot VACUUM - SQL statements in progress

Обслуговування бази при цьому вважалось робочим — чистка вище відпрацьовувала
й комітилась, а помилка ловилась загальним except і йшла в лог. Тобто команда
була, лог про неї був, стиснення не було жодного разу.

Причина не в конкурентності, а в незавершених statement-ах: PRAGMA, що
повертає рядок, лишається "в процесі", поки той рядок не прочитали.
"""
import asyncio
import shutil
import tempfile
import unittest
from pathlib import Path

import aiosqlite

from core.storage.merchant_db import MerchantDB
from core.workers.db_maintenance import DBMaintenanceTask


class TestVacuumActuallyRuns(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self._dir = Path(tempfile.mkdtemp(prefix="vacuum_test_"))
        self.db = MerchantDB(self._dir / "m.db")
        await self.db.start()

    async def asyncTearDown(self):
        await self.db.stop()
        shutil.rmtree(self._dir, ignore_errors=True)

    async def test_vacuum_completes_on_live_connection(self):
        # Головний тест: база відкрита застосунком рівно так, як у проді.
        task = DBMaintenanceTask(self.db)
        with self.assertLogs("DBMaintenance", level="INFO") as logs:
            await task._vacuum()
        text = "\n".join(logs.output)
        self.assertIn("VACUUM завершено", text, f"VACUUM не дійшов до кінця: {text}")
        self.assertNotIn("не виконано", text)

    async def test_vacuum_survives_open_cursor_elsewhere(self):
        # Сканер, LLM-воркери і збирач відгуків ходять по тому самому
        # з'єднанню. Відкритий чужий курсор не має зривати обслуговування.
        cur = await self.db._db.execute("SELECT * FROM merchant_verdict")
        await cur.fetchone()
        try:
            task = DBMaintenanceTask(self.db)
            with self.assertLogs("DBMaintenance", level="INFO") as logs:
                await task._vacuum()
            self.assertIn("VACUUM завершено", "\n".join(logs.output))
        finally:
            await cur.close()

    async def test_startup_pragmas_leave_no_statement_in_progress(self):
        # Корінь бага: PRAGMA journal_mode і busy_timeout повертають рядок.
        # Якщо його не прочитати, statement висить до кінця життя процесу.
        await self.db._db.execute("VACUUM")   # має пройти без винятку

    async def test_maintenance_reports_failure_instead_of_pretending(self):
        # Якщо стиснути не вдалось — це має бути видно як помилка VACUUM,
        # а не мовчазний успіх.
        task = DBMaintenanceTask(self.db)
        task.db = type("Broken", (), {"_path": None})()
        with self.assertLogs("DBMaintenance", level="WARNING") as logs:
            await task._vacuum()
        self.assertIn("VACUUM пропущено", "\n".join(logs.output))


class TestPragmaCursorsBlockVacuum(unittest.IsolatedAsyncioTestCase):
    """Фіксує саме поведінку SQLite, на якій тримається фікс."""

    async def test_unread_row_returning_pragma_blocks_vacuum(self):
        d = Path(tempfile.mkdtemp(prefix="vacuum_pragma_"))
        try:
            async with aiosqlite.connect(str(d / "x.db"), isolation_level=None) as c:
                await c.execute("PRAGMA journal_mode=WAL")   # рядок не прочитано
                with self.assertRaises(Exception) as ctx:
                    await c.execute("VACUUM")
                self.assertIn("SQL statements in progress", str(ctx.exception))

            async with aiosqlite.connect(str(d / "y.db"), isolation_level=None) as c:
                async with c.execute("PRAGMA journal_mode=WAL") as cur:
                    await cur.fetchall()                    # рядок прочитано
                await c.execute("VACUUM")                   # тепер проходить
        finally:
            shutil.rmtree(d, ignore_errors=True)


if __name__ == "__main__":
    unittest.main()
