import asyncio
import logging
import time
from enum import Enum

logger = logging.getLogger(__name__)


class State(Enum):
    CLOSED = "CLOSED"
    OPEN = "OPEN"
    HALF_OPEN = "HALF_OPEN"


class CircuitBreaker:
    def __init__(self, failure_threshold: int = 3, recovery_timeout: float = 60.0):
        self._threshold = failure_threshold
        self._recovery_timeout = recovery_timeout
        self._failures = 0
        self._state = State.CLOSED
        self._opened_at = None
        self._probe_in_flight = False

    @property
    def state(self) -> State:
        """Чиста property: тільки читає, не мутує стан."""
        if self._state == State.OPEN and self._opened_at is not None:
            if time.monotonic() - self._opened_at >= self._recovery_timeout:
                return State.HALF_OPEN
        return self._state

    async def call(self, coro):
        """Єдиний спосіб взаємодії: обгортає будь-яку корутину."""
        current_state = self.state

        # Явний перехід у HALF_OPEN тут, а не всередині property
        if current_state == State.HALF_OPEN and self._state != State.HALF_OPEN:
            self._state = State.HALF_OPEN
            coro.close()
            logger.info("💓 Circuit HALF_OPEN: Пробний запит для відновлення...")

        if current_state == State.OPEN:
            wait_time = self._recovery_timeout - (time.monotonic() - self._opened_at)
            coro.close()
            raise RuntimeError(f"🚫 Circuit is OPEN. Очікування: {wait_time:.0f}s")

        if current_state == State.HALF_OPEN:
            if self._probe_in_flight:
                raise RuntimeError("⏳ Circuit is HALF_OPEN. Пробний запит вже виконується.")
            self._probe_in_flight = True

        try:
            result = await coro
            self._record_success()
            return result
        except Exception as e:
            self._record_failure(e)
            raise
        finally:
            self._probe_in_flight = False

    def _record_success(self):
        """Успішний запит скидає лічильники."""
        if self._state != State.CLOSED:
            logger.info("✅ Circuit CLOSED: Зв'язок відновлено.")
        self._failures = 0
        self._state = State.CLOSED
        self._opened_at = None

    def _record_failure(self, error: Exception):
        """Рахує помилки і переходить в OPEN тільки після threshold."""
        self._failures += 1
        logger.warning("⚠️ Failure #%d/%d | %s", self._failures, self._threshold, error)

        if self._failures >= self._threshold:
            self._state = State.OPEN
            self._opened_at = time.monotonic()
            logger.error("🔴 Circuit OPEN: Пауза запитів на %.0fs", self._recovery_timeout)