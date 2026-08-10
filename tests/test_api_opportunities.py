"""
Спред-видача на сайті мусить збігатися з тим, що приходить у Telegram.

`GET /opportunities` довго віддавав `state.opportunities` — глобальний
список того, що знайшов сканер, без жодного персонального фільтра. Через
це на сайті було видно зв'язки, яких цей користувач у чаті не отримав би
ніколи: не проходили ні за капіталом, ні за спредом, ні за банками. І
навпаки — «на сайті густо, у чаті тихо» не мало жодного пояснення.

Рішення ухвалює той самий `AlertDispatcher._user_wants`, що й для алертів.
Ці тести фіксують саме це: не «фільтр працює», а «фільтр той самий».
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
from core.storage.merchant_db import MerchantDB
from state import state

USER_ID = 8080
BOT_TOKEN = "123456:TEST-TOKEN-FOR-SIGNING"


class _Order:
    """Мінімум полів, який читають _user_wants і adapt_alert_for_user."""

    def __init__(self, price: float, bank: str = "43", exchange: str = "Bybit",
                 min_limit: float = 500.0):
        self.id = f"o-{price}"
        self.price = price
        self.min_limit = min_limit
        self.max_limit = 50_000.0
        self.merchant_id = "m1"
        self.merchant_name = "Merchant"
        self.exchange = exchange
        self.bank_codes = [bank]
        self.risk_flag = "OK"
        self.month_order_count = 500
        self.finish_rate_pct = 99.0
        self.is_new_user_subsidy = False


def _opp(spread: float, entry: float, bank: str = "monobank",
         min_limit: float = 500.0) -> dict:
    return {
        "net_spread_pct": spread,
        "actual_entry_uah": entry,
        "buy_order": _Order(41.0, min_limit=min_limit),
        "sell_order": _Order(42.0, min_limit=min_limit),
        "buy_banks_fit": [bank],
        "sell_banks_fit": [bank],
        "buy_bank": bank,
        "sell_bank": bank,
    }


class _Case(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        from api.routers.dashboard import router

        self._tmp = TemporaryDirectory()
        self.db = MerchantDB(db_path=Path(self._tmp.name) / "t.db")
        await self.db.start()
        await self.db.register_user(USER_ID, USER_ID)

        self._patchers = [
            patch("bot.handlers.core._db", self.db),
            patch("api.routers.dashboard._is_admin", lambda uid: False),
            patch.object(settings_obj, "telegram_bot_token", BOT_TOKEN),
        ]
        for p in self._patchers:
            p.start()

        app = FastAPI()
        app.include_router(router)
        self.client = TestClient(app, raise_server_exceptions=False)

        self._prev_opps = state.opportunities
        self._prev_raw = state.opportunities_raw

    async def asyncTearDown(self):
        state.opportunities = self._prev_opps
        state.opportunities_raw = self._prev_raw
        for p in self._patchers:
            p.stop()
        await self.db.stop()
        self._tmp.cleanup()

    def _auth(self) -> dict:
        return {"Authorization": f"Bearer {auth_lib.issue_session(USER_ID)}"}

    def _put(self, pairs: list[tuple[str, dict]]):
        """Кладе в стан пари (id, сирий opp) разом із їхнім фронт-виглядом."""
        state.opportunities = [{"id": oid, "netSpreadPct": o["net_spread_pct"]} for oid, o in pairs]
        state.opportunities_raw = {oid: o for oid, o in pairs}

    async def _setup_user(self, **columns):
        sets = ", ".join(f"{k}=?" for k in columns)
        await self.db._db.execute(
            f"UPDATE scanner_users SET {sets} WHERE user_id=?",
            (*columns.values(), USER_ID),
        )
        await self.db._db.commit()


class TestPersonalFiltering(_Case):
    async def test_low_spread_is_hidden(self):
        await self._setup_user(
            scanner_mode="SPREAD", scanner_modes="SPREAD",
            min_spread_pct=2.0, working_capital=50_000,
            bank_codes="43", buy_bank_codes="43", sell_bank_codes="43",
        )
        self._put([("keep", _opp(spread=5.0, entry=10_000)),
                   ("drop", _opp(spread=0.4, entry=10_000))])

        body = self.client.get("/api/v1/opportunities", headers=self._auth()).json()
        self.assertEqual([o["id"] for o in body], ["keep"])

    async def test_entry_above_capital_is_hidden(self):
        await self._setup_user(
            scanner_mode="SPREAD", scanner_modes="SPREAD",
            min_spread_pct=0.5, working_capital=5_000, capital_mode="manual",
            bank_codes="43", buy_bank_codes="43", sell_bank_codes="43",
        )
        # Сума понад капітал сама по собі зв'язку не відкидає: движок
        # спершу пробує зайти меншим обсягом. Відмова настає лише тоді,
        # коли навіть мінімалка мерчанта більша за наявні гроші — саме це
        # й перевіряємо, інакше тест ловив би не ту поведінку.
        self._put([("small", _opp(spread=3.0, entry=3_000)),
                   ("huge", _opp(spread=3.0, entry=90_000, min_limit=20_000))])

        body = self.client.get("/api/v1/opportunities", headers=self._auth()).json()
        ids = [o["id"] for o in body]
        self.assertIn("small", ids)
        self.assertNotIn("huge", ids)

    async def test_foreign_bank_is_hidden(self):
        await self._setup_user(
            scanner_mode="SPREAD", scanner_modes="SPREAD",
            min_spread_pct=0.5, working_capital=50_000,
            bank_codes="43", buy_bank_codes="43", sell_bank_codes="43",
        )
        self._put([("mine", _opp(spread=3.0, entry=10_000, bank="monobank")),
                   ("alien", _opp(spread=3.0, entry=10_000, bank="oschadbank"))])

        body = self.client.get("/api/v1/opportunities", headers=self._auth()).json()
        self.assertEqual([o["id"] for o in body], ["mine"])

    async def test_non_spread_mode_shows_nothing(self):
        # Спред-алерти йдуть лише тим, у кого SPREAD серед активних режимів;
        # сайт має поводитись так само, інакше «бачу, але не приходить».
        await self._setup_user(
            scanner_mode="TAKER_BUY", scanner_modes="TAKER_BUY",
            min_spread_pct=0.5, working_capital=50_000,
        )
        self._put([("any", _opp(spread=9.0, entry=1_000))])

        body = self.client.get("/api/v1/opportunities", headers=self._auth()).json()
        self.assertEqual(body, [])


class TestFallbacks(_Case):
    async def test_without_session_list_stays_raw(self):
        # Вітрина на лендінгу не має падати й не має вимагати входу.
        self._put([("a", _opp(spread=0.1, entry=999_999))])
        body = self.client.get("/api/v1/opportunities").json()
        self.assertEqual([o["id"] for o in body], ["a"])

    async def test_personal_false_disables_filtering(self):
        await self._setup_user(
            scanner_mode="SPREAD", scanner_modes="SPREAD", min_spread_pct=9.0,
        )
        self._put([("a", _opp(spread=0.1, entry=1_000))])

        body = self.client.get(
            "/api/v1/opportunities",
            params={"personal": "false"},
            headers=self._auth(),
        ).json()
        self.assertEqual([o["id"] for o in body], ["a"])

    async def test_stale_opportunity_without_raw_is_kept(self):
        # Сирий opp живе один цикл. Ховати зв'язку лише тому, що її не
        # встигли перерахувати, — гірше за зайвий рядок на екрані.
        await self._setup_user(scanner_mode="SPREAD", scanner_modes="SPREAD")
        state.opportunities = [{"id": "stale"}]
        state.opportunities_raw = {}

        body = self.client.get("/api/v1/opportunities", headers=self._auth()).json()
        self.assertEqual([o["id"] for o in body], ["stale"])

    async def test_show_rejected_keeps_reason(self):
        await self._setup_user(
            scanner_mode="SPREAD", scanner_modes="SPREAD",
            min_spread_pct=5.0, working_capital=50_000,
            bank_codes="43", buy_bank_codes="43", sell_bank_codes="43",
        )
        self._put([("weak", _opp(spread=0.4, entry=10_000))])

        body = self.client.get(
            "/api/v1/opportunities",
            params={"show_rejected": "true"},
            headers=self._auth(),
        ).json()
        self.assertEqual(len(body), 1)
        self.assertTrue(body[0].get("rejectedReason"))


if __name__ == "__main__":
    unittest.main()
