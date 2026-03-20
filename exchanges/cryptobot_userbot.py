# exchanges/cryptobot_userbot.py
import asyncio
import sys

if sys.version_info >= (3, 10):
    _loop = asyncio.new_event_loop()
    asyncio.set_event_loop(_loop)

import logging
import random
import time
from decimal import Decimal, InvalidOperation
from typing import Optional

from pyrogram import Client
from pyrogram.raw.functions.contacts import ResolveUsername
from pyrogram.raw.functions.messages import GetBotCallbackAnswer

from exchanges.base import Order

logger = logging.getLogger("CryptoBotUserbot")

CRYPTOBOT_USERNAME = "CryptoBot"

from config.banks import BankRegistry as _BankRegistry

# Автоматично будується з BankRegistry — не редагувати вручну
BANK_CALLBACK_TO_CODE = {
    cb: code
    for code, bank in [(b.internal_code, b) for b in _BankRegistry._by_code.values()]
    for cb in [bank.get_code("CryptoBot")]
    if cb and cb.startswith("choose-method-")
}
# Додаємо спеціальний запис що не в BankRegistry
BANK_CALLBACK_TO_CODE["choose-method-globalbanktransfer"] = "transfer"

NAV_CALLBACKS = {
    "market-trade-open-filters",
    "offers-page-0", "offers-page-1", "offers-page-2",
    "offers-page-3", "offers-page-4", "offers-page-5", "offers-page-6",
    "market-trade-payment-methods", "back", "back-to-main-menu", "market",
}

# Ланцюжок кнопок для повернення до P2P меню зі списку ордерів
BACK_CHAIN = [
    "back",                          # деталі ордера → список (якщо зайшли всередину)
    "market-trade-payment-methods",  # список ордерів → вибір банку
    "back",                          # вибір банку → вибір активу
    "market",                        # вибір активу → P2P меню
]

def _parse_amount(s: str) -> Decimal:
    s = s.strip().replace(",", "").replace(" ", "")
    try:
        if s.upper().endswith("K"):
            return Decimal(s[:-1]) * 1000
        if s.upper().endswith("M"):
            return Decimal(s[:-1]) * 1_000_000
        return Decimal(s)
    except InvalidOperation:
        return Decimal("0")


def _parse_offer_button(text: str, callback: str, bank_code: str, side: str) -> Optional[Order]:
    if not callback or not callback.startswith("trade-open-offer-"):
        return None

    offer_id = callback.split("-")[-1]

    clean = text.strip()
    for badge in ["💎 ", "⚡️ ", "⚡ ", "🔥 "]:
        if clean.startswith(badge):
            clean = clean[len(badge):]
            break

    parts = [p.strip() for p in clean.split(" · ")]

    try:
        if parts[0].startswith("₴"):
            merchant_name = "Unknown"
            price_str = parts[0].lstrip("₴").replace(",", "")
            limit_parts = parts[1:]
        else:
            merchant_name = parts[0]
            price_str = parts[1].lstrip("₴").replace(",", "")
            limit_parts = parts[2:]

        price = Decimal(price_str)

        if limit_parts:
            limit_str = limit_parts[0]
            if " - " in limit_str:
                min_s, max_s = limit_str.split(" - ", 1)
                min_limit = _parse_amount(min_s)
                max_limit = _parse_amount(max_s)
            else:
                min_limit = Decimal("0")
                max_limit = _parse_amount(limit_str)
        else:
            min_limit = Decimal("0")
            max_limit = Decimal("0")

        available = max_limit / price if price > 0 else Decimal("0")

        return Order(
            id=f"cb_{offer_id}",
            price=price,
            available_amount=available,
            min_limit=min_limit,
            max_limit=max_limit,
            merchant_id=offer_id,
            merchant_name=merchant_name,
            month_order_count=500,
            finish_rate_pct=100.0,
            exchange="CryptoBot",
            link=f"https://t.me/CryptoBot?start=p2p_{offer_id}",
            bank_codes=[bank_code],
        )

    except Exception as e:
        logger.debug("Не вдалося розпарсити кнопку %r: %s", text, e)
        return None


class OrderCache:
    def __init__(self, ttl: float = 90.0):
        self._ttl = ttl
        self._buy: list[Order] = []
        self._sell: list[Order] = []
        self._updated_at: float = 0.0

    def update(self, buy: list[Order], sell: list[Order]):
        self._buy = buy
        self._sell = sell
        self._updated_at = time.monotonic()
        logger.info("📦 CryptoBot кеш: %d buy / %d sell", len(buy), len(sell))

    def get(self) -> tuple[list[Order], list[Order]]:
        if time.monotonic() - self._updated_at > self._ttl:
            return [], []
        return self._buy, self._sell

    @property
    def age_seconds(self) -> float:
        return time.monotonic() - self._updated_at


