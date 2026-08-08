"""
Банки окремо для режиму.

Колонок для банків три, і всі спільні: bank_codes, buy_bank_codes,
sell_bank_codes. Майстер Taker Buy записував обраний список просто в
buy_bank_codes — те саме поле, яке читає спред-режим. Тобто налаштувавши
банки в тейкері, користувач мовчки змінював банки купівлі для спредів, і
помітити це було майже неможливо: спред просто починав пропускати частину
зв'язок.

Тепер поверх базових списків є перевизначення на режим. Порожнє (типовий
випадок) означає «беремо спільні» — тому наявні налаштування працюють
рівно як раніше, доки перевизначення не задали явно.
"""
from __future__ import annotations

import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from fastapi import FastAPI
from fastapi.testclient import TestClient

from api import auth as auth_lib
from config import settings as settings_obj
from core.engine.bank_scope import (
    base_banks, has_override, normalize_overrides, resolve_banks,
)
from core.storage.merchant_db import MerchantDB
from core.storage.user_repo import SCANNER_MODES

USER_ID = 6200
BOT_TOKEN = "123456:TEST-TOKEN-FOR-SIGNING"


class TestResolution(unittest.TestCase):
    def test_without_overrides_everything_uses_shared_lists(self):
        user = {"bank_codes": ["43", "14"], "buy_bank_codes": ["43"], "sell_bank_codes": []}

        # Купівля має свій список, продаж падає на загальні.
        self.assertEqual(resolve_banks(user, "SPREAD", "buy"), ["43"])
        self.assertEqual(resolve_banks(user, "SPREAD", "sell"), ["43", "14"])
        # І для тейкера так само — доки немає перевизначення.
        self.assertEqual(resolve_banks(user, "TAKER_BUY", "buy"), ["43"])

    def test_override_applies_only_to_its_own_mode(self):
        user = {
            "bank_codes": ["43", "14"],
            "buy_bank_codes": ["43", "14"],
            "mode_bank_overrides": {"TAKER_BUY": {"buy": ["64"]}},
        }

        self.assertEqual(resolve_banks(user, "TAKER_BUY", "buy"), ["64"])
        # Головне твердження: спред не зачеплено.
        self.assertEqual(resolve_banks(user, "SPREAD", "buy"), ["43", "14"])

    def test_override_of_one_side_leaves_the_other_shared(self):
        user = {
            "bank_codes": ["43"],
            "sell_bank_codes": ["14"],
            "mode_bank_overrides": {"TAKER_SELL": {"buy": ["64"]}},
        }
        self.assertEqual(resolve_banks(user, "TAKER_SELL", "sell"), ["14"])

    def test_csv_and_list_are_both_accepted(self):
        # bank_codes приходять то списком, то рядком — залежно від шляху.
        self.assertEqual(base_banks({"bank_codes": "43,14"}, "buy"), ["43", "14"])
        self.assertEqual(base_banks({"bank_codes": ["43", "14"]}, "buy"), ["43", "14"])

    def test_has_override_reports_only_explicit_ones(self):
        user = {"bank_codes": ["43"], "mode_bank_overrides": {"TAKER_BUY": {"buy": ["64"]}}}
        self.assertTrue(has_override(user, "TAKER_BUY", "buy"))
        self.assertFalse(has_override(user, "TAKER_BUY", "sell"))
        self.assertFalse(has_override(user, "SPREAD", "buy"))

    def test_normalize_drops_unknown_modes_and_empty_lists(self):
        cleaned = normalize_overrides(
            {
                "TAKER_BUY": {"buy": ["43"], "sell": []},
                "НЕІСНУЮЧИЙ": {"buy": ["43"]},
                "SPREAD": {"buy": []},
            },
            SCANNER_MODES,
        )
        # Порожнє перевизначення не зберігаємо: воно нічим не відрізняється
        # від «беремо спільні», а зайвий ключ лише плутав би.
        self.assertEqual(cleaned, {"TAKER_BUY": {"buy": ["43"]}})


