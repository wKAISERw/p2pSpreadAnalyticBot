# core/risk_engine.py
"""
P2P Risk Engine — захист капіталу від трикутників, казино, скамерів.

Архітектура:
  ReputationCache   — кеш вироків (12 год TTL, щоб не аналізувати одного мерчанта 1000 разів)
  TextAnalyzer      — regex по умовах угоди (трикутники, казино, процесинг)
  BehaviorAnalyzer  — скоринг по статистиці (угоди, відсоток, ліміти)
  RiskEngine        — оркестратор, викликає підмодулі та пише risk_flag в Order
"""

import re
import logging
from typing import Optional
from collections import OrderedDict
from exchanges.base import Order

logger = logging.getLogger("RiskEngine")

# ── Плаваючі пороги по біржах ─────────────────────────────────────────────────
MIN_ORDERS: dict[str, int] = {
    "Binance":   50,
    "Bybit":     30,
    "OKX":       30,
    "Wallet":    10,
    "MEXC":      20,
    "CryptoBot": 15,
}

MIN_COMPLETION: dict[str, float] = {
    "Binance":   95.0,
    "Bybit":     92.0,
    "OKX":       92.0,
    "Wallet":    88.0,
    "MEXC":      90.0,
    "CryptoBot": 85.0,
}


# ── ReputationCache ───────────────────────────────────────────────────────────
class ReputationCache:
    """
    Простий LRU+TTL кеш без зовнішніх залежностей.
    Ключ: "{exchange}:{merchant_id}"  Значення: risk_flag str
    TTL: 12 годин (43200 секунд)
    """
    def __init__(self, maxsize: int = 50_000, ttl: float = 43_200.0):
        self._maxsize = maxsize
        self._ttl = ttl
        self._store: OrderedDict[str, tuple[str, float]] = OrderedDict()

    def _key(self, exchange: str, merchant_id: str) -> str:
        return f"{exchange}:{merchant_id}"

    def get(self, exchange: str, merchant_id: str) -> Optional[str]:
        import time
        k = self._key(exchange, merchant_id)
        entry = self._store.get(k)
        if entry is None:
            return None
        flag, ts = entry
        if time.monotonic() - ts > self._ttl:
            del self._store[k]
            return None
        self._store.move_to_end(k)
        return flag

    def set(self, exchange: str, merchant_id: str, flag: str) -> None:
        import time
        k = self._key(exchange, merchant_id)
        if k in self._store:
            self._store.move_to_end(k)
        self._store[k] = (flag, time.monotonic())
        if len(self._store) > self._maxsize:
            self._store.popitem(last=False)

    def invalidate(self, exchange: str, merchant_id: str) -> None:
        self._store.pop(self._key(exchange, merchant_id), None)

    @property
    def size(self) -> int:
        return len(self._store)


# ── TextAnalyzer ──────────────────────────────────────────────────────────────
class TextAnalyzer:
    """
    NLP-фільтр по умовах угоди.
    Всі патерни — заздалегідь скомпільовані regex для швидкості.
    """

    # Трикутникові схеми — третя особа
    _TRIANGLE = re.compile(
        r"тре(тя|тіх|тіх)|третіх\s+осіб|3\s*особ|third\s*party|third\s*person"
        r"|від\s*третіх|від\s*інших|не\s*від\s*свого|чужі\s*(карт|рахун)"
        r"|перерахун[ок]{0,2}\s*від\s*інш"
        r"|only\s*personal|тільки\s*(особист|своя\s*карт)"
        r"|без\s*(коментарів?|комент|призначен)",  # "без коментарів" = трикутник
        re.IGNORECASE,
    )

    # Казино / ставки / процесинг
    _CASINO = re.compile(
        r"казино|casino|1xbet|1x\s*bet|melbet|mostbet|betway|parimatch"
        r"|покер|poker|ставк[иа]|букмекер|bookie"
        r"|пишіть\s+у\s+чат|пишіть\s+в\s+чат|пишіть\s+мені"
        r"|процесинг|processing|агрегатор|обмінник",
        re.IGNORECASE,
    )

    # Явно підозрілі патерни
    _SUSPICIOUS = re.compile(
        r"анонімн|anonymous|без\s*перевірк|не\s*питаю|no\s*questions"
        r"|фізична\s*особа\s*підприємець|фоп\s*оплат",
        re.IGNORECASE,
    )

    def analyze(self, trade_terms: str) -> str:
        """Повертає: 'TRIANGLE', 'CASINO', 'SUSPICIOUS', 'EMPTY_TERMS', 'OK'"""
        if not trade_terms or not trade_terms.strip():
            return "EMPTY_TERMS"

        text = trade_terms.lower()

        if self._TRIANGLE.search(text):
            return "TRIANGLE"
        if self._CASINO.search(text):
            return "CASINO"
        if self._SUSPICIOUS.search(text):
            return "SUSPICIOUS"

        return "OK"


