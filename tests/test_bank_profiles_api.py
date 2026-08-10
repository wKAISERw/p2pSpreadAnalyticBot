"""
Довідник банків назовні: GET /banks/profiles.

Профілі банків (ліміти, комісії, нічні вікна, спільні ліцензії) з'явились у
config/banks.py і одразу почали керувати дефолтними лімітами карток. Але
дашборд про них не знав: поле «місячний ліміт 60 000» виглядало магічним
числом, і незрозуміло було, чи його задав користувач, чи довідник.

Тест фіксує дві речі, які легко зламати непомітно:
  * форму відповіді — фронтенд типізує її вручну (types.ts, BankProfile);
  * узгодженість `default_limits` із тим, що реально підставляє движок.
"""
from __future__ import annotations

import asyncio
import unittest

from config.banks import BANK_PROFILES, UNLIMITED, normalize_bank
from config.card_limits import default_limits_for_bank


def _profiles() -> list[dict]:
    from api.routers.control import get_bank_profiles
    return asyncio.run(get_bank_profiles())


class TestBankProfilesEndpoint(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.rows = _profiles()
        cls.by_slug = {r["slug"]: r for r in cls.rows}

    def test_every_profile_is_exposed(self):
        self.assertEqual(len(self.rows), len(BANK_PROFILES))
        self.assertEqual(set(self.by_slug), set(BANK_PROFILES))

    def test_shape_matches_what_the_dashboard_types(self):
        # Ключі camelCase — як решта ендпоінтів. Фронтенд типізує їх
        # вручну, тож зникле поле він виявить лише в рантаймі.
        expected = {
            "slug", "name", "tier", "tradable", "safeMonthlyUah",
            "maxMonthlyUah", "safeTxPerDay", "singleTxLimitUah",
            "businessDaysOnly", "licenseGroup", "terminationFeePct",
            "thirdPartyFriendly", "note", "p2pFee", "nightWindow",
            "defaultLimits",
        }
        for row in self.rows:
            with self.subTest(bank=row["slug"]):
                self.assertEqual(set(row), expected)

    def test_slug_is_canonical(self):
        # Ключ має переживати normalize_bank незмінним, інакше ліміти
        # ляжуть у банк, якого движок не знає.
        for slug in self.by_slug:
            with self.subTest(slug=slug):
                self.assertEqual(normalize_bank(slug), slug)

    def test_unlimited_single_tx_is_null_not_minus_one(self):
        # У полі суми -1 фронтенд показав би як «-1 ₴».
        mono = self.by_slug["monobank"]
        self.assertEqual(BANK_PROFILES["monobank"].single_tx_limit_uah, UNLIMITED)
        self.assertIsNone(mono["singleTxLimitUah"])

    def test_default_limits_match_the_engine(self):
        # Те, що показує дашборд, і те, що движок підставить картці, —
        # мусить бути однією цифрою.
        for slug, row in self.by_slug.items():
            with self.subTest(bank=slug):
                engine = default_limits_for_bank(slug)
                self.assertEqual(row["defaultLimits"]["maxTxPerDay"], engine["max_tx_per_day"])
                self.assertEqual(row["defaultLimits"]["monthlyOutMax"], engine["monthly_out_max"])

    def test_izibank_is_not_given_fifteen_transactions(self):
        # Регресія на головну знахідку розділу 7 плану: спільні дефолти
        # дозволяли 15 переказів там, де безпечно 2–3.
        izi = self.by_slug["izibank"]
        self.assertLessEqual(izi["defaultLimits"]["maxTxPerDay"], 5)
        self.assertLessEqual(izi["defaultLimits"]["monthlyOutMax"], 100_000)


class TestLicenseGroups(unittest.TestCase):
    """Спільна ліцензія — це ще й спільний фінмон, тож група має бути парною."""

    def setUp(self):
        self.rows = _profiles()

    def test_known_groups_have_at_least_two_members(self):
        groups: dict[str, list[str]] = {}
        for row in self.rows:
            if row["licenseGroup"]:
                groups.setdefault(row["licenseGroup"], []).append(row["slug"])

        self.assertTrue(groups, "жодної групи спільної ліцензії не оголошено")
        for group, members in groups.items():
            with self.subTest(group=group):
                self.assertGreaterEqual(
                    len(members), 2,
                    f"група {group} має одного учасника ({members}) — "
                    f"тоді вона нічого не означає",
                )

    def test_confirmed_pairs_share_a_group(self):
        # Обидві пари підтверджені операційною зведенкою, див. розділ 6.4
        # плану. Слаг банку («taskombank») і назва групи («tascombank»)
        # написані по-різному — звіряємось із самою групою, а не з іменем.
        by_slug = {r["slug"]: r for r in self.rows}
        for left, right in (("izibank", "taskombank"), ("bvr", "vostok")):
            with self.subTest(pair=(left, right)):
                self.assertEqual(
                    by_slug[left]["licenseGroup"],
                    by_slug[right]["licenseGroup"],
                )
                self.assertTrue(by_slug[left]["licenseGroup"])


if __name__ == "__main__":
    unittest.main()
