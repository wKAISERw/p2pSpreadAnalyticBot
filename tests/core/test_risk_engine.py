# tests/test_risk_engine.py
"""
Unit-тести для core/risk_engine.py.

Покриття:
    TestRiskEngineSync      — sync analyze() без БД
    TestRiskEngineAsync     — _async_analyze() з моками БД / LLM
    TestReviewFlags         — _build_review_flags()
    TestBehaviorFlags       — _behavior()
"""

import sys
import importlib.util
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

# ── bootstrap ──────────────────────────────────────────────────────────────

_THIS_FILE = Path(__file__).resolve()
_TESTS_DIR = _THIS_FILE.parent
_PROJECT_ROOT = _TESTS_DIR.parent

# Resolve project root by walking up until we find config or core
for p in [_THIS_FILE] + list(_THIS_FILE.parents):
    if (p / "core").exists() and (p / "config").exists():
        _PROJECT_ROOT = p
        break

sys.path.insert(0, str(_PROJECT_ROOT))


def _load(module_name: str, rel_path: str):
    abs_path = _PROJECT_ROOT / rel_path
    if not abs_path.exists():
        raise FileNotFoundError(
            f"Не знайдено {abs_path}. "
            f"PROJECT_ROOT={_PROJECT_ROOT}. "
            f"Перевір що risk_engine.py є в core/"
        )
    spec = importlib.util.spec_from_file_location(module_name, str(abs_path))
    mod = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = mod
    spec.loader.exec_module(mod)
    return mod


risk_mod = _load("core.engine.risk_engine", "core/engine/risk_engine.py")

from core.engine.risk_engine import (
    RiskEngine,
    _build_pending_flag,
    _build_weak_regex_flag,
    _build_cached_flag,
    _is_trusted_merchant,
)


def make_order(**overrides):
    data = {
        "exchange": "Binance",
        "merchant_id": "m1",
        "merchant_name": "Test Merchant",
        "trade_terms": "звичайні умови",
        "finish_rate_pct": 98.0,
        "month_order_count": 120,
        "is_verified": True,
        "min_limit": 100.0,
        "max_limit": 10000.0,
        "risk_flag": "",
    }
    data.update(overrides)
    return SimpleNamespace(**data)


def make_regex_result(
    verdict="OK",
    risk_type="",
    reason="",
    needs_llm=False,
    score=0,
):
    return SimpleNamespace(
        verdict=verdict,
        risk_type=risk_type,
        reason=reason,
        needs_llm=needs_llm,
        score=score,
        matches=[],
        warn_flags=[],
        normalized_text="",
    )


def make_db_mock():
    import time
    db = MagicMock()
    db.is_blacklisted = AsyncMock(return_value=(False, ""))
    db.get_reviews_summary = AsyncMock(return_value={"positive": 0, "negative": 0, "neutral": 0, "bad_texts": []})
    db.get_verdict = AsyncMock(return_value=None)
    db.get_risk_score = AsyncMock(return_value=0)
    db.get_reason = AsyncMock(return_value=("", ""))
    db.save_verdict = AsyncMock()
    db.get_recent_snapshots = AsyncMock(return_value=[])
    db.find_digital_twins = AsyncMock(return_value=[])
    db.get_verdict_timestamp = AsyncMock(return_value=time.time())
    db.get_trade_recommendation = AsyncMock(return_value="APPROVE")  # v2.3: anti-recheck guard
    db.needs_review_fetch = AsyncMock(return_value=False)
    db.mark_rechecking = AsyncMock()
    return db



