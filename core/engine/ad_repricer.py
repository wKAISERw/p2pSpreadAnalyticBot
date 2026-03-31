import asyncio
import logging
import time
from typing import Optional, Callable, Awaitable

from core.utils.rate_limiter import RateLimiter, global_rate_limiter

logger = logging.getLogger("AdRepricer")

# Типізація callback для сповіщення в Telegram
NotifyCallback = Optional[Callable[[str], Awaitable[None]]]


class AdRepricer:
    """
    Блок 4: Maker AdRepricer.

    Стежить за ціновою позицією Maker-оголошення у стакані.
    Розраховує мінімальну рентабельну ціну та дає команду RouteExecutor
    оновити ціну оголошення, щоб лишатись першим у книзі.

    Мінімальна рентабельна ціна:
        min_sell_price = buy_price * (1 + min_margin) + (network_fee / amount)

    де:
        buy_price   — ціна по якій ми купили USDT (Нога 1)
        min_margin  — мінімальна маржа (напр. 0.003 = 0.3%)
        network_fee — вже сплачена комісія за переказ (USDT)
        amount      — кількість USDT для продажу

    Якщо топ стакану нижче min_sell_price → сповіщаємо і НЕ оновлюємо ціну.
    """

    POLL_INTERVAL = 10.0   # секунди між перевірками стакану
    MAX_IDLE_SEC  = 900    # 15 хвилин — якщо угода не закрилась, сповіщаємо

    def __init__(
        self,
        session_id: int,
        sell_ad_id: str,
        exchange: str,
        buy_price: float,       # ціна купівлі Ноги 1
        amount_usdt: float,     # кількість USDT для продажу
        network_fee: float,     # вже сплачена мережева комісія в USDT
        min_margin: float = 0.003,  # мінімальна маржа (0.3%)
        step: float = 0.01,    # крок зниження ціни (UAH)
        rate_limiter: Optional[RateLimiter] = None,
        notify_cb: NotifyCallback = None,
    ):
        self._session_id  = session_id
        self._sell_ad_id  = sell_ad_id
        self._exchange    = exchange
        self._buy_price   = buy_price
        self._amount      = amount_usdt
        self._network_fee = network_fee
        self._min_margin  = min_margin
        self._step        = step
        self._limiter     = rate_limiter or global_rate_limiter
        self._notify_cb   = notify_cb

        self._current_price: Optional[float] = None
        self._started_at  = time.time()
        self._running     = False

    # ─── Публічний API ─────────────────────────────────────────────────────

    @property
    def min_sell_price(self) -> float:
        """Мінімальна ціна нижче якої ми не опускаємось."""
        return self._buy_price * (1 + self._min_margin) + (self._network_fee / max(self._amount, 1))

    async def watch(
        self,
        fetch_book_top: Callable[[str, str], Awaitable[Optional[float]]],
        update_ad_price: Callable[[str, str, float], Awaitable[bool]],
        db,  # MerchantDB — для оновлення статусу
    ) -> None:
        """
        Основний цикл AdRepricer.

        :param fetch_book_top: async fn(exchange, ad_id) -> float | None
                               Повертає поточну топ-ціну стакану (найкращий конкурент).
        :param update_ad_price: async fn(exchange, ad_id, new_price) -> bool
                               Відправляє запит на біржу для оновлення ціни оголошення.
        :param db: MerchantDB для оновлення FSM-статусів.
        """
        self._running = True
        logger.info(
            f"[AdRepricer #{self._session_id}] Запуск. Exchange={self._exchange}, "
            f"Ad={self._sell_ad_id}, MinPrice={self.min_sell_price:.4f}"
        )

        while self._running:
            try:
                # Перевірка таймауту бездіяльності
                if time.time() - self._started_at > self.MAX_IDLE_SEC:
                    await self._notify(
                        f"⏰ [AdRepricer #{self._session_id}] Оголошення {self._sell_ad_id} "
                        f"на {self._exchange} не закрите вже 15 хвилин! Перевірте вручну."
                    )

                # 1. Отримуємо топ стакану (найдешевший конкурент вище нас)
                book_top = await fetch_book_top(self._exchange, self._sell_ad_id)

                if book_top is None:
                    logger.debug(f"[AdRepricer #{self._session_id}] book_top=None, пропускаємо ітерацію")
                    await asyncio.sleep(self.POLL_INTERVAL)
                    continue

                # 2. Розраховуємо нашу цільову ціну (бити конкурента на 1 крок)
                target_price = round(book_top - self._step, 4)

                # 3. Перевіряємо рентабельність
                if target_price < self.min_sell_price:
                    logger.warning(
                        f"[AdRepricer #{self._session_id}] 📉 Нижче мінімальної маржі! "
                        f"BookTop={book_top:.4f} Target={target_price:.4f} Min={self.min_sell_price:.4f}. "
                        f"Ціну НЕ оновлюємо."
                    )
                    await self._notify(
                        f"📉 [AdRepricer] Сесія #{self._session_id}: Неможливо бути першим без збитку!\n"
                        f"Конкурент: {book_top:.2f} UAH | Ваш мінімум: {self.min_sell_price:.2f} UAH\n"
                        f"Оголошення #{self._sell_ad_id} на {self._exchange} — перевірте вручну."
                    )
                    await asyncio.sleep(self.POLL_INTERVAL)
                    continue

                # 4. Порівнюємо з поточною ціною — оновлюємо тільки якщо змінилась
                if self._current_price is not None and abs(target_price - self._current_price) < 0.001:
                    logger.debug(f"[AdRepricer #{self._session_id}] Ціна не змінилась ({self._current_price:.4f}), пропуск.")
                    await asyncio.sleep(self.POLL_INTERVAL)
                    continue

                # 5. Виклик оновлення через RateLimiter (Блок 5)
                async with self._limiter(self._exchange):
                    success = await update_ad_price(self._exchange, self._sell_ad_id, target_price)

                if success:
                    logger.info(
                        f"[AdRepricer #{self._session_id}] ✅ Ціну оновлено: "
                        f"{self._current_price} → {target_price:.4f} UAH"
                    )
                    self._current_price = target_price
                else:
                    logger.warning(f"[AdRepricer #{self._session_id}] ⚠️ Не вдалося оновити ціну.")

            except asyncio.CancelledError:
                logger.info(f"[AdRepricer #{self._session_id}] Зупинено (CancelledError).")
                break
            except Exception as e:
                logger.error(f"[AdRepricer #{self._session_id}] Помилка: {e}", exc_info=True)

            await asyncio.sleep(self.POLL_INTERVAL)

        self._running = False
        logger.info(f"[AdRepricer #{self._session_id}] Цикл завершено.")

    def stop(self) -> None:
        """Зупиняє цикл repricera."""
        self._running = False

    # ─── Допоміжні ─────────────────────────────────────────────────────────

    async def _notify(self, message: str) -> None:
        if self._notify_cb:
            try:
                await self._notify_cb(message)
            except Exception as e:
                logger.error(f"[AdRepricer] Помилка сповіщення: {e}")