class TestHttpApi(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        from api.routers.control import router

        self._tmp = TemporaryDirectory()
        self.db = MerchantDB(db_path=Path(self._tmp.name) / "t.db")
        await self.db.start()
        await self.db.register_user(USER_ID, USER_ID)
        await self.db._db.execute(
            "UPDATE scanner_users SET bank_codes='43,14', buy_bank_codes='43' WHERE user_id=?",
            (USER_ID,),
        )
        await self.db._db.commit()

        self._patchers = [
            patch("bot.handlers.core._db", self.db),
            patch.object(settings_obj, "telegram_bot_token", BOT_TOKEN),
        ]
        for p in self._patchers:
            p.start()

        app = FastAPI()
        app.include_router(router)
        self.client = TestClient(app, raise_server_exceptions=False)

    async def asyncTearDown(self):
        for p in self._patchers:
            p.stop()
        await self.db.stop()
        self._tmp.cleanup()

    def _auth(self) -> dict:
        return {"Authorization": f"Bearer {auth_lib.issue_session(USER_ID)}"}

    def test_defaults_show_shared_lists_for_every_mode(self):
        body = self.client.get("/api/v1/user/bank-scopes", headers=self._auth()).json()

        self.assertEqual(body["base"]["buy"], ["43"])
        for mode in SCANNER_MODES:
            with self.subTest(mode=mode):
                self.assertEqual(body["resolved"][mode]["buy"]["banks"], ["43"])
                self.assertFalse(body["resolved"][mode]["buy"]["isOverride"])

    def test_setting_an_override_does_not_touch_other_modes(self):
        resp = self.client.post(
            "/api/v1/user/bank-scopes",
            json={"mode": "TAKER_BUY", "side": "buy", "banks": ["64"]},
            headers=self._auth(),
        )
        self.assertEqual(resp.status_code, 200, resp.text)

        body = self.client.get("/api/v1/user/bank-scopes", headers=self._auth()).json()
        self.assertEqual(body["resolved"]["TAKER_BUY"]["buy"]["banks"], ["64"])
        self.assertTrue(body["resolved"]["TAKER_BUY"]["buy"]["isOverride"])
        # Спред лишається на спільному списку.
        self.assertEqual(body["resolved"]["SPREAD"]["buy"]["banks"], ["43"])
        self.assertFalse(body["resolved"]["SPREAD"]["buy"]["isOverride"])

    def test_empty_list_removes_the_override(self):
        self.client.post(
            "/api/v1/user/bank-scopes",
            json={"mode": "TAKER_BUY", "side": "buy", "banks": ["64"]},
            headers=self._auth(),
        )
        self.client.post(
            "/api/v1/user/bank-scopes",
            json={"mode": "TAKER_BUY", "side": "buy", "banks": []},
            headers=self._auth(),
        )

        body = self.client.get("/api/v1/user/bank-scopes", headers=self._auth()).json()
        self.assertEqual(body["resolved"]["TAKER_BUY"]["buy"]["banks"], ["43"])
        self.assertEqual(body["overrides"], {})

    def test_unknown_mode_or_side_is_rejected(self):
        self.assertEqual(
            self.client.post(
                "/api/v1/user/bank-scopes",
                json={"mode": "НОНСЕНС", "side": "buy", "banks": ["43"]},
                headers=self._auth(),
            ).status_code,
            400,
        )
        self.assertEqual(
            self.client.post(
                "/api/v1/user/bank-scopes",
                json={"mode": "SPREAD", "side": "middle", "banks": ["43"]},
                headers=self._auth(),
            ).status_code,
            400,
        )

    def test_requires_session(self):
        self.assertEqual(self.client.get("/api/v1/user/bank-scopes").status_code, 401)


class TestTakerWizardKeepsSpreadIntact(unittest.IsolatedAsyncioTestCase):
    """
    Найважливіше твердження цього файлу: майстер Taker Buy більше не чіпає
    банки спред-режиму.
    """

    async def asyncSetUp(self):
        self._tmp = TemporaryDirectory()
        self.db = MerchantDB(db_path=Path(self._tmp.name) / "t.db")
        await self.db.start()
        await self.db.register_user(USER_ID, USER_ID)
        await self.db._db.execute(
            "UPDATE scanner_users SET bank_codes='43,14', buy_bank_codes='43,14' WHERE user_id=?",
            (USER_ID,),
        )
        await self.db._db.commit()

        self._patcher = patch("bot.handlers.filters._db", self.db)
        self._patcher.start()

    async def asyncTearDown(self):
        self._patcher.stop()
        await self.db.stop()
        self._tmp.cleanup()

    async def test_saving_taker_preset_leaves_spread_banks_alone(self):
        from bot.handlers.filters import _save_taker_buy_db

        await _save_taker_buy_db(USER_ID, {
            "amount": 100.0, "price_strategy": "any", "price_from": 0.0,
            "price_to": 0.0, "limit_min": 0.0, "limit_max": 0.0,
            "speed": "ANY", "banks": ["64"],
        })

        user = await self.db.get_user_by_id(USER_ID)
        self.assertEqual(resolve_banks(user, "TAKER_BUY", "buy"), ["64"])
        self.assertEqual(
            resolve_banks(user, "SPREAD", "buy"), ["43", "14"],
            "майстер тейкера перезаписав банки спред-режиму",
        )


if __name__ == "__main__":
    unittest.main()