# ─────────────────────────────────────────────────────────────────────────────
class TestRiskEngineSync(unittest.TestCase):
    """Синхронний analyze() без БД."""

    def test_sync_block_sets_block_flag(self):
        engine = RiskEngine(db=None, llm_pool=None)
        order = make_order()

        rr = make_regex_result(
            verdict="BLOCK",
            risk_type="CASINO",
            reason="Казино / беттинг",
            score=100,
        )

        with patch.object(risk_mod, "regex_analyze", return_value=rr):
            out = engine.analyze(order)

        self.assertEqual(out.risk_flag, "BLOCK:CASINO:Казино / беттинг")

    def test_sync_needs_llm_sets_pending_flag(self):
        engine = RiskEngine(db=None, llm_pool=None)
        order = make_order()

        rr = make_regex_result(
            verdict="NEEDS_LLM",
            risk_type="CHAT_FIRST",
            reason="Сигналів: 1 (CHAT_FIRST) Score: 30",
            needs_llm=True,
            score=30,
        )

        with patch.object(risk_mod, "regex_analyze", return_value=rr):
            out = engine.analyze(order)

        self.assertEqual(out.risk_flag, "LLM_PENDING:CHAT_FIRST:S30")

    def test_sync_weak_regex_sets_regex_weak(self):
        engine = RiskEngine(db=None, llm_pool=None)
        order = make_order()

        rr = make_regex_result(
            verdict="OK",
            risk_type="CHAT_FIRST",
            reason="Слабкі сигнали: 1 (CHAT_FIRST) Score: 10",
            score=10,
        )

        with patch.object(risk_mod, "regex_analyze", return_value=rr):
            out = engine.analyze(order)

        self.assertEqual(out.risk_flag, "REGEX_WEAK:CHAT_FIRST:S10")

    def test_sync_with_db_sets_pending_and_schedules_async(self):
        db = make_db_mock()
        engine = RiskEngine(db=db, llm_pool=None)
        order = make_order()

        scheduled = {}

        def _capture_and_close(coro, name, **kwargs):
            scheduled["called"] = True
            scheduled["coro"] = coro
            scheduled["name"] = name
            coro.close()
            return MagicMock()

        # analyze() планує _async_analyze через core.utils.tasks.spawn —
        # він, на відміну від голого ensure_future, тримає посилання на таск
        # і логує виняток.
        with patch.object(risk_mod, "spawn", side_effect=_capture_and_close) as spawn_mock:
            out = engine.analyze(order)

        spawn_mock.assert_called_once()
        self.assertTrue(scheduled.get("called"))
        self.assertTrue(scheduled["name"].startswith("risk-analyze-"))
        self.assertEqual(out.risk_flag, "PENDING")