# ── BehaviorAnalyzer ──────────────────────────────────────────────────────────
class BehaviorAnalyzer:
    """
    Аналізує поведінкові ознаки на основі статистики мерчанта.
    Плаваючі пороги залежно від біржі.
    """

    def analyze(self, order: Order) -> str:
        """Повертає: 'LOW_STATS', 'SUSPICIOUS_LIMITS', 'OK'"""
        exchange = order.exchange
        min_orders = MIN_ORDERS.get(exchange, 30)
        min_completion = MIN_COMPLETION.get(exchange, 90.0)

        # Перевірка статистики
        if order.month_order_count < min_orders:
            return "LOW_STATS"
        if order.finish_rate_pct < min_completion:
            return "LOW_STATS"

        # Аномально вузькі ліміти — ознака схеми під конкретну суму
        if order.min_limit > 0 and order.max_limit > 0:
            spread_ratio = float(order.max_limit - order.min_limit) / float(order.max_limit)
            # Якщо max - min < 2% від max → ліміт майже фіксований
            if spread_ratio < 0.02 and float(order.max_limit) > 500:
                return "SUSPICIOUS_LIMITS"

        return "OK"


# ── RiskEngine (оркестратор) ──────────────────────────────────────────────────
class RiskEngine:
    """
    Головний клас. Викликається з scanner.py після отримання ордерів.
    Збагачує кожен Order полем risk_flag.
    """

    def __init__(self):
        self.cache = ReputationCache()
        self.text = TextAnalyzer()
        self.behavior = BehaviorAnalyzer()
        self._analyzed = 0
        self._cache_hits = 0

    def analyze(self, order: Order) -> Order:
        """
        Аналізує один ордер і встановлює order.risk_flag.
        Повертає той самий об'єкт (мутує in-place).
        """
        # 1. Спочатку кеш — не витрачаємо час на вже відомих мерчантів
        cached = self.cache.get(order.exchange, order.merchant_id)
        if cached is not None:
            order.risk_flag = cached
            self._cache_hits += 1
            return order

        self._analyzed += 1

        # 2. TextAnalyzer — умови угоди
        text_flag = self.text.analyze(order.trade_terms)
        if text_flag not in ("OK", "EMPTY_TERMS"):
            order.risk_flag = text_flag
            self.cache.set(order.exchange, order.merchant_id, text_flag)
            logger.debug("🚩 %s [%s] → %s: %r",
                         order.merchant_name, order.exchange, text_flag, order.trade_terms[:60])
            return order

        # 3. BehaviorAnalyzer — статистика
        behavior_flag = self.behavior.analyze(order)
        if behavior_flag != "OK":
            order.risk_flag = behavior_flag
            # Поведінкові флаги НЕ кешуємо надовго — статистика змінюється
            return order

        # 4. Якщо умови порожні але статистика ок — позначаємо окремо
        if text_flag == "EMPTY_TERMS":
            order.risk_flag = "EMPTY_TERMS"
        else:
            order.risk_flag = "OK"

        # Кешуємо тільки чистих мерчантів
        self.cache.set(order.exchange, order.merchant_id, order.risk_flag)
        return order

    def analyze_batch(self, orders: list[Order]) -> list[Order]:
        """Аналізує список ордерів. Повертає той самий список."""
        for order in orders:
            self.analyze(order)
        return orders

    def stats(self) -> str:
        return (f"RiskEngine: {self._analyzed} analyzed, "
                f"{self._cache_hits} cache hits, "
                f"{self.cache.size} in cache")