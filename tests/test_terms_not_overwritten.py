# tests/test_terms_not_overwritten.py
"""
У полі умов лежать слова мерчанта — і більше нічого.

`core/engine/terms_status.py` створили саме щоб службова причина «чому умов
немає» не писалась усередину `trade_terms`. Wallet і CryptoBot виправили,
`tests/test_terms_status.py` це стереже. А `risk_engine` лишився з тією ж
звичкою: шість місць у гілках дотягування умов Binance/OKX робили

    order.trade_terms = "не вдалося отримати доступ до умов через ..."

Далі це речення жило власним життям:
  * `regex_analyze` проганяв його через правила як текст мерчанта;
  * LLM отримувала його всередині <merchant_terms> і переказувала в
    terms_summary як умови угоди;
  * `hash_terms` від нього виходив ОДНАКОВИЙ для всіх мерчантів без сесії —
    тобто ставав спільним ключем кешу вердиктів і полем terms_hash у
    снапшотах, на якому тримається детектор FLICKER_RELIST.

Окремо перевіряємо те, чого раніше не було зовсім: коротка версія умов із
пошукової видачі при невдалому дотягуванні має ЗБЕРІГАТИСЬ. Стара гілка
затирала і її.
"""
from __future__ import annotations

import unittest
from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock

from core.engine import terms_status
from core.engine.risk_engine import _note_terms_fetched, _note_terms_unavailable
from exchanges.base import Order


def _order(terms: str = "", status: str = terms_status.OK) -> Order:
    return Order(
        id="bn_1", price=Decimal("41.5"), available_amount=Decimal("1000"),
        min_limit=Decimal("1000"), max_limit=Decimal("50000"),
        merchant_id="m1", merchant_name="Merchant",
        month_order_count=500, finish_rate_pct=99.0,
        exchange="Binance", trade_terms=terms, terms_status=status,
    )


class TestFailureNeverWritesProseIntoTerms(unittest.TestCase):
    SENTINEL = "не вдалося отримати доступ"

    def test_missing_session_leaves_terms_empty(self):
        o = _order(terms="", status=terms_status.UNKNOWN)
        out = _note_terms_unavailable(o, terms_status.NO_SESSION)

        self.assertEqual(out, "")
        self.assertEqual(o.trade_terms, "")
        self.assertNotIn(self.SENTINEL, o.trade_terms)
        self.assertEqual(o.terms_status, terms_status.NO_SESSION)

    def test_reason_goes_to_status_not_into_the_text(self):
        for status in (terms_status.NO_SESSION, terms_status.FETCH_FAILED):
            with self.subTest(status=status):
                o = _order(terms="")
                _note_terms_unavailable(o, status)
                self.assertEqual(o.terms_status, status)
                self.assertEqual(o.trade_terms, "")

    def test_short_terms_from_search_survive_a_failed_fetch(self):
        # Обрізані умови з видачі — це справжні слова мерчанта. Вони кращі
        # за порожнечу, і стара гілка дарма їх затирала.
        o = _order(terms="тільки своя картка")
        out = _note_terms_unavailable(o, terms_status.FETCH_FAILED)

        self.assertEqual(out, "тільки своя картка")
        self.assertEqual(o.trade_terms, "тільки своя картка")
        # Статус не псуємо: умови в нас є.
        self.assertEqual(o.terms_status, terms_status.OK)

    def test_blind_status_is_recognised_as_blind(self):
        o = _order(terms="")
        _note_terms_unavailable(o, terms_status.NO_SESSION)
        self.assertTrue(terms_status.is_blind(o.terms_status))