class CryptoBotUserbot:
    def __init__(
        self,
        api_id: int,
        api_hash: str,
        session_name: str = "sessions/cryptobot_twink",
        update_interval: float = 30.0,
        banks: Optional[list[str]] = None,
    ):
        self.app = Client(session_name, api_id=api_id, api_hash=api_hash)
        self.interval = update_interval
        self.banks = banks or ["43", "14"]
        self.cache = OrderCache(ttl=update_interval * 3)
        self._chat_id: Optional[int] = None
        self._task: Optional[asyncio.Task] = None
        self._stop = asyncio.Event()

    # ─── Public API ───────────────────────────────────────────────────────

    async def start(self):
        await self.app.start()
        result = await self.app.invoke(ResolveUsername(username=CRYPTOBOT_USERNAME))
        self._chat_id = result.users[0].id
        logger.info("✅ CryptoBotUserbot: chat_id=%d", self._chat_id)

        # Відкриваємо чат і отримуємо меню з кнопками
        await self.app.send_message(self._chat_id, "/start")
        await asyncio.sleep(2.0)
        logger.info("✅ CryptoBotUserbot: ініціалізація завершена")

        self._task = asyncio.create_task(self._worker_loop())

    async def stop(self):
        self._stop.set()
        if self._task:
            self._task.cancel()
            try:
                await asyncio.wait_for(self._task, timeout=5.0)
            except (asyncio.CancelledError, asyncio.TimeoutError):
                pass
        await self.app.stop()

    def get_orders(self) -> tuple[list[Order], list[Order]]:
        return self.cache.get()

    # ─── Worker Loop ──────────────────────────────────────────────────────

    async def _worker_loop(self):
        while not self._stop.is_set():
            try:
                all_buy, all_sell = [], []

                for bank_code in self.banks:
                    buy = await self._fetch_list(bank_code, side="buy")
                    all_buy.extend(buy)
                    await asyncio.sleep(random.uniform(2.0, 4.0))

                    sell = await self._fetch_list(bank_code, side="sell")
                    all_sell.extend(sell)
                    await asyncio.sleep(random.uniform(2.0, 4.0))

                self.cache.update(self._dedup(all_buy), self._dedup(all_sell))

            except Exception as e:
                logger.error("❌ CryptoBot worker: %s", e, exc_info=True)

            jitter = random.uniform(-5.0, 5.0)
            await asyncio.sleep(self.interval + jitter)

    # ─── Navigation ───────────────────────────────────────────────────────

    async def _fetch_list(self, bank_code: str, side: str) -> list[Order]:
        bank_cb = self._code_to_callback(bank_code)
        if not bank_cb:
            return []

        side_cb = "market-trade-buy" if side == "buy" else "market-trade-sell"

        try:
            # 1. Перевіряємо позицію ОДИН РАЗ — чи є P2P меню (market-trade-buy або sell)
            on_p2p_menu = (
                await self._find_msg_with_button("market-trade-buy") or
                await self._find_msg_with_button("market-trade-sell")
            )
            if not on_p2p_menu:
                # Не на P2P меню — повертаємось
                await self._navigate_back_to_p2p()
                await asyncio.sleep(1.0)
                # Якщо все одно не на P2P меню — аварійний reset
                if not await self._find_msg_with_button("market-trade-buy"):
                    await self._reset_navigation()
                    await asyncio.sleep(1.0)

            # 2. Йдемо по маршруту з фіксованими паузами
            steps = [side_cb, "choose-asset-USDT", bank_cb]
            for cb in steps:
                if not await self._click(cb):
                    logger.warning("CryptoBot: не вдалося натиснути %r", cb)
                    await self._reset_navigation()
                    return []
                await asyncio.sleep(2.5)

            # 3. Читаємо список ордерів ОДИН раз
            msg = None
            async for m in self.app.get_chat_history(self._chat_id, limit=1):
                msg = m
                break

            if not msg or not msg.reply_markup:
                return []

            orders = []
            for row in msg.reply_markup.inline_keyboard:
                for btn in row:
                    if btn.callback_data in NAV_CALLBACKS:
                        continue
                    order = _parse_offer_button(btn.text, btn.callback_data, bank_code, side)
                    if order:
                        orders.append(order)

            logger.info("✅ CryptoBot [%s/%s]: %d ордерів", bank_code, side, len(orders))

            # 4. Повертаємось назад і чекаємо поки меню оновиться
            await self._navigate_back_to_p2p()
            await asyncio.sleep(1.5)  # пауза перед наступним fetch
            return orders

        except Exception as e:
            logger.error("CryptoBot _fetch_list [%s/%s]: %s", bank_code, side, e)
            await self._reset_navigation()
            return []

    async def _navigate_back_to_p2p(self):
        """
        Проходить весь ланцюжок back-кнопок від будь-якого рівня до P2P меню.
        Натискає кожну кнопку якщо вона є. Не перевіряє стан між кліками.

        Повний ланцюжок (якщо ми всередині ордера):
          Деталі ордера   -> back
          Список ордерів  -> market-trade-payment-methods
          Вибір банку     -> back
          Вибір активу    -> market  (тут є market-trade-buy = P2P меню)
        """
        full_chain = [
            "back",                         # деталі ордера -> список
            "market-trade-payment-methods", # список -> вибір банку
            "back",                         # вибір банку -> вибір активу
            "market",                       # вибір активу -> P2P меню
        ]
        for back_cb in full_chain:
            clicked = await self._click(back_cb)
            if clicked:
                logger.info("↩️  back: %s", back_cb)
                await asyncio.sleep(2.0)
        # Після ланцюжка маємо бути на P2P меню (market-trade-buy є)
        await asyncio.sleep(0.5)

    async def _reset_navigation(self):
        """Аварійний скид (Ядерна кнопка)."""
        logger.warning("🔄 Аварійне скидання навігації CryptoBot через /start...")
        await self.app.send_message(self._chat_id, "/start")
        await asyncio.sleep(3.0)

        if await self._find_msg_with_button("p2p"):
            await self._click("p2p")
            await asyncio.sleep(2.0)

    # ─── Pyrogram Helpers ─────────────────────────────────────────────────

    async def _find_msg_with_button(self, callback: str) -> Optional[tuple[int, object]]:
        """Знаходить повідомлення з потрібною кнопкою. Робить ОДИН запит."""
        async for msg in self.app.get_chat_history(self._chat_id, limit=3):
            if not msg.reply_markup:
                continue
            for row in msg.reply_markup.inline_keyboard:
                for btn in row:
                    if btn.callback_data == callback:
                        return msg.id, msg
        return None

    async def _invoke_on_msg(self, msg_id: int, callback: str) -> bool:
        """Надсилає callback через raw MTProto на конкретне повідомлення."""
        try:
            peer = await self.app.resolve_peer(self._chat_id)
            await self.app.invoke(
                GetBotCallbackAnswer(
                    peer=peer,
                    msg_id=msg_id,
                    data=callback.encode("utf-8"),
                )
            )
            return True
        except Exception as e:
            logger.warning("invoke_on_msg ПОМИЛКА [%s]: %s", callback, e)
            return False

    async def _click(self, callback: str) -> bool:
        """Знаходить кнопку і натискає її через raw MTProto."""
        result = await self._find_msg_with_button(callback)
        if result:
            msg_id, _ = result
            try:
                peer = await self.app.resolve_peer(self._chat_id)
                await self.app.invoke(
                    GetBotCallbackAnswer(
                        peer=peer,
                        msg_id=msg_id,
                        data=callback.encode("utf-8"),
                    )
                )
                return True
            except Exception as e:
                logger.debug("invoke_on_msg [%s]: %s", callback, e)
                return False
        return False

    async def _get_current_message(self, wait: float = 4.0):
        """Чекає оновлення повідомлення (з безпечним інтервалом)."""
        old_first = await self._get_first_button()

        deadline = time.monotonic() + wait
        while time.monotonic() < deadline:
            await asyncio.sleep(1.2) # ЗБІЛЬШЕНО щоб не ловити FloodWait
            async for msg in self.app.get_chat_history(self._chat_id, limit=1):
                if msg.reply_markup and msg.reply_markup.inline_keyboard:
                    new_first = msg.reply_markup.inline_keyboard[0][0].callback_data
                    if new_first != old_first:
                        return msg
                break

        async for msg in self.app.get_chat_history(self._chat_id, limit=1):
            return msg
        return None

    async def _get_first_button(self) -> Optional[str]:
        """Повертає callback першої кнопки поточного повідомлення."""
        async for msg in self.app.get_chat_history(self._chat_id, limit=1):
            if msg.reply_markup and msg.reply_markup.inline_keyboard:
                return msg.reply_markup.inline_keyboard[0][0].callback_data
        return None

    async def _wait_for_update(self, old_first: Optional[str], timeout: float = 4.0):
        """Чекає поки перша кнопка зміниться (= повідомлення оновилось)."""
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            await asyncio.sleep(1.2) # ЗБІЛЬШЕНО щоб не ловити FloodWait
            current = await self._get_first_button()
            if current != old_first:
                return
    # ─── Utils ────────────────────────────────────────────────────────────

    def _code_to_callback(self, code: str) -> Optional[str]:
        """internal_code → CryptoBot callback data."""
        return _BankRegistry.get_exchange_code(code, "CryptoBot")

    def _dedup(self, orders: list[Order]) -> list[Order]:
        seen, result = set(), []
        for o in orders:
            if o.id not in seen:
                seen.add(o.id)
                result.append(o)
        return result