"""
Кілька режимів сканера одночасно.

Досі scanner_mode був одним рядком: щоб ловити і купівлю, і продаж,
доводилось перемикатися туди-сюди, і половину часу друга сторона не
сканувалась узагалі. Тепер набір режимів лежить у scanner_modes, а
scanner_mode лишається «основним» — на нього падають старі рядки.
"""
from __future__ import annotations

import unittest
from decimal import Decimal
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import MagicMock, patch

from fastapi import FastAPI
from fastapi.testclient import TestClient

from api import auth as auth_lib
from config import settings as settings_obj
from core.engine.scanner_helpers import _user_modes
from core.storage.merchant_db import MerchantDB
from core.storage.user_repo import _parse_scanner_modes
from exchanges.base import Order

USER_ID = 7100
BOT_TOKEN = "123456:TEST-TOKEN-FOR-SIGNING"


def _order() -> Order:
    return Order(
        id="o1", price=Decimal("41"), available_amount=Decimal("100"),
        min_limit=Decimal("500"), max_limit=Decimal("10000"),
        merchant_id="m1", merchant_name="M", month_order_count=100,
        finish_rate_pct=99.0, exchange="Bybit", bank_codes=["43"],
    )


def _opportunity() -> dict:
    return {
        "buy_order": _order(), "sell_order": _order(),
        "actual_entry_uah": 1000.0, "net_spread_pct": 2.0,
        "buy_bank": "43", "sell_bank": "43",
        "buy_banks_fit": ["43"], "sell_banks_fit": ["43"],
    }


class TestParsing(unittest.TestCase):
    def test_empty_column_falls_back_to_single_mode(self):
        # Рядки, створені до появи scanner_modes, мають працювати як раніше.
        self.assertEqual(_parse_scanner_modes("", "TAKER_BUY"), ["TAKER_BUY"])
        self.assertEqual(_parse_scanner_modes(None, "MAKER_SELL"), ["MAKER_SELL"])

    def test_order_is_canonical_regardless_of_input(self):
        # "TAKER_BUY,SPREAD" і "SPREAD,TAKER_BUY" — той самий набір.
        self.assertEqual(
            _parse_scanner_modes("TAKER_BUY,SPREAD", "SPREAD"),
            _parse_scanner_modes("SPREAD,TAKER_BUY", "SPREAD"),
        )

    def test_garbage_is_dropped_but_never_leaves_user_without_modes(self):
        self.assertEqual(_parse_scanner_modes("NONSENSE", "SPREAD"), ["SPREAD"])
        self.assertEqual(_parse_scanner_modes("", ""), ["SPREAD"])

    def test_duplicates_collapse(self):
        self.assertEqual(
            _parse_scanner_modes("SPREAD,SPREAD,TAKER_BUY", "SPREAD"),
            ["SPREAD", "TAKER_BUY"],
        )


class TestPipelineSelectsByModeSet(unittest.TestCase):
    """Конвеєр має дивитись на набір, а не на одне поле."""

    def test_user_modes_prefers_the_set(self):
        user = {"scanner_mode": "SPREAD", "scanner_modes": ["TAKER_BUY", "TAKER_SELL"]}
        self.assertEqual(_user_modes(user), ["TAKER_BUY", "TAKER_SELL"])

    def test_user_modes_falls_back_for_hand_built_dicts(self):
        # Такі словники збирають тести й HTTP-шар.
        self.assertEqual(_user_modes({"scanner_mode": "TAKER_SELL"}), ["TAKER_SELL"])
        self.assertEqual(_user_modes({}), ["SPREAD"])

    def test_spread_alerts_reach_a_user_who_also_hunts_taker(self):
        from core.engine.alert_dispatcher import AlertDispatcher

        dispatcher = AlertDispatcher(db=MagicMock(), notifier=MagicMock())
        user = {
            "user_id": USER_ID, "capital": 5000.0, "min_spread": 1.0,
            "bank_codes": ["43"], "buy_bank_codes": ["43"], "sell_bank_codes": ["43"],
            "merchant_filters": {},
            # Основний режим — тейкер, але спред теж увімкнений.
            "scanner_mode": "TAKER_BUY",
            "scanner_modes": ["SPREAD", "TAKER_BUY"],
        }

        ok, reason, _ = dispatcher._user_wants(user, _opportunity())
        self.assertTrue(ok, f"спред відкинуто: {reason}")

    def test_spread_alerts_skip_a_user_without_spread(self):
        from core.engine.alert_dispatcher import AlertDispatcher

        dispatcher = AlertDispatcher(db=MagicMock(), notifier=MagicMock())
        user = {
            "user_id": USER_ID, "capital": 5000.0, "min_spread": 1.0,
            "bank_codes": ["43"], "merchant_filters": {},
            "scanner_mode": "TAKER_BUY", "scanner_modes": ["TAKER_BUY", "TAKER_SELL"],
        }

        ok, reason, _ = dispatcher._user_wants(user, _opportunity())
        self.assertFalse(ok)
        self.assertIn("modes=", reason)