class TestSuccessfulFetch(unittest.TestCase):
    def test_full_remarks_replace_the_short_version(self):
        o = _order(terms="тільки своя")
        out = _note_terms_fetched(o, "Тільки Своя Картка, Оплата 15 хв", "тільки своя")

        self.assertEqual(out, "тільки своя картка, оплата 15 хв")
        self.assertEqual(o.terms_status, terms_status.OK)

    def test_profile_without_remarks_means_merchant_wrote_nothing(self):
        # Це єдина гілка, де порожнеча — факт про МЕРЧАНТА, а не про нас.
        o = _order(terms="")
        _note_terms_fetched(o, "", "")

        self.assertEqual(o.trade_terms, "")
        self.assertEqual(o.terms_status, terms_status.EMPTY)
        self.assertFalse(terms_status.is_blind(o.terms_status))

    def test_empty_profile_does_not_erase_search_terms(self):
        o = _order(terms="тільки своя картка")
        out = _note_terms_fetched(o, "", "тільки своя картка")

        self.assertEqual(out, "тільки своя картка")
        self.assertEqual(o.terms_status, terms_status.OK)


class TestHashesStopColliding(unittest.TestCase):
    """
    Найдорожчий наслідок старої гілки: однаковий службовий текст давав
    однаковий terms_hash у всіх мерчантів без сесії.
    """

    def test_two_blind_merchants_do_not_share_a_verdict_key(self):
        from core.storage.base_db import hash_terms

        a, b = _order(terms=""), _order(terms="")
        _note_terms_unavailable(a, terms_status.NO_SESSION)
        _note_terms_unavailable(b, terms_status.FETCH_FAILED)

        # Порожні умови теж дають однаковий хеш — але тепер це чесна
        # «порожнеча», а не текст, який регекс і LLM аналізують як умови.
        self.assertEqual(hash_terms(a.trade_terms), hash_terms(""))
        self.assertEqual(hash_terms(b.trade_terms), hash_terms(""))

    def test_sentinel_would_have_been_analysed_as_merchant_text(self):
        # Фіксуємо, чому стара поведінка була небезпечною: службове речення
        # проходить нормалізацію й потрапляє в аналіз нарівні з умовами.
        from core.analysis.regex_analyzer import analyze

        sentinel = "не вдалося отримати доступ до умов через відсутність активної сесії"
        result = analyze(sentinel, 99.0, 500, True)
        self.assertEqual(result.normalized_text, sentinel)


class TestEngineBranchesUseTheHelpers(unittest.IsolatedAsyncioTestCase):
    """Наскрізь: гілка Binance без сесії не чіпає текст умов."""

    async def test_binance_without_session_keeps_terms_clean(self):
        from core.engine.risk_engine import RiskEngine

        db = MagicMock()
        db.get_auth_session = AsyncMock(return_value=(None, None, 0))
        db.is_blacklisted = AsyncMock(return_value=(False, ""))
        db.get_reviews_summary = AsyncMock(
            return_value={"positive": 0, "negative": 0, "neutral": 0,
                          "bad_texts": [], "status": "NO_SESSION", "data_at": 0}
        )
        db.get_recent_snapshots = AsyncMock(return_value=[])
        db.find_digital_twins = AsyncMock(return_value=[])
        db.get_verdict = AsyncMock(return_value=None)
        db.get_risk_score = AsyncMock(return_value=0)
        db.get_verdict_timestamp = AsyncMock(return_value=0)
        db.get_trade_recommendation = AsyncMock(return_value="PENDING")
        db.needs_review_fetch = AsyncMock(return_value=False)
        db.save_verdict = AsyncMock()

        fetcher = MagicMock()
        fetcher._binance = MagicMock()
        fetcher.fetch_now = AsyncMock(return_value={})

        engine = RiskEngine(db=db, llm_pool=None, review_fetcher=fetcher)
        order = _order(terms="", status=terms_status.UNKNOWN)
        await engine._async_analyze_inner(order, [])

        self.assertEqual(order.trade_terms, "")
        self.assertEqual(order.terms_status, terms_status.NO_SESSION)
        self.assertIn("UNKNOWN:TERMS:NO_SESSION", order.risk_flag)


if __name__ == "__main__":
    unittest.main()
