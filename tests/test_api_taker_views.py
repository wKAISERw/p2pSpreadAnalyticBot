"""
Дашбордні зрізи тейкер-режиму: готовність, USDT по гаманцях, комісія переказу.

Три речі, які движок і бот уже вміли, а сайт показував як порожнечу:

  * `readiness` — чому алертів не буде, ще до першого циклу сканера;
  * `usdt_inventory` — «є на біржі» не дорівнює «можу продати зараз»;
  * комісія банку за переказ — при спреді 0.5–1% вона з'їдає весь профіт,
    і без неї ордер на сайті виглядав вигіднішим, ніж він є.

Тести фіксують форму відповіді (фронтенд типізує її вручну) і два місця,
де легко збрехати мовчки: невідоме подане як нуль, і перевірка одного
режиму там, де їх увімкнено кілька.
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
from config.runtime import runtime_config
from core.storage.merchant_db import MerchantDB

USER_ID = 5151
BOT_TOKEN = "123456:TEST-TOKEN-FOR-SIGNING"


class _Case(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        from api.routers.control import router

        self._tmp = TemporaryDirectory()
        self.db = MerchantDB(db_path=Path(self._tmp.name) / "t.db")
        await self.db.start()
        await self.db.register_user(USER_ID, USER_ID)

        self._patchers = [
            patch("bot.handlers.core._db", self.db),
            patch.object(settings_obj, "telegram_bot_token", BOT_TOKEN),
        ]
        for p in self._patchers:
            p.start()

        self._prev_db = runtime_config._db
        self._prev_cache = dict(runtime_config._cache)
        runtime_config._db = self.db
        runtime_config._cache = {}
        await runtime_config.init_table()

        app = FastAPI()
        app.include_router(router)
        self.client = TestClient(app, raise_server_exceptions=False)

    async def asyncTearDown(self):
        runtime_config._db = self._prev_db
        runtime_config._cache = self._prev_cache
        for p in self._patchers:
            p.stop()
        await self.db.stop()
        self._tmp.cleanup()

    def _auth(self) -> dict:
        return {"Authorization": f"Bearer {auth_lib.issue_session(USER_ID)}"}


class TestReadiness(_Case):
    async def test_non_taker_mode_returns_empty_not_error(self):
        # У спреді перевіряти нічого — але це не помилка й не мовчання.
        await self.db._db.execute(
            "UPDATE scanner_users SET scanner_mode='SPREAD', scanner_modes='SPREAD'"
            " WHERE user_id=?", (USER_ID,),
        )
        await self.db._db.commit()

        body = self.client.get("/api/v1/taker/readiness", headers=self._auth()).json()
        self.assertEqual(body["checks"], [])
        self.assertFalse(body["hasBlockers"])

    async def test_checks_cover_every_enabled_taker_mode(self):
        # Режимів може бути кілька одночасно. Читати одиничне `scanner_mode`
        # означало б перевірити лише один і промовчати про другий.
        await self.db._db.execute(
            "UPDATE scanner_users SET scanner_mode='TAKER_BUY',"
            " scanner_modes='TAKER_BUY,TAKER_SELL' WHERE user_id=?", (USER_ID,),
        )
        await self.db._db.commit()

        body = self.client.get("/api/v1/taker/readiness", headers=self._auth()).json()
        self.assertEqual(sorted(body["modes"]), ["TAKER_BUY", "TAKER_SELL"])

    async def test_explicit_mode_wins(self):
        body = self.client.get(
            "/api/v1/taker/readiness",
            params={"mode": "TAKER_SELL"},
            headers=self._auth(),
        ).json()
        self.assertEqual(body["modes"], ["TAKER_SELL"])

    async def test_every_check_carries_level_and_mode(self):
        # Без картки й без банків перевірка мусить щось сказати — інакше
        # порожній екран знову означав би «все гаразд».
        await self.db._db.execute(
            "UPDATE scanner_users SET scanner_mode='TAKER_BUY',"
            " scanner_modes='TAKER_BUY', buy_bank_codes='43',"
            " taker_buy_amount=700 WHERE user_id=?", (USER_ID,),
        )
        await self.db._db.commit()

        body = self.client.get("/api/v1/taker/readiness", headers=self._auth()).json()
        self.assertTrue(body["checks"], "жодної перевірки без карток — це мовчання")
        for check in body["checks"]:
            with self.subTest(text=check["text"]):
                self.assertIn(check["level"], ("blocker", "warning", "note"))
                self.assertEqual(check["mode"], "TAKER_BUY")
                self.assertTrue(check["text"])


class TestUsdtInventory(_Case):
    def test_no_keys_means_unknown_not_zero(self):
        # Нуль веде до висновку «треба переказувати», невідоме — ні до якого.
        body = self.client.get("/api/v1/inventory/usdt", headers=self._auth()).json()
        self.assertFalse(body["known"])
        self.assertEqual(body["exchanges"], [])

    def test_wallets_are_reported_separately(self):
        from core.engine.usdt_inventory import Wallets

        fake = {"Bybit": Wallets(funding=100.0, spot=250.0, earn=50.0, earn_known=True)}
        with patch("core.engine.usdt_inventory.usdt_by_exchange", return_value=fake):
            body = self.client.get("/api/v1/inventory/usdt", headers=self._auth()).json()

        self.assertTrue(body["known"])
        row = body["exchanges"][0]
        # Три різні відстані до угоди, а не одне число: саме їх злиття в
        # get_balance() і ховало «є, але не там, де треба».
        self.assertEqual(row["funding"], 100.0)
        self.assertEqual(row["spot"], 250.0)
        self.assertEqual(row["earn"], 50.0)
        self.assertEqual(row["total"], 400.0)
        self.assertEqual(body["totals"]["funding"], 100.0)


class TestTransferFeeView(unittest.TestCase):
    """Комісія рахується з профілю банку й не бреше в жоден бік."""

    def test_bank_without_fee_gives_none(self):
        from api.routers.control import _transfer_fee_view

        # Monobank по Україні — 0%, і вигадувати там комісію не можна.
        row = {"bankCodes": ["43"], "price": 41.0}
        self.assertIsNone(_transfer_fee_view(row, 20_000.0))

    def test_threshold_is_respected(self):
        from api.routers.control import _transfer_fee_view

        # А-Банк: до 100к безкоштовно, далі 2%. Раніше код брав 0.5%
        # беззастережно — і завищував комісію на малих сумах.
        row = {"bankCodes": ["48"], "price": 41.0}
        self.assertIsNone(_transfer_fee_view(row, 10_000.0))

    def test_effective_price_is_above_raw_price(self):
        from api.routers.control import _transfer_fee_view

        row = {"bankCodes": ["328"], "price": 41.0}   # Sense: 1% + 5 ₴ понад 20к
        fee = _transfer_fee_view(row, 50_000.0)
        if fee is not None:
            self.assertGreater(fee["effectivePrice"], 41.0)
            self.assertGreater(fee["amountUah"], 0)
            self.assertEqual(fee["onAmountUah"], 50_000.0)

    def test_missing_bank_or_price_is_not_a_crash(self):
        from api.routers.control import _transfer_fee_view

        self.assertIsNone(_transfer_fee_view({"bankCodes": [], "price": 41.0}, 1000.0))
        self.assertIsNone(_transfer_fee_view({"bankCodes": ["48"], "price": 0}, 1000.0))
        self.assertIsNone(_transfer_fee_view({"bankCodes": ["48"], "price": 41.0}, 0))


if __name__ == "__main__":
    unittest.main()
