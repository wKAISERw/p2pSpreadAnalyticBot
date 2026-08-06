# core/utils/tasks.py
"""
Запуск фонових тасків "вистрілив і забув" — але безпечно.

Голий `asyncio.create_task(...)` / `ensure_future(...)` має дві вади, обидві
тихі:

  1. Event loop тримає на таск лише СЛАБКЕ посилання. Якщо результат нікуди не
     зберегти, збирач сміття має право прибрати таск посеред виконання — і
     запис у БД просто не відбудеться, без жодного сліду в логах.
  2. Виняток усередині таска нікуди не потрапляє, доки таск не заберуть. На
     практиці це означає "помилка зникла".

`spawn()` тримає сильне посилання до завершення і логує будь-який виняток.
"""
from __future__ import annotations

import asyncio
import logging
from typing import Any, Coroutine, Optional, Set

logger = logging.getLogger("BackgroundTasks")

# Сильні посилання на живі таски — знімаються в done-callback.
_background_tasks: Set[asyncio.Task] = set()


def spawn(
    coro: Coroutine[Any, Any, Any],
    name: str,
    *,
    logger_: Optional[logging.Logger] = None,
) -> asyncio.Task:
    """
    Запускає корутину фоново.

    :param coro:    корутина для виконання
    :param name:    ім'я таска — потрапляє в лог помилки і в asyncio-дебаг
    :param logger_: логер виклику (щоб помилка йшла в його неймспейс)
    """
    task = asyncio.create_task(coro, name=name)
    _background_tasks.add(task)
    log = logger_ or logger

    def _done(t: asyncio.Task) -> None:
        _background_tasks.discard(t)
        if t.cancelled():
            return
        exc = t.exception()
        if exc is not None:
            log.error("💥 Фоновий таск %s впав: %s", name, exc, exc_info=exc)

    task.add_done_callback(_done)
    return task


def pending_count() -> int:
    """Скільки фонових тасків зараз у польоті (для /status і метрик)."""
    return len(_background_tasks)
