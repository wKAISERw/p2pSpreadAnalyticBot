# core/risk_engine.py
"""
P2P Risk Engine — захист капіталу від трикутників, казино, скамерів.

Архітектура:
  ReputationCache   — in-memory кеш вироків (12 год TTL)
  MerchantDB        — персистентна SQLite база між сесіями
  ReviewFetcher     — асинхронний фетчер відгуків (Binance/Bybit/OKX)
  TextAnalyzer      — regex по умовах угоди
  BehaviorAnalyzer  — скоринг по статистиці
  RiskEngine        — оркестратор
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

# Поріг поганих відгуків (%) для флага BAD_REVIEWS
BAD_REVIEW_PCT_THRESHOLD = 15.0
BAD_REVIEW_MIN_COUNT     = 3   # Мінімум негативних щоб вважатись значущим

# Підозрілий 100% рейтинг — можлива накрутка
PERFECT_RATING_MIN_ORDERS = 50   # Менше угод — 100% ще нормально (просто новий)
PERFECT_RATING_THRESHOLD  = 99.9 # Вище цього — підозріло якщо угод багато


# ── ReputationCache (in-memory LRU+TTL) ──────────────────────────────────────
class ReputationCache:
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
    _TRIANGLE = re.compile(
        r"тре(тя|тіх|тіх)|третіх\s+осіб|3\s*особ|third\s*party|third\s*person"
        r"|від\s*третіх|від\s*інших|не\s*від\s*свого|чужі\s*(карт|рахун)"
        r"|перерахун[ок]{0,2}\s*від\s*інш"
        r"|only\s*personal|тільки\s*(особист|своя\s*карт)"
        r"|без\s*(коментарів?|комент|призначен)",
        re.IGNORECASE,
    )
    _CASINO = re.compile(
        r"казино|casino|1xbet|1x\s*bet|melbet|mostbet|betway|parimatch"
        r"|покер|poker|ставк[иа]|букмекер|bookie"
        r"|пишіть\s+у\s+чат|пишіть\s+в\s+чат|пишіть\s+мені"
        r"|процесинг|processing|агрегатор|обмінник",
        re.IGNORECASE,
    )
    _SUSPICIOUS = re.compile(
        r"анонімн|anonymous|без\s*перевірк|не\s*питаю|no\s*questions"
        r"|фізична\s*особа\s*підприємець|фоп\s*оплат",
        re.IGNORECASE,
    )

    def analyze(self, trade_terms: str) -> str:
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
    def analyze(self, order: Order) -> list[str]:
        flags = []
        exchange = order.exchange
        min_orders     = MIN_ORDERS.get(exchange, 30)
        min_completion = MIN_COMPLETION.get(exchange, 90.0)

        if order.month_order_count < min_orders or order.finish_rate_pct < min_completion:
            flags.append("LOW_STATS")

        if order.min_limit > 0 and order.max_limit > 0:
            spread_ratio = float(order.max_limit - order.min_limit) / float(order.max_limit)
            if spread_ratio < 0.02 and float(order.max_limit) > 500:
                if not (order.is_verified or order.month_order_count > 1000):
                    flags.append("SUSPICIOUS_LIMITS")

        return flags



def _extract_reason(bad_texts: list[str]) -> str:
    """Витягує найінформативнішу причину з текстів поганих відгуків."""
    from core.review_fetcher import BAD_KEYWORDS
    if not bad_texts:
        return ""
    # Знаходимо перший текст де є ключове слово
    for text in bad_texts:
        t = text.lower()
        for kw in BAD_KEYWORDS:
            if kw in t:
                # Повертаємо короткий фрагмент навколо ключового слова
                idx = t.find(kw)
                start = max(0, idx - 15)
                end   = min(len(text), idx + len(kw) + 30)
                snippet = text[start:end].strip()
                return f'"{snippet}"'
    # Немає ключових слів але є тексти — повертаємо початок першого
    return f'"{bad_texts[0][:50]}"' if bad_texts else ""


# ── RiskEngine (оркестратор) ──────────────────────────────────────────────────
class RiskEngine:
    def __init__(self, db=None, review_fetcher=None):
        """
        db             — MerchantDB (опційно, якщо None — персистентність вимкнена)
        review_fetcher — ReviewFetcher (опційно)
        """
        self.cache          = ReputationCache()
        self.text           = TextAnalyzer()
        self.behavior       = BehaviorAnalyzer()
        self._db            = db
        self._fetcher       = review_fetcher
        self._analyzed      = 0
        self._cache_hits    = 0

    def analyze(self, order: Order) -> Order:
        # 1. In-memory кеш
        cached = self.cache.get(order.exchange, order.merchant_id)
        if cached is not None:
            order.risk_flag = cached
            self._cache_hits += 1
            return order

        self._analyzed += 1
        all_flags = []

        # 2. Текстовий аналіз
        text_flag = self.text.analyze(order.trade_terms)
        if text_flag not in ("OK", "EMPTY_TERMS"):
            all_flags.append(text_flag)
            logger.debug("🚩 %s [%s] → %s", order.merchant_name, order.exchange, text_flag)

        # 3. Поведінковий аналіз
        behavior_flags = self.behavior.analyze(order)
        all_flags.extend(behavior_flags)

        # 4. Перевірка відгуків з БД (якщо є)
        if self._db and order.merchant_id:
            review_flag = self._check_reviews(order.exchange, order.merchant_id,
                                              order.merchant_name,
                                              order.month_order_count,
                                              order.finish_rate_pct)
            if review_flag:
                all_flags.append(review_flag)

            # Плануємо фетч відгуків якщо потрібно (неблокуючий)
            if self._fetcher:
                self._fetcher.schedule(order.exchange, order.merchant_id)

        # 5. Фінальний вирок
        if not all_flags:
            order.risk_flag = "EMPTY_TERMS" if text_flag == "EMPTY_TERMS" else "OK"
        else:
            order.risk_flag = ",".join(all_flags)

        # 6. Кешуємо (тільки якщо немає поведінкових флагів — вони змінюються)
        if not behavior_flags:
            self.cache.set(order.exchange, order.merchant_id, order.risk_flag)

        # 7. Зберігаємо мерчанта в БД
        if self._db and order.merchant_id:
            self._db.upsert_merchant(
                order.exchange, order.merchant_id,
                order.merchant_name, order.month_order_count,
                order.finish_rate_pct,
            )

        return order

    def _check_reviews(self, exchange: str, merchant_id: str,
                       name: str, orders: int, completion: float) -> Optional[str]:
        """Перевіряє відгуки в БД. Повертає flag або None."""
        row = self._db.get(exchange, merchant_id)
        if row is None:
            return None

        neg     = row["reviews_neg"] or 0
        bad_pct = row["bad_review_pct"] or 0.0

        # Погані відгуки з підозрілими ключовими словами
        if bad_pct >= BAD_REVIEW_PCT_THRESHOLD and neg >= BAD_REVIEW_MIN_COUNT:
            bad_texts = self._db.get_bad_reviews(exchange, merchant_id)
            reason = _extract_reason(bad_texts)
            logger.warning("🚨 BAD_REVIEWS: %s [%s] %.0f%% негативних (%d) — %s",
                           name, exchange, bad_pct, neg, reason or "ключові слова")
            # Причина зберігається в order через окремий механізм — повертаємо з тегом
            return f"BAD_REVIEWS:{reason}" if reason else "BAD_REVIEWS"

        # Підозрілий ідеальний рейтинг — можлива накрутка
        if (orders >= PERFECT_RATING_MIN_ORDERS
                and completion >= PERFECT_RATING_THRESHOLD):
            logger.debug("⚠️ PERFECT_RATING: %s [%s] %.1f%% при %d угодах",
                         name, exchange, completion, orders)
            return "PERFECT_RATING"

        return None

    def analyze_batch(self, orders: list[Order]) -> list[Order]:
        for order in orders:
            self.analyze(order)
        return orders

    def stats(self) -> str:
        db_info = f", DB: {self._db is not None}" if self._db else ""
        return (f"RiskEngine: {self._analyzed} analyzed, "
                f"{self._cache_hits} cache hits, "
                f"{self.cache.size} in cache{db_info}")