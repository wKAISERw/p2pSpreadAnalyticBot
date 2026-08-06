"""
Ендпоінти керування: фільтри, ядро, картки, моніторинг.

Ці домени жили тільки в Telegram-меню — дашборд не міг ані змінити фільтри,
ані зупинити ядро, ані побачити протухлу сесію біржі.
"""
from __future__ import annotations

import time
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from fastapi import FastAPI
from fastapi.testclient import TestClient

from config.runtime import runtime_config
from core.storage.merchant_db import MerchantDB

USER_ID = 4242


class _ControlCase(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        from api.routers.control import router

        self._tmp = TemporaryDirectory()
        self.db = MerchantDB(db_path=Path(self._tmp.name) / "t.db")
        await self.db.start()
        await self.db.register_user(USER_ID, USER_ID)

        self._patcher = patch("bot.handlers.core._db", self.db)
        self._patcher.start()

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
        self._patcher.stop()
        await self.db.stop()
        self._tmp.cleanup()


class TestUserFilters(_ControlCase):
    def test_get_returns_full_filter_set(self):
        body = self.client.get("/api/v1/user/filters", params={"telegram_id": USER_ID}).json()

        self.assertEqual(body["userId"], USER_ID)
        self.assertIn("minSpread", body)
        self.assertIn("scannerMode", body)

    def test_unknown_user_is_404(self):
        resp = self.client.get("/api/v1/user/filters", params={"telegram_id": 1})
        self.assertEqual(resp.status_code, 404)

    async def test_partial_update_does_not_null_other_columns(self):
        # Головний ризик часткового апдейту: відсутній ключ затирає значення,
        # яке юзер виставляв у боті.
        await self.db._db.execute(
            "UPDATE scanner_users SET working_capital = 9999, min_spread_pct = 2.5 WHERE user_id = ?",
            (USER_ID,),
        )
        await self.db._db.commit()

        resp = self.client.post(
            "/api/v1/user/filters", json={"telegramId": USER_ID, "minSpreadPct": 1.25}
        )

        self.assertEqual(resp.status_code, 200)
        user = await self.db.get_user_by_id(USER_ID)
        self.assertEqual(user["min_spread"], 1.25)
        self.assertEqual(user["capital"], 9999, "капітал не мав змінитись")

    async def test_bank_codes_accept_list(self):
        self.client.post(
            "/api/v1/user/filters", json={"telegramId": USER_ID, "bankCodes": ["43", "14"]}
        )
        user = await self.db.get_user_by_id(USER_ID)
        self.assertIn("43", user["bank_codes"])
        self.assertIn("14", user["bank_codes"])

    def test_invalid_scanner_mode_is_rejected(self):
        resp = self.client.post(
            "/api/v1/user/filters", json={"telegramId": USER_ID, "scannerMode": "NONSENSE"}
        )
        self.assertEqual(resp.status_code, 400)

    def test_non_editable_column_is_not_written(self):
        resp = self.client.post(
            "/api/v1/user/filters", json={"telegramId": USER_ID, "telegramChatId": 777}
        )
        # Єдине передане поле не редаговане — оновлювати нічого.
        self.assertEqual(resp.status_code, 400)

    def test_update_of_missing_user_is_404(self):
        resp = self.client.post(
            "/api/v1/user/filters", json={"telegramId": 999999, "minSpreadPct": 1.0}
        )
        self.assertEqual(resp.status_code, 404)

    async def test_per_exchange_merchant_filter_overrides_global(self):
        self.client.post(
            "/api/v1/user/merchant-filters",
            json={"telegramId": USER_ID, "minOrders": 50, "minRate": 95.0},
        )
        self.client.post(
            "/api/v1/user/merchant-filters",
            json={"telegramId": USER_ID, "exchange": "Bybit", "minOrders": 300},
        )

        user = await self.db.get_user_by_id(USER_ID)
        self.assertEqual(user["merchant_filters"]["min_orders"], 50)
        self.assertEqual(user["exchange_merchant_filters"]["Bybit"]["min_orders"], 300)

    def test_merchant_filters_need_at_least_one_value(self):
        resp = self.client.post("/api/v1/user/merchant-filters", json={"telegramId": USER_ID})
        self.assertEqual(resp.status_code, 400)


class TestScannerControl(_ControlCase):
    def test_start_and_stop_write_runtime_config(self):
        self.client.post("/api/v1/scanner/start")
        # Саме цей ключ scanner.py перечитує на кожному циклі.
        self.assertEqual(runtime_config.get("is_scanner_active"), "true")

        self.client.post("/api/v1/scanner/stop")
        self.assertEqual(runtime_config.get("is_scanner_active"), "false")

    def test_state_reflects_runtime_config(self):
        self.client.post("/api/v1/scanner/start")
        body = self.client.get("/api/v1/scanner/state").json()
        self.assertTrue(body["isScannerActive"])

    def test_mute_and_unmute(self):
        from bot.handlers import core

        try:
            body = self.client.post("/api/v1/scanner/mute", json={"hours": 1}).json()
            self.assertTrue(body["isMuted"])
            self.assertGreater(self.client.get("/api/v1/scanner/state").json()["muteSecondsLeft"], 0)

            body = self.client.post("/api/v1/scanner/mute", json={"hours": 0}).json()
            self.assertFalse(body["isMuted"])
        finally:
            core._mute_until = 0.0

    def test_unknown_exchange_is_404(self):
        resp = self.client.post("/api/v1/exchanges/NoSuchExchange/enable")
        self.assertEqual(resp.status_code, 404)

    def test_disable_then_enable_round_trip(self):
        from core.engine.exchange_manager import exchange_manager

        try:
            self.client.post(
                "/api/v1/exchanges/Bybit/disable", json={"reason": "test", "cooldownHours": 0}
            )
            self.assertFalse(exchange_manager.is_enabled("Bybit"))

            self.client.post("/api/v1/exchanges/Bybit/enable")
            self.assertTrue(exchange_manager.is_enabled("Bybit"))
        finally:
            # Синглтон спільний для всього процесу — прибираємо за собою,
            # інакше наступні тести побачать вимкнену біржу.
            await_ = exchange_manager._states["Bybit"]
            await_.disabled = False
            await_.cooldown_until = 0.0
            await_.disabled_reason = ""


class TestCards(_ControlCase):
    async def asyncSetUp(self):
        await super().asyncSetUp()
        await self.db.add_card({
            "id": "card-1", "owner_id": USER_ID, "bank_name": "monobank",
            "last_four": "1234", "label": "основна", "balance": 12000.0,
        })

    def test_cards_include_limits_and_usage(self):
        body = self.client.get("/api/v1/cards", params={"telegram_id": USER_ID}).json()

        self.assertEqual(len(body), 1)
        card = body[0]
        self.assertEqual(card["lastFour"], "1234")
        self.assertIn("limits", card)
        self.assertIn("usedDaily", card)
        self.assertIn("usedMonthly", card)

    def test_other_users_cards_are_not_returned(self):
        body = self.client.get("/api/v1/cards", params={"telegram_id": 1}).json()
        self.assertEqual(body, [])

    async def test_transactions_are_newest_first(self):
        now = time.time()
        for i, ts in enumerate((now - 300, now - 100, now - 200)):
            await self.db._db.execute(
                "INSERT INTO card_transactions (id, card_id, amount, direction, type, source, timestamp)"
                " VALUES (?, 'card-1', ?, 'out', 'work', 'test', ?)",
                (f"tx-{i}", 100.0 + i, ts),
            )
        await self.db._db.commit()

        body = self.client.get("/api/v1/cards/card-1/transactions").json()

        self.assertEqual(len(body), 3)
        timestamps = [t["timestamp"] for t in body]
        self.assertEqual(timestamps, sorted(timestamps, reverse=True))


class TestMonitoring(_ControlCase):
    def test_sessions_listed_for_every_exchange(self):
        from core.engine.exchange_manager import ALL_EXCHANGES

        body = self.client.get("/api/v1/monitoring/sessions", params={"telegram_id": USER_ID}).json()

        self.assertEqual({s["exchange"] for s in body}, set(ALL_EXCHANGES))
        self.assertTrue(all(s["hasSession"] is False for s in body))

    async def test_stale_session_is_flagged(self):
        await self.db.save_auth_session(
            exchange="Bybit", headers_dict={"User-Agent": "x"}, cookies_dict={"a": "b"}, user_id=USER_ID
        )
        # Відсуваємо в минуле — свіжою вважається сесія молодша за 12 годин.
        await self.db._db.execute(
            "UPDATE auth_sessions SET updated_at = ? WHERE user_id = ? AND exchange = 'Bybit'",
            (time.time() - 30 * 3600, USER_ID),
        )
        await self.db._db.commit()

        body = self.client.get("/api/v1/monitoring/sessions", params={"telegram_id": USER_ID}).json()
        bybit = next(s for s in body if s["exchange"] == "Bybit")

        self.assertTrue(bybit["hasSession"])
        self.assertTrue(bybit["isStale"])

    def test_orders_reject_empty_status_list(self):
        resp = self.client.get("/api/v1/monitoring/orders", params={"statuses": " , "})
        self.assertEqual(resp.status_code, 400)

    def test_queues_endpoint_shape(self):
        body = self.client.get("/api/v1/monitoring/queues").json()
        self.assertIn("llmQueue", body)
        self.assertIn("cbStatus", body)


class TestBanksDirectory(_ControlCase):
    def test_banks_are_returned(self):
        body = self.client.get("/api/v1/banks").json()
        codes = {b["code"] for b in body}
        self.assertIn("43", codes)   # monobank
        self.assertIn("14", codes)   # privatbank


if __name__ == "__main__":
    unittest.main()
