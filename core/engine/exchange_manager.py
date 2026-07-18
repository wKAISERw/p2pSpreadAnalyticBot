# core/engine/exchange_manager.py
"""
ExchangeManager — централізоване управління доступністю бірж.

Функціонал:
  1. Ручне вимкнення/ввімкнення біржі (через Telegram)
  2. Cooldown-режим: вимкнення на N годин з авто-ввімкненням
  3. Автоматичне сповіщення при відмові CircuitBreaker
  4. Health-check (спроба переконатися що біржа жива)
"""
from __future__ import annotations

import asyncio
import json
import logging
import time
from dataclasses import dataclass
from typing import Callable, Awaitable, Optional

logger = logging.getLogger("ExchangeManager")

# Всі відомі біржі (для UI)
ALL_EXCHANGES = ["Bybit", "OKX", "Wallet", "Binance", "MEXC", "BingX", "CryptoBot"]


@dataclass
class ExchangeState:
    """Стан конкретної біржі."""
    name: str
    disabled: bool = False
    cooldown_until: float = 0.0       # monotonic час закінчення cooldown
    disabled_reason: str = ""          # причина вимкнення
    consecutive_failures: int = 0     # лічильник послідовних відмов CB
    notified_at: float = 0.0          # коли востаннє сповістили юзера
    NOTIFY_COOLDOWN: float = 300.0    # мінімум між сповіщеннями (5 хв)


