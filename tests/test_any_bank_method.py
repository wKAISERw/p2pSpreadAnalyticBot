"""
«Банківський переказ» — не банк, а знята умова.

Біржі кладуть у список методів оплати не лише банки: OKX віддає «Bank
Transfer», CryptoBot — «Global Transfer». Це переказ на рахунок, і
приймається він З БУДЬ-ЯКОГО банку.

Система бачила в цьому черговий невідомий «банк», якого в користувача
немає, і відмовляла на ордерах, які насправді підходять усім: «немає
твоєї картки» при трьох активних картках. Найгірше — виглядало це як
прогалина в реєстрі банків, тобто вело шукати проблему не там.
"""
from __future__ import annotations

import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from config.banks import bank_view_list, is_any_bank
from core.engine.card_routing import resolve_route
from core.storage.merchant_db import MerchantDB

USER_ID = 9191


class TestDetection(unittest.TestCase):
    def test_payment_methods_are_recognised(self):
        for value in ("Bank Transfer", "bank transfer", "Банковский перевод",
                      "transfer", "Global Transfer", "bank"):
            with self.subTest(value=value):
                self.assertTrue(is_any_bank(value))

    def test_real_banks_are_not(self):
        # Найнебезпечніша помилка тут — прийняти справжній банк за «будь-який»
        # і зняти обмеження, якого мерчант не знімав.
        for value in ("Monobank", "43", "privatbank", "Ощадбанк", "99", ""):
            with self.subTest(value=value):
                self.assertFalse(is_any_bank(value))

    def test_view_marks_it_instead_of_calling_unknown(self):
        rows = {r["code"]: r for r in bank_view_list(["Bank Transfer", "43"])}

        transfer = rows["Bank Transfer"]
        self.assertTrue(transfer["anyBank"])
        # `known: false` означає «немає в реєстрі», і для способу оплати це
        # неправда — реєстру банків він не стосується взагалі.
        self.assertTrue(transfer["known"])
        self.assertNotIn("код", transfer["name"])

        self.assertFalse(rows["43"]["anyBank"])


class TestRoute(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self._tmp = TemporaryDirectory()
        self.db = MerchantDB(db_path=Path(self._tmp.name) / "t.db")
        await self.db.start()
        await self.db.register_user(USER_ID, USER_ID)
        for i, (bank, bal) in enumerate(
            [("monobank", 21_000.0), ("sense", 4_800.0), ("pumb", 5_000.0)]
        ):
            await self.db.add_card({
                "id": f"c{i}", "owner_id": USER_ID, "bank_name": bank,
                "last_four": f"111{i}", "balance": bal, "status": "active",
            })

    async def asyncTearDown(self):
        await self.db.stop()
        self._tmp.cleanup()

    async def test_transfer_only_order_is_not_a_dead_end(self):
        # Мерчант не назвав жодного банку — лише спосіб. Раніше маршрут
        # виходив порожнім, і ордер відпадав як «немає картки цього банку».
        route = await resolve_route(self.db, USER_ID, ["Bank Transfer"])

        self.assertTrue(route.banks, "маршрут порожній на ордері «переказ»")
        self.assertIn(route.primary, {"monobank", "sense", "pumb"})

    async def test_transfer_declares_every_own_bank(self):
        route = await resolve_route(self.db, USER_ID, ["Bank Transfer"])
        # Заявленими вважаються всі свої: узгоджувати в чаті нема чого,
        # мерчант сам сказав «звідки завгодно».
        self.assertEqual(set(route.declared), {"monobank", "sense", "pumb"})

    async def test_named_bank_still_narrows_the_route(self):
        # Без «переказу» поведінка стара: лише те, що мерчант назвав.
        route = await resolve_route(self.db, USER_ID, ["43"])
        self.assertEqual(route.declared, ["monobank"])
        self.assertEqual(route.banks, ["monobank"])

    async def test_transfer_alongside_named_bank_still_opens_all(self):
        # «Приват АБО переказ» — переказ однаково знімає обмеження.
        route = await resolve_route(self.db, USER_ID, ["14", "Bank Transfer"])
        self.assertEqual(set(route.declared), {"privatbank", "monobank", "sense", "pumb"})


if __name__ == "__main__":
    unittest.main()
