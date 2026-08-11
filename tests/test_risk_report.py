# tests/test_risk_report.py
"""
Друга половина етапу 2: прапори як структура.

`order.risk_flag` — рядок, склеєний комами. `risk_flags.py` навчив читати
його списком замість підрядка, але розбирати кожен прапор далі доводилось
на місці: двадцять один `startswith` по шести файлах.

Найдорожче тут не дублювання, а тиша. Прапор нового виду не підходив під
жодну гілку й зникав без сліду — рівно так `UNKNOWN:REVIEWS:*` півроку не
показувався людині взагалі, хоча движок його чесно ставив.

Тому головний тест тут не «розбирає правильно», а **«нічого не губить»**.
"""
from __future__ import annotations

import unittest

from core.engine.risk_report import (
    BLIND, BLOCK, INFO, META, PENDING, REVIEWS, RISK, Finding, RiskReport,
    parse_finding,
)

REAL = (
    "LOW_STATS,"
    "BLOCK:TRIANGLE:приймаю від третіх осіб,"
    "STALE_REVIEWS:NO_SESSION:30h,"
    "UNKNOWN:REVIEWS:NO_SESSION,"
    "BANKA_JAR_BLOCKED,"
    "LLM_PENDING:BEHAVIOR:S40:C55,"
    "BADREVIEWS:3"
)


class TestNothingIsSilentlyDropped(unittest.TestCase):
    def test_every_flag_becomes_a_finding(self):
        report = RiskReport.from_flag(REAL)
        self.assertEqual(len(report.findings), 7)

    def test_unknown_flag_shape_survives_as_info(self):
        # Головне правило модуля: незнайоме не зникає, а лишається сирим.
        report = RiskReport.from_flag("СЮРПРИЗ_2027:щось нове")
        self.assertEqual(len(report.findings), 1)
        self.assertEqual(report.findings[0].kind, INFO)
        self.assertEqual(report.findings[0].raw, "СЮРПРИЗ_2027:щось нове")

    def test_raw_round_trips(self):
        for part in REAL.split(","):
            with self.subTest(part=part):
                self.assertEqual(parse_finding(part).raw, part)

    def test_empty_flag_gives_quiet_report(self):
        report = RiskReport.from_flag("")
        self.assertTrue(report.is_quiet)
        self.assertEqual(report.findings, ())

    def test_quiet_is_not_the_same_as_clean(self):
        # Тиша означає «нічого не спрацювало», а не «перевірено, чисто».
        # Що саме встигли перевірити, каже coverage — і саме цю різницю
        # весь реворк і розводить.
        report = RiskReport.from_flag("")
        self.assertTrue(report.is_quiet)
        self.assertFalse(report.has_block)
        self.assertEqual(report.blind, [])


class TestKinds(unittest.TestCase):
    def test_block_is_recognised(self):
        self.assertTrue(RiskReport.from_flag(REAL).has_block)

    def test_blocked_suffix_is_not_a_block(self):
        # Той самий підрядковий баг, що й у risk_flags: `BANKA_JAR_BLOCKED`
        # містить «BLOCK», але блоком не є — це метадані для персональних
        # фільтрів.
        report = RiskReport.from_flag("BANKA_JAR_BLOCKED,FOP_TOV_BLOCKED")
        self.assertFalse(report.has_block)
        self.assertEqual(len(report.of_kind(META)), 2)

    def test_pending_is_not_a_risk(self):
        report = RiskReport.from_flag("LLM_PENDING:BEHAVIOR:S40")
        self.assertEqual(report.findings[0].kind, PENDING)
        self.assertEqual(report.risks, [])

    def test_reviews_flags_are_grouped(self):
        report = RiskReport.from_flag("BADREVIEWS:3,NEEDS_LLM:BADREVIEWS:скам")
        self.assertEqual(len(report.of_kind(REVIEWS)), 2)

    def test_categories_come_from_risks_only(self):
        report = RiskReport.from_flag(REAL)
        self.assertIn("TRIANGLE", report.categories)
        self.assertNotIn("REVIEWS", report.categories)


class TestBlindnessHasOneShape(unittest.TestCase):
    """
    Два прапори про одне й те саме написані по-різному.

    `UNKNOWN:REVIEWS:NO_SESSION` кладе предмет у другу позицію, а
    `STALE_REVIEWS:NO_SESSION:30h` — у саму назву. Читач не повинен знати
    про цю різницю.
    """

    def test_unknown_reviews(self):
        f = parse_finding("UNKNOWN:REVIEWS:NO_SESSION")
        self.assertTrue(f.is_blind)
        self.assertEqual(f.what, "REVIEWS")
        self.assertEqual(f.why, "NO_SESSION")

    def test_stale_reviews(self):
        f = parse_finding("STALE_REVIEWS:NO_SESSION:30h")
        self.assertTrue(f.is_blind)
        self.assertEqual(f.what, "REVIEWS")
        self.assertEqual(f.why, "NO_SESSION")
        self.assertEqual(f.detail, "30h")

    def test_both_land_in_blind(self):
        report = RiskReport.from_flag(REAL)
        self.assertEqual(len(report.blind), 2)
        self.assertEqual({f.what for f in report.blind}, {"REVIEWS"})

    def test_non_blind_finding_has_no_what(self):
        f = parse_finding("BLOCK:TRIANGLE:деталь")
        self.assertEqual(f.what, "")
        self.assertEqual(f.why, "")


class TestDetailsSurviveCommasAndColons(unittest.TestCase):
    def test_colons_in_detail_are_kept(self):
        f = parse_finding("LLM_PENDING:BEHAVIOR:S40:C55")
        self.assertEqual(f.subject, "BEHAVIOR")
        self.assertEqual(f.detail, "S40:C55")

    def test_flag_without_parts_keeps_its_name(self):
        f = parse_finding("LOW_STATS")
        self.assertEqual(f.raw, "LOW_STATS")
        self.assertEqual(f.subject, "")

    def test_scrubbed_reason_stays_in_one_finding(self):
        from core.engine import risk_flags

        # Кома в причині розривала прапор навпіл — `scrub` для цього і є.
        reason = risk_flags.scrub("треті особи, дропи, обнал")
        report = RiskReport.from_flag(f"BLOCK:TRIANGLE:{reason}")
        self.assertEqual(len(report.findings), 1)
        self.assertIn("дропи", report.findings[0].detail)


class TestAgreesWithRiskFlags(unittest.TestCase):
    """Два погляди на ті самі прапори не мають розходитись."""

    CASES = (
        "", "OK", REAL, "BLOCK:BLACKLIST:скам", "BANKA_JAR_BLOCKED",
        "LLM_PENDING:PROACTIVE", "BLOCK", "LOW_STATS,BADREVIEWS:2",
    )

    def test_has_block_matches(self):
        from core.engine import risk_flags

        for flag in self.CASES:
            with self.subTest(flag=flag):
                self.assertEqual(
                    RiskReport.from_flag(flag).has_block, risk_flags.has_block(flag)
                )

    def test_finding_count_matches_parse(self):
        from core.engine import risk_flags

        for flag in self.CASES:
            with self.subTest(flag=flag):
                self.assertEqual(
                    len(RiskReport.from_flag(flag).findings), len(risk_flags.parse(flag))
                )


if __name__ == "__main__":
    unittest.main()