class ExchangeManager:
    """
    Керує доступністю бірж.
    Зберігає стан в runtime_config (ключ disabled_exchanges) для персистенції.
    """

    def __init__(self):
        self._states: dict[str, ExchangeState] = {
            name: ExchangeState(name=name) for name in ALL_EXCHANGES
        }
        self._notify_callback: Optional[Callable[[str], Awaitable[None]]] = None
        self._health_check_fn: Optional[Callable[[str], Awaitable[bool]]] = None

    def set_notify_callback(self, fn: Callable[[str], Awaitable[None]]) -> None:
        """Встановлює callback для відправки TG повідомлення."""
        self._notify_callback = fn

    def set_health_check(self, fn: Callable[[str], Awaitable[bool]]) -> None:
        """Встановлює функцію перевірки здоров'я біржі."""
        self._health_check_fn = fn

    # ── Persistence ────────────────────────────────────────────────────────

    async def load_from_config(self, runtime_config) -> None:
        """Завантажує стан з runtime_config при старті."""
        raw = runtime_config.get("disabled_exchanges", "")
        if not raw:
            return
        try:
            data = json.loads(raw)
            now = time.monotonic()
            # Переконвертуємо wall-clock в monotonic (приблизно)
            wall_now = time.time()
            for entry in data:
                name = entry.get("name", "")
                if name not in self._states:
                    continue
                st = self._states[name]
                st.disabled = entry.get("disabled", False)
                st.disabled_reason = entry.get("reason", "")
                # cooldown_until зберігається як wall-clock
                wall_until = entry.get("cooldown_wall", 0.0)
                if wall_until > wall_now:
                    st.cooldown_until = now + (wall_until - wall_now)
                else:
                    st.cooldown_until = 0.0
                    # Якщо cooldown сплив — автоматично ввімкнути
                    if st.disabled and entry.get("is_cooldown", False):
                        st.disabled = False
                        st.disabled_reason = ""
                        logger.info("⏰ Cooldown сплив для %s — біржу ввімкнено", name)
            logger.info("📦 ExchangeManager: завантажено стан з БД")
        except Exception as e:
            logger.warning("ExchangeManager load error: %s", e)

    async def save_to_config(self, runtime_config) -> None:
        """Зберігає стан в runtime_config."""
        wall_now = time.time()
        mono_now = time.monotonic()
        data = []
        for st in self._states.values():
            wall_until = 0.0
            if st.cooldown_until > mono_now:
                wall_until = wall_now + (st.cooldown_until - mono_now)
            data.append({
                "name": st.name,
                "disabled": st.disabled,
                "reason": st.disabled_reason,
                "cooldown_wall": wall_until,
                "is_cooldown": st.cooldown_until > mono_now,
            })
        await runtime_config.set("disabled_exchanges", json.dumps(data))

    # ── Core API ───────────────────────────────────────────────────────────

    def is_enabled(self, exchange_name: str) -> bool:
        """Перевіряє чи біржа активна (враховує cooldown)."""
        st = self._states.get(exchange_name)
        if not st:
            return True
        # Cooldown закінчився?
        if st.disabled and st.cooldown_until > 0:
            if time.monotonic() >= st.cooldown_until:
                st.disabled = False
                st.cooldown_until = 0.0
                st.disabled_reason = ""
                logger.info("⏰ Cooldown завершено: %s ввімкнено автоматично", exchange_name)
        return not st.disabled

    async def disable(
        self, exchange_name: str, reason: str = "manual",
        cooldown_hours: float = 0, runtime_config=None,
    ) -> bool:
        """Вимикає біржу. cooldown_hours=0 → назавжди (до ручного ввімкнення)."""
        st = self._states.get(exchange_name)
        if not st:
            return False
        st.disabled = True
        st.disabled_reason = reason
        if cooldown_hours > 0:
            st.cooldown_until = time.monotonic() + cooldown_hours * 3600
        else:
            st.cooldown_until = 0.0
        logger.warning(
            "🔴 Біржу %s вимкнено: %s (cooldown: %s)",
            exchange_name, reason,
            f"{cooldown_hours}г" if cooldown_hours else "до ручного ввімкнення",
        )
        if runtime_config:
            await self.save_to_config(runtime_config)
        return True

    async def enable(self, exchange_name: str, runtime_config=None) -> bool:
        """Вмикає біржу."""
        st = self._states.get(exchange_name)
        if not st:
            return False
        st.disabled = False
        st.cooldown_until = 0.0
        st.disabled_reason = ""
        st.consecutive_failures = 0
        logger.info("🟢 Біржу %s ввімкнено", exchange_name)
        if runtime_config:
            await self.save_to_config(runtime_config)
        return True

    async def health_check(self, exchange_name: str) -> tuple[bool, str]:
        """
        Перевірка здоров'я біржі. Повертає (is_ok, message).
        3 спроби з інтервалом.
        """
        if not self._health_check_fn:
            return False, "Health-check не налаштовано"

        for attempt in range(1, 4):
            try:
                ok = await self._health_check_fn(exchange_name)
                if ok:
                    return True, f"✅ {exchange_name} відповідає (спроба {attempt}/3)"
                logger.debug("Health-check %s спроба %d/3 — не відповідає", exchange_name, attempt)
            except Exception as e:
                logger.debug("Health-check %s спроба %d/3 — помилка: %s", exchange_name, attempt, e)
            if attempt < 3:
                await asyncio.sleep(2.0)

        return False, f"❌ {exchange_name} не відповідає після 3 спроб"

    # ── Автоматичне сповіщення ─────────────────────────────────────────────

    async def on_circuit_open(self, exchange_name: str, runtime_config=None) -> None:
        """
        Викликається коли CircuitBreaker переходить в OPEN.
        Сповіщає юзера і пропонує вимкнути біржу.
        """
        st = self._states.get(exchange_name)
        if not st:
            return

        st.consecutive_failures += 1
        now = time.monotonic()

        # Не спамимо — мінімум 5 хв між сповіщеннями
        if now - st.notified_at < st.NOTIFY_COOLDOWN:
            return
        st.notified_at = now

        if self._notify_callback:
            await self._notify_callback(exchange_name)

    def reset_failures(self, exchange_name: str) -> None:
        """Скидає лічильник помилок при успішному запиті."""
        st = self._states.get(exchange_name)
        if st:
            st.consecutive_failures = 0

    # ── UI helpers ─────────────────────────────────────────────────────────

    def get_status_all(self) -> list[dict]:
        """Повертає стан всіх бірж для відображення."""
        now = time.monotonic()
        result = []
        for st in self._states.values():
            remaining_h = 0.0
            if st.disabled and st.cooldown_until > now:
                remaining_h = (st.cooldown_until - now) / 3600

            result.append({
                "name": st.name,
                "enabled": self.is_enabled(st.name),
                "disabled_reason": st.disabled_reason,
                "cooldown_remaining_h": round(remaining_h, 1),
                "is_cooldown": st.cooldown_until > now,
                "failures": st.consecutive_failures,
            })
        return result

    def get_disabled_names(self) -> set[str]:
        """Повертає множину імен вимкнених бірж."""
        return {name for name, st in self._states.items() if not self.is_enabled(name)}


# Глобальний сінглтон
exchange_manager = ExchangeManager()