class TestTakerPathCoversEveryMode(unittest.IsolatedAsyncioTestCase):
    """
    Тейкер-шлях має обійти всі активні тейкер-режими користувача.

    Раніше він фільтрував за одним scanner_mode, тож із двох увімкнених
    сторін сканувалась рівно одна.
    """

    async def test_both_sides_are_scanned_for_one_user(self):
        from core.engine import scanner_helpers

        seen_modes: list[str] = []

        from core.engine.taker_scanner import TakerScanResult

        class FakeScanner:
            db = None

            async def scan(self, user, buy_grouped, sell_grouped):
                seen_modes.append(user["scanner_mode"])
                return TakerScanResult()

        user = {
            "user_id": USER_ID, "chat_id": USER_ID,
            "scanner_mode": "TAKER_BUY",
            "scanner_modes": ["TAKER_BUY", "TAKER_SELL"],
        }

        with patch.object(scanner_helpers, "is_muted", return_value=False):
            await scanner_helpers.process_taker_path(
                [user], {}, {}, FakeScanner(), MagicMock(), MagicMock(),
            )

        self.assertEqual(sorted(seen_modes), ["TAKER_BUY", "TAKER_SELL"])

    async def test_dedup_key_separates_modes(self):
        # Той самий ордер може підійти під різні пресети; «вже надіслано в
        # BUY» не має глушити SELL.
        from core.engine import scanner_helpers

        marked: list[str] = []
        dedup = MagicMock()
        dedup.seen.return_value = False
        dedup.mark.side_effect = marked.append

        from core.engine.taker_scanner import TakerScanResult

        class FakeScanner:
            db = None

            async def scan(self, user, buy_grouped, sell_grouped):
                return TakerScanResult(orders=[_order()])

        notifier = MagicMock()
        notifier.send_taker_to_user = MagicMock(return_value=_noop())
        notifier._db.save_proposal = MagicMock(return_value=_noop())

        user = {
            "user_id": USER_ID, "chat_id": USER_ID,
            "scanner_mode": "TAKER_BUY",
            "scanner_modes": ["TAKER_BUY", "TAKER_SELL"],
        }

        with patch.object(scanner_helpers, "is_muted", return_value=False), \
             patch.object(scanner_helpers, "spawn", lambda coro, *a, **kw: coro.close()):
            await scanner_helpers.process_taker_path(
                [user], {}, {}, FakeScanner(), dedup, notifier,
            )

        self.assertEqual(len(marked), 2, "обидва режими мали позначити ордер")
        self.assertNotEqual(marked[0], marked[1], "ключі дедупу однакові для різних режимів")


async def _noop():
    return None


class TestHttpApi(unittest.IsolatedAsyncioTestCase):
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

    async def test_writing_a_set_updates_both_columns(self):
        resp = self.client.post(
            "/api/v1/user/filters",
            json={"scannerModes": ["TAKER_SELL", "SPREAD"]},
            headers=self._auth(),
        )
        self.assertEqual(resp.status_code, 200, resp.text)

        user = await self.db.get_user_by_id(USER_ID)
        self.assertEqual(user["scanner_modes"], ["SPREAD", "TAKER_SELL"])
        # Основний режим має лишатись усередині набору, інакше меню бота
        # показувало б режим, якого вже немає.
        self.assertIn(user["scanner_mode"], user["scanner_modes"])

    def test_unknown_mode_is_rejected(self):
        resp = self.client.post(
            "/api/v1/user/filters",
            json={"scannerModes": ["SPREAD", "NONSENSE"]},
            headers=self._auth(),
        )
        self.assertEqual(resp.status_code, 400)

    def test_empty_set_is_rejected(self):
        resp = self.client.post(
            "/api/v1/user/filters",
            json={"scannerModes": []},
            headers=self._auth(),
        )
        self.assertEqual(resp.status_code, 400)

    async def test_single_mode_still_works(self):
        # Старий шлях — запис одного scanner_mode — має лишатись робочим.
        resp = self.client.post(
            "/api/v1/user/filters",
            json={"scannerMode": "MAKER_BUY"},
            headers=self._auth(),
        )
        self.assertEqual(resp.status_code, 200)

        user = await self.db.get_user_by_id(USER_ID)
        self.assertEqual(user["scanner_mode"], "MAKER_BUY")
        self.assertEqual(user["scanner_modes"], ["MAKER_BUY"])

    def test_modes_are_visible_in_both_filter_groups(self):
        self.client.post(
            "/api/v1/user/filters",
            json={"scannerModes": ["SPREAD", "TAKER_BUY"]},
            headers=self._auth(),
        )

        for path in ("/api/v1/user/filters/core", "/api/v1/user/filters/taker"):
            with self.subTest(path=path):
                body = self.client.get(path, headers=self._auth()).json()
                self.assertEqual(body["scannerModes"], ["SPREAD", "TAKER_BUY"])


if __name__ == "__main__":
    unittest.main()
