"""
«Не змогли перевірити» ≠ «безпечно».

Коли відгуки недоступні — протухла сесія, біржа не віддає API, немає
інтернету — ризик-движок раніше просто не додавав жодного прапорця. Ордер
отримував risk_flag="OK", а в базу лягав вердикт «ризиків не виявлено» з
trade_recommendation="APPROVE". Далі цей вердикт брався з кешу як доведено
чистий, хоча перевірки не було взагалі.

Це найгірша форма помилки в ризиках: невідоме подається як перевірене.

Друга частина файла з'явилась після того, як виявилось, що тести стерегли
не той бік. Перевірялись `format_risk_line` і `risk_badge` — функції, яких
прод не викликає жодного разу. Живий рендер алерта — `_risk_badge(order)` —
прапор `UNKNOWN:` мовчки ігнорував: у словнику badges такого ключа немає, а
startswith-гілки для нього не було. Тобто тести були зелені, движок чесно
писав «відгуків не бачили», а людина цього не бачила ніде.
"""
from __future__ import annotations

import time
import unittest

from bot.formatters import _risk_badge, format_risk_line, risk_badge
from core.engine.risk_engine import _build_review_flags_from_summary

TECHNICAL_STATUSES = (
    "UNAVAILABLE", "NOT_SUPPORTED", "NO_AUTH", "NO_SESSION", "UNKNOWN",
    # Ці два движок раніше не знав: вони провалювались у гілку з
    # лічильниками, де 0/0 читалось як «претензій немає».
    "SESSION_EXPIRED", "API_ERROR",
)


class _Order:
    """Мінімум, який читає _risk_badge."""

    def __init__(self, risk_flag: str, trade_terms: str = "умови мерчанта"):
        self.risk_flag = risk_flag
        self.trade_terms = trade_terms


class TestReviewGapsAreVisible(unittest.TestCase):
    def test_technical_failure_is_flagged_not_silently_clean(self):
        for status in TECHNICAL_STATUSES:
            with self.subTest(status=status):
                flags = _build_review_flags_from_summary({"status": status})
                self.assertTrue(flags, f"{status} не лишив жодного сліду")
                self.assertTrue(
                    any(f.startswith("UNKNOWN:REVIEWS") for f in flags),
                    f"{status} має давати UNKNOWN:REVIEWS, отримано {flags}",
                )

    def test_unknown_never_blocks(self):
        # Фільтри в alert_dispatcher/taker_scanner шукають підрядок "BLOCK".
        # Якщо він тут з'явиться, без сесій перестане працювати все.
        for status in TECHNICAL_STATUSES:
            with self.subTest(status=status):
                flags = _build_review_flags_from_summary({"status": status})
                self.assertNotIn("BLOCK", ",".join(flags))

    def test_real_reviews_still_produce_normal_flags(self):
        # Успішна перевірка з чистим результатом не має позначатись як UNKNOWN.
        flags = _build_review_flags_from_summary(
            {"status": "OK", "positive": 500, "negative": 0, "neutral": 0, "bad_texts": []}
        )
        self.assertFalse(
            any(f.startswith("UNKNOWN") for f in flags),
            f"чисті відгуки позначено як неперевірені: {flags}",
        )


class TestStaleReviewsAreNotTreatedAsMissing(unittest.TestCase):
    """
    Відколи невдалий фетч перестав затирати відомі відгуки, з'явився третій
    стан: дані є, але вони не сьогоднішні. Він не має схлопуватись ані в
    «немає відгуків», ані в «свіжа перевірка».
    """

    def _stale(self, **over):
        base = {
            "status": "NO_SESSION",
            "positive": 90, "negative": 10, "neutral": 0,
            "bad_texts": [], "data_at": time.time() - 30 * 3600,
        }
        base.update(over)
        return base

    def test_known_reviews_survive_a_failed_refresh(self):
        flags = _build_review_flags_from_summary(self._stale())
        self.assertFalse(
            any(f.startswith("UNKNOWN:REVIEWS") for f in flags),
            f"відомі відгуки подано як невідомі: {flags}",
        )
        self.assertTrue(
            any(f.startswith("STALE_REVIEWS:") for f in flags),
            f"вік даних не позначено: {flags}",
        )

    def test_stale_data_is_still_evaluated(self):
        # 20 негативних із 100 — це 20%, вище порогу WARN (15%). Те, що сесія
        # впала сьогодні, не робить учорашні скарги неіснуючими.
        flags = _build_review_flags_from_summary(self._stale(positive=80, negative=20))
        self.assertTrue(
            any(f.startswith("BADREVIEWS") for f in flags),
            f"скарги загубились через технічний статус: {flags}",
        )

    def test_no_data_at_all_stays_unknown(self):
        # Успішного збору не було жодного разу — нулі тут чесні.
        flags = _build_review_flags_from_summary(
            self._stale(positive=0, negative=0, neutral=0, data_at=0)
        )
        self.assertTrue(any(f.startswith("UNKNOWN:REVIEWS") for f in flags), flags)
        self.assertFalse(any(f.startswith("STALE_REVIEWS") for f in flags), flags)