# ─────────────────────────────────────────────────────────────────────────────
class TestRiskEngineAsync(unittest.IsolatedAsyncioTestCase):
    """Асинхронний _async_analyze() з моками БД / LLM."""

    async def test_async_blacklist_overrides_everything(self):
        db = make_db_mock()
        db.is_blacklisted.return_value = (True, "manual blacklist")

        engine = RiskEngine(db=db, llm_pool=None)
        order = make_order()

        await engine._async_analyze(order, behavior_flags=[])

        self.assertEqual(order.risk_flag, "BLOCK:BLACKLIST:manual blacklist")
        db.save_verdict.assert_not_called()

    async def test_async_cached_ok_high_risk_score_sets_high_risk(self):
        db = make_db_mock()
        db.get_verdict.return_value = "OK"
        db.get_risk_score.return_value = 85

        engine = RiskEngine(db=db, llm_pool=None)
        order = make_order()

        await engine._async_analyze(order, behavior_flags=["LOW_STATS"])

        self.assertIn("HIGH_RISK_SCORE", order.risk_flag)
        self.assertIn("LOW_STATS", order.risk_flag)

    async def test_async_cached_verdict_uses_cached_flag(self):
        db = make_db_mock()
        db.get_verdict.return_value = "BLOCK"
        db.get_reason.return_value = ("CASINO", "cached regex block")

        engine = RiskEngine(db=db, llm_pool=None)
        order = make_order()

        await engine._async_analyze(order, behavior_flags=["LOW_STATS"])

        self.assertIn("BLOCK:CASINO:cached regex block", order.risk_flag)
        self.assertIn("LOW_STATS", order.risk_flag)

    async def test_async_block_reviews_override_cached(self):
        db = make_db_mock()
        db.get_verdict.return_value = "OK"
        db.get_risk_score.return_value = 10
        db.get_reviews_summary.return_value = {
            "positive": 1,
            "negative": 5,
            "neutral": 0,
            "bad_texts": ["мерчант шахрай"],
        }

        engine = RiskEngine(db=db, llm_pool=None)
        order = make_order()

        await engine._async_analyze(order, behavior_flags=[])

        self.assertTrue(order.risk_flag.startswith("NEEDS_LLM:BADREVIEWS:"))

    async def test_async_regex_block_saves_verdict(self):
        db = make_db_mock()
        llm = MagicMock()
        llm.schedule.return_value = True
        engine = RiskEngine(db=db, llm_pool=llm)
        order = make_order()

        rr = make_regex_result(
            verdict="BLOCK",
            risk_type="TRIANGLE",
            reason="Оплата від третьої особи",
            score=100,
        )

        with patch.object(risk_mod, "regex_analyze", return_value=rr):
            await engine._async_analyze(order, behavior_flags=["LOW_STATS"])

        llm.schedule.assert_called_once()
        db.save_verdict.assert_not_called()
        self.assertIn("LLM_PENDING:TRIANGLE:S100", order.risk_flag)
        self.assertIn("LOW_STATS", order.risk_flag)

    async def test_async_trusted_merchant_skips_llm_when_score_below_trusted_threshold(self):
        db = make_db_mock()
        db.get_risk_score.return_value = 10
        llm = MagicMock()

        engine = RiskEngine(db=db, llm_pool=llm)
        order = make_order(
            month_order_count=700,
            finish_rate_pct=99.2,
            is_verified=True,
        )

        rr = make_regex_result(
            verdict="NEEDS_LLM",
            risk_type="CHAT_FIRST",
            reason="Сигналів: 1 (CHAT_FIRST) Score: 40",
            needs_llm=True,
            score=40,
        )

        with patch.object(risk_mod, "regex_analyze", return_value=rr):
            await engine._async_analyze(order, behavior_flags=[])

        llm.schedule.assert_not_called()
        self.assertEqual(order.risk_flag, "REGEX_WEAK:CHAT_FIRST:S40")

    async def test_async_needs_llm_schedule_success_sets_pending(self):
        db = make_db_mock()
        db.get_risk_score.return_value = 5

        llm = MagicMock()
        llm.schedule.return_value = True

        engine = RiskEngine(db=db, llm_pool=llm)
        order = make_order()

        rr = make_regex_result(
            verdict="NEEDS_LLM",
            risk_type="SUSPICIOUS_BIZ",
            reason="Сигналів: 1 (SUSPICIOUS_BIZ) Score: 40",
            needs_llm=True,
            score=40,
        )

        with patch.object(risk_mod, "regex_analyze", return_value=rr):
            await engine._async_analyze(order, behavior_flags=["LOW_STATS"])

        llm.schedule.assert_called_once()
        self.assertIn("LLM_PENDING:SUSPICIOUS_BIZ:S40", order.risk_flag)
        self.assertIn("LOW_STATS", order.risk_flag)

    async def test_async_needs_llm_schedule_false_falls_back_to_regex_weak(self):
        db = make_db_mock()
        db.get_risk_score.return_value = 5

        llm = MagicMock()
        llm.schedule.return_value = False

        engine = RiskEngine(db=db, llm_pool=llm)
        order = make_order()

        rr = make_regex_result(
            verdict="NEEDS_LLM",
            risk_type="CHAT_FIRST",
            reason="Сигналів: 1 (CHAT_FIRST) Score: 30",
            needs_llm=True,
            score=30,
        )

        with patch.object(risk_mod, "regex_analyze", return_value=rr):
            await engine._async_analyze(order, behavior_flags=[])

        self.assertEqual(order.risk_flag, "REGEX_WEAK:CHAT_FIRST:S30")


# ─────────────────────────────────────────────────────────────────────────────
class TestReviewFlags(unittest.IsolatedAsyncioTestCase):

    async def test_build_review_flags_block_reviews(self):
        db = make_db_mock()
        db.get_reviews_summary.return_value = {
            "positive": 5,
            "negative": 5,
            "neutral": 0,
            "bad_texts": ["скам, не підтверджує оплату"],
        }

        engine = RiskEngine(db=db, llm_pool=None)
        flags = await engine._build_review_flags("Binance", "m1")

        self.assertEqual(len(flags), 1)
        self.assertTrue(flags[0].startswith("NEEDS_LLM:BADREVIEWS:"))

    async def test_build_review_flags_warn_reviews(self):
        db = make_db_mock()
        db.get_reviews_summary.return_value = {
            "positive": 15,
            "negative": 3,
            "neutral": 0,
            "bad_texts": ["були затримки"],
        }

        engine = RiskEngine(db=db, llm_pool=None)
        flags = await engine._build_review_flags("Binance", "m1")

        self.assertEqual(len(flags), 1)
        self.assertTrue(flags[0].startswith("BADREVIEWS:"))

    async def test_build_review_flags_empty_when_no_reviews(self):
        db = make_db_mock()
        db.get_reviews_summary.return_value = {
            "positive": 0,
            "negative": 0,
            "neutral": 0,
            "bad_texts": [],
        }

        engine = RiskEngine(db=db, llm_pool=None)
        flags = await engine._build_review_flags("Binance", "m1")

        self.assertEqual(flags, [])


# ─────────────────────────────────────────────────────────────────────────────
class TestBehaviorFlags(unittest.TestCase):

    def test_behavior_low_stats(self):
        engine = RiskEngine(db=None, llm_pool=None)
        order = make_order(month_order_count=10, finish_rate_pct=80.0)

        flags = engine._behavior(order)

        self.assertIn("LOW_STATS", flags)

    def test_behavior_perfect_rating_unverified(self):
        engine = RiskEngine(db=None, llm_pool=None)
        order = make_order(
            month_order_count=60,
            finish_rate_pct=99.95,
            is_verified=False,
        )

        flags = engine._behavior(order)

        self.assertIn("PERFECT_RATING", flags)

    def test_behavior_suspicious_limits(self):
        engine = RiskEngine(db=None, llm_pool=None)
        order = make_order(
            is_verified=False,
            month_order_count=80,
            min_limit=990.0,
            max_limit=1000.0,
        )

        flags = engine._behavior(order)

        self.assertIn("SUSPICIOUS_LIMITS", flags)


# ─────────────────────────────────────────────────────────────────────────────
class TestHelpers(unittest.IsolatedAsyncioTestCase):

    def test_build_pending_flag(self):
        rr = make_regex_result(risk_type="CHAT_FIRST", score=30)
        self.assertEqual(_build_pending_flag(rr), "LLM_PENDING:CHAT_FIRST:S30")

    def test_build_weak_regex_flag(self):
        rr = make_regex_result(risk_type="CHAT_FIRST", score=10)
        self.assertEqual(_build_weak_regex_flag(rr), "REGEX_WEAK:CHAT_FIRST:S10")

    def test_is_trusted_merchant_true(self):
        order = make_order(month_order_count=700, finish_rate_pct=98.0)
        self.assertTrue(_is_trusted_merchant(order, risk_score=10))

    def test_is_trusted_merchant_false(self):
        order = make_order(month_order_count=100, finish_rate_pct=98.0)
        self.assertFalse(_is_trusted_merchant(order, risk_score=10))

    async def test_build_cached_flag_block(self):
        db = make_db_mock()
        db.get_reason.return_value = ("CASINO", "cached verdict")
        flag = await _build_cached_flag("BLOCK", "Binance", "m1", db)
        self.assertEqual(flag, "BLOCK:CASINO:cached verdict")

    async def test_build_cached_flag_ok(self):
        db = make_db_mock()
        flag = await _build_cached_flag("OK", "Binance", "m1", db)
        self.assertEqual(flag, "OK")


if __name__ == "__main__":
    unittest.main(verbosity=2)