class TestLiveAlertRendererShowsGaps(unittest.TestCase):
    """
    Головне: перевіряємо ФУНКЦІЮ, ЯКУ ВИКЛИКАЄ ПРОД.

    `_risk_badge` — це те, що бачить людина в alert_builder, taker_builder і
    maker_builder. Саме тут прапор UNKNOWN раніше зникав безслідно.
    """

    def test_missing_reviews_are_shown_to_the_user(self):
        out = _risk_badge(_Order("UNKNOWN:REVIEWS:NO_SESSION"))
        self.assertIn("НЕ ПЕРЕВІРЕНО", out)
        self.assertIn("немає сесії", out)
        # Це не звинувачення мерчанта.
        self.assertNotIn("РИЗИК", out.upper())

    def test_every_blind_status_has_human_wording(self):
        for status in TECHNICAL_STATUSES:
            with self.subTest(status=status):
                out = _risk_badge(_Order(f"UNKNOWN:REVIEWS:{status}"))
                self.assertIn("НЕ ПЕРЕВІРЕНО", out)
                self.assertNotIn(
                    "технічна причина", out,
                    f"{status} не має людського формулювання в UNKNOWN_REASONS",
                )

    def test_real_risk_is_not_swallowed_by_the_gap(self):
        out = _risk_badge(_Order("UNKNOWN:REVIEWS:API_ERROR,BLOCK:TRIANGLE:щось"))
        self.assertIn("ТРИКУТНИК", out)
        self.assertIn("НЕ ПЕРЕВІРЕНО", out)
        # Ризик іде першим, пробіл у перевірці — після нього.
        self.assertLess(out.index("ТРИКУТНИК"), out.index("НЕ ПЕРЕВІРЕНО"))

    def test_terms_gap_is_left_to_the_terms_block(self):
        # Про умови пише _terms_block з поля terms_status. Дублювати тут —
        # означає сказати те саме двічі різними словами.
        self.assertEqual(_risk_badge(_Order("UNKNOWN:TERMS:NO_SESSION")), "")

    def test_stale_reviews_get_their_own_line(self):
        out = _risk_badge(_Order("STALE_REVIEWS:NO_SESSION:31h,BADREVIEWS:12% neg (3/25)"))
        self.assertIn("НЕ ОНОВЛЮВАЛИСЬ", out)
        self.assertIn("31h", out)
        self.assertIn("ПОГАНІ ВІДГУКИ", out)

    def test_short_mode_still_marks_the_gap(self):
        # Груповий алерт має лише емодзі — але мовчати й там не можна.
        self.assertIn("❔", _risk_badge(_Order("UNKNOWN:REVIEWS:NO_SESSION"), short=True))

    def test_clean_order_stays_silent(self):
        self.assertEqual(_risk_badge(_Order("OK")), "")
        self.assertEqual(_risk_badge(_Order("")), "")


class TestUnknownIsReadable(unittest.TestCase):
    """Допоміжний рендер: використовується дашбордом і як довідка."""

    def test_badge_marks_unknown_separately_from_risk(self):
        self.assertEqual(risk_badge("UNKNOWN:REVIEWS:NO_SESSION"), "❔")
        self.assertNotEqual(risk_badge("BLOCK:TRIANGLE"), "❔")

    def test_line_explains_why_there_is_no_verdict(self):
        line = format_risk_line("UNKNOWN:REVIEWS:NO_SESSION")
        self.assertIn("НЕ ПЕРЕВІРЕНО", line)
        self.assertIn("немає сесії", line)
        self.assertNotIn("РИЗИК", line.upper())

    def test_several_gaps_are_listed_together(self):
        line = format_risk_line("UNKNOWN:REVIEWS:NO_SESSION,UNKNOWN:TERMS:EMPTY")
        self.assertIn("відгуки", line)
        self.assertIn("умови угоди", line)

    def test_clean_order_stays_silent(self):
        self.assertEqual(format_risk_line("OK"), "")
        self.assertEqual(format_risk_line(""), "")


if __name__ == "__main__":
    unittest.main()
