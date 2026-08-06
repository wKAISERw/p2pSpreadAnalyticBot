"""
Автентифікація дашборду.

Ключове, що тут перевіряється: telegram_id більше не береться на віру з
параметра запиту. До сесій будь-хто з API-ключем читав чужі картки,
фільтри й баланси, просто змінивши цифру в URL.
"""
from __future__ import annotations

import hashlib
import hmac
import time
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from fastapi import FastAPI
from fastapi.testclient import TestClient

from api import auth as auth_lib
from config import settings as settings_obj
from core.storage.merchant_db import MerchantDB

USER_ID = 5150
OTHER_ID = 6161
ADMIN_ID = 7171
BOT_TOKEN = "123456:TEST-TOKEN-FOR-SIGNING"


def _widget_payload(telegram_id: int, auth_date: int | None = None) -> dict:
    """Збирає payload віджета і підписує його так само, як це робить Telegram."""
    data = {
        "id": telegram_id,
        "first_name": "Test",
        "username": "tester",
        "auth_date": auth_date if auth_date is not None else int(time.time()),
    }
    check_string = "\n".join(f"{k}={data[k]}" for k in sorted(data))
    secret = hashlib.sha256(BOT_TOKEN.encode()).digest()
    data["hash"] = hmac.new(secret, check_string.encode(), hashlib.sha256).hexdigest()
    return data


class _AuthCase(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        from api.routers.auth import router as auth_router
        from api.routers.control import router as control_router

        self._tmp = TemporaryDirectory()
        self.db = MerchantDB(db_path=Path(self._tmp.name) / "t.db")
        await self.db.start()
        for uid in (USER_ID, OTHER_ID, ADMIN_ID):
            await self.db.register_user(uid, uid)

        # Патчимо саме інстанс Settings: "config.settings" — це модуль,
        # а поля живуть на об'єкті, який з нього імпортують.
        self._patchers = [
            patch("bot.handlers.core._db", self.db),
            patch.object(settings_obj, "telegram_bot_token", BOT_TOKEN),
            patch.object(settings_obj, "admin_id", ADMIN_ID),
        ]
        for p in self._patchers:
            p.start()

        app = FastAPI()
        app.include_router(auth_router)
        app.include_router(control_router)
        self.client = TestClient(app, raise_server_exceptions=False)

    async def asyncTearDown(self):
        for p in self._patchers:
            p.stop()
        await self.db.stop()
        self._tmp.cleanup()

    def _auth(self, telegram_id: int) -> dict:
        return {"Authorization": f"Bearer {auth_lib.issue_session(telegram_id)}"}


class TestSessionToken(_AuthCase):
    def test_round_trip(self):
        token = auth_lib.issue_session(USER_ID)
        self.assertEqual(auth_lib.read_session(token), USER_ID)

    def test_tampered_payload_is_rejected(self):
        token = auth_lib.issue_session(USER_ID)
        body, signature = token.split(".", 1)
        forged = auth_lib._b64(b'{"tid":999,"exp":9999999999}')
        self.assertIsNone(auth_lib.read_session(f"{forged}.{signature}"))

    def test_expired_token_is_rejected(self):
        self.assertIsNone(auth_lib.read_session(auth_lib.issue_session(USER_ID, ttl=-1)))

    def test_garbage_is_rejected(self):
        for junk in ("", "no-dot", "a.b", "...."):
            self.assertIsNone(auth_lib.read_session(junk))


class TestTelegramWidget(_AuthCase):
    def test_valid_payload_issues_session(self):
        resp = self.client.post("/api/v1/auth/telegram/widget", json=_widget_payload(USER_ID))

        self.assertEqual(resp.status_code, 200)
        body = resp.json()
        self.assertEqual(body["telegramId"], USER_ID)
        self.assertEqual(auth_lib.read_session(body["token"]), USER_ID)

    def test_forged_hash_is_rejected(self):
        payload = _widget_payload(USER_ID)
        payload["hash"] = "0" * 64

        self.assertEqual(
            self.client.post("/api/v1/auth/telegram/widget", json=payload).status_code, 401
        )

    def test_swapped_id_invalidates_signature(self):
        # Підпис прикриває саме id — підміна ламає його.
        payload = _widget_payload(USER_ID)
        payload["id"] = OTHER_ID

        self.assertEqual(
            self.client.post("/api/v1/auth/telegram/widget", json=payload).status_code, 401
        )

    def test_stale_auth_date_is_rejected(self):
        payload = _widget_payload(USER_ID, auth_date=int(time.time()) - 200_000)

        self.assertEqual(
            self.client.post("/api/v1/auth/telegram/widget", json=payload).status_code, 401
        )

    async def test_unknown_user_is_registered(self):
        new_id = 9090
        self.client.post("/api/v1/auth/telegram/widget", json=_widget_payload(new_id))

        self.assertIsNotNone(await self.db.get_user_by_id(new_id))


class TestLoginCode(_AuthCase):
    async def test_code_logs_in_once(self):
        code = await auth_lib.create_login_code(self.db, USER_ID)

        first = self.client.post("/api/v1/auth/telegram/code", json={"code": code})
        self.assertEqual(first.status_code, 200)
        self.assertEqual(first.json()["telegramId"], USER_ID)

        # Другий раз той самий код не має спрацювати.
        second = self.client.post("/api/v1/auth/telegram/code", json={"code": code})
        self.assertEqual(second.status_code, 401)

    def test_wrong_code_is_rejected(self):
        resp = self.client.post("/api/v1/auth/telegram/code", json={"code": "000000"})
        self.assertEqual(resp.status_code, 401)

    async def test_expired_code_is_rejected(self):
        code = await auth_lib.create_login_code(self.db, USER_ID)
        await self.db._db.execute(
            "UPDATE web_login_codes SET created_at = ? WHERE code = ?",
            (time.time() - auth_lib.LOGIN_CODE_TTL_SECONDS - 60, code),
        )
        await self.db._db.commit()

        resp = self.client.post("/api/v1/auth/telegram/code", json={"code": code})
        self.assertEqual(resp.status_code, 401)

    async def test_new_code_invalidates_previous(self):
        old = await auth_lib.create_login_code(self.db, USER_ID)
        await auth_lib.create_login_code(self.db, USER_ID)

        self.assertEqual(
            self.client.post("/api/v1/auth/telegram/code", json={"code": old}).status_code, 401
        )


class TestGoogleLinking(_AuthCase):
    def test_google_login_before_linking_is_404(self):
        resp = self.client.post("/api/v1/auth/google", json={"googleUid": "g-1"})
        self.assertEqual(resp.status_code, 404)

    def test_link_then_login(self):
        self.client.post(
            "/api/v1/auth/link/google",
            json={"googleUid": "g-1", "email": "a@b.c"},
            headers=self._auth(USER_ID),
        )

        resp = self.client.post("/api/v1/auth/google", json={"googleUid": "g-1"})
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.json()["telegramId"], USER_ID)

    def test_linking_requires_session(self):
        resp = self.client.post("/api/v1/auth/link/google", json={"googleUid": "g-1"})
        self.assertEqual(resp.status_code, 401)

    def test_google_cannot_be_linked_to_two_accounts(self):
        self.client.post(
            "/api/v1/auth/link/google", json={"googleUid": "g-1"}, headers=self._auth(USER_ID)
        )
        resp = self.client.post(
            "/api/v1/auth/link/google", json={"googleUid": "g-1"}, headers=self._auth(OTHER_ID)
        )
        self.assertEqual(resp.status_code, 409)

    def test_unlink_removes_login_path(self):
        self.client.post(
            "/api/v1/auth/link/google", json={"googleUid": "g-1"}, headers=self._auth(USER_ID)
        )
        self.client.post("/api/v1/auth/unlink/google", headers=self._auth(USER_ID))

        self.assertEqual(
            self.client.post("/api/v1/auth/google", json={"googleUid": "g-1"}).status_code, 404
        )

    def test_me_lists_identities(self):
        self.client.post(
            "/api/v1/auth/link/google",
            json={"googleUid": "g-1", "email": "a@b.c"},
            headers=self._auth(USER_ID),
        )

        body = self.client.get("/api/v1/auth/me", headers=self._auth(USER_ID)).json()
        self.assertEqual(body["telegramId"], USER_ID)
        self.assertEqual([i["provider"] for i in body["identities"]], ["google"])


class TestPersonalDataIsolation(_AuthCase):
    """Головне, заради чого затівались сесії."""

    async def asyncSetUp(self):
        await super().asyncSetUp()
        await self.db.add_card({
            "id": "victim-card", "owner_id": OTHER_ID, "bank_name": "monobank",
            "last_four": "9999", "label": "чужа", "balance": 50000.0,
        })

    def test_session_cannot_read_another_users_cards(self):
        resp = self.client.get(
            "/api/v1/cards", params={"telegram_id": OTHER_ID}, headers=self._auth(USER_ID)
        )
        self.assertEqual(resp.status_code, 403)

    def test_session_cannot_read_another_users_filters(self):
        resp = self.client.get(
            "/api/v1/user/filters", params={"telegram_id": OTHER_ID}, headers=self._auth(USER_ID)
        )
        self.assertEqual(resp.status_code, 403)

    def test_session_cannot_write_another_users_filters(self):
        resp = self.client.post(
            "/api/v1/user/filters",
            json={"telegramId": OTHER_ID, "minSpreadPct": 9.9},
            headers=self._auth(USER_ID),
        )
        self.assertEqual(resp.status_code, 403)

    def test_id_is_taken_from_session_when_param_omitted(self):
        body = self.client.get("/api/v1/user/filters", headers=self._auth(USER_ID)).json()
        self.assertEqual(body["userId"], USER_ID)

    def test_admin_may_read_other_users(self):
        resp = self.client.get(
            "/api/v1/cards", params={"telegram_id": OTHER_ID}, headers=self._auth(ADMIN_ID)
        )
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(len(resp.json()), 1)

    def test_without_session_and_without_id_request_is_rejected(self):
        # Лишається старий шлях по X-API-Key, але тоді id має бути явним.
        self.assertEqual(self.client.get("/api/v1/user/filters").status_code, 401)


class TestDisplaySettings(_AuthCase):
    def test_defaults_are_returned(self):
        body = self.client.get("/api/v1/user/display", headers=self._auth(USER_ID)).json()

        self.assertIn("showAiLogic", body)
        self.assertIn("alertCooldown", body)

    def test_partial_update_keeps_other_flags(self):
        # update_user_display_settings перезаписує всі колонки — без merge
        # вимкнення одного прапорця скидало б решту у дефолт.
        self.client.post(
            "/api/v1/user/display",
            json={"showAiLogic": False, "showFullTerms": False},
            headers=self._auth(USER_ID),
        )
        self.client.post(
            "/api/v1/user/display", json={"showBankDetails": False}, headers=self._auth(USER_ID)
        )

        body = self.client.get("/api/v1/user/display", headers=self._auth(USER_ID)).json()
        self.assertFalse(body["showAiLogic"], "перший прапорець не мав повернутись у True")
        self.assertFalse(body["showFullTerms"])
        self.assertFalse(body["showBankDetails"])

    def test_invalid_filter_mode_is_rejected(self):
        resp = self.client.post(
            "/api/v1/user/display", json={"filterFopTov": "nonsense"}, headers=self._auth(USER_ID)
        )
        self.assertEqual(resp.status_code, 400)

    def test_auto_cooldown_tiers_are_sorted(self):
        resp = self.client.post(
            "/api/v1/user/display/auto-cooldown",
            json={
                "windowSeconds": 5,
                "tiers": [{"threshold": 20, "delay": 1.0}, {"threshold": 10, "delay": 0.0}],
            },
            headers=self._auth(USER_ID),
        )

        self.assertEqual(resp.status_code, 200)
        thresholds = [t["threshold"] for t in resp.json()["tiers"]]
        self.assertEqual(thresholds, [10, 20], "сканер бере перший підхожий рівень зверху")

    def test_empty_tiers_are_rejected(self):
        resp = self.client.post(
            "/api/v1/user/display/auto-cooldown",
            json={"windowSeconds": 5, "tiers": []},
            headers=self._auth(USER_ID),
        )
        self.assertEqual(resp.status_code, 400)


class TestTakerFilters(_AuthCase):
    async def test_taker_sell_settings_are_written(self):
        resp = self.client.post(
            "/api/v1/user/filters",
            json={
                "scannerMode": "TAKER_SELL",
                "takerSellAmount": 500,
                "takerSellMinPrice": 41.5,
                "takerSellSpeed": "FAST",
                "takerSellPriceStrategy": "roi",
            },
            headers=self._auth(USER_ID),
        )

        self.assertEqual(resp.status_code, 200)
        user = await self.db.get_user_by_id(USER_ID)
        self.assertEqual(user["scanner_mode"], "TAKER_SELL")
        self.assertEqual(user["taker_sell_amount"], 500)
        self.assertEqual(user["taker_sell_speed"], "FAST")

    async def test_taker_buy_settings_are_written(self):
        self.client.post(
            "/api/v1/user/filters",
            json={
                "scannerMode": "TAKER_BUY",
                "takerBuyAmount": 300,
                "takerBuyMaxPrice": 40.9,
                "takerBuyPriceStrategy": "max",
                "buyBalanceMode": "AUTO_SCALE",
            },
            headers=self._auth(USER_ID),
        )

        user = await self.db.get_user_by_id(USER_ID)
        self.assertEqual(user["taker_buy_amount"], 300)
        self.assertEqual(user["buy_balance_mode"], "AUTO_SCALE")

    def test_unknown_speed_is_rejected(self):
        resp = self.client.post(
            "/api/v1/user/filters",
            json={"takerBuySpeed": "TURBO"},
            headers=self._auth(USER_ID),
        )
        self.assertEqual(resp.status_code, 400)

    def test_unknown_price_strategy_is_rejected(self):
        resp = self.client.post(
            "/api/v1/user/filters",
            json={"takerSellPriceStrategy": "vibes"},
            headers=self._auth(USER_ID),
        )
        self.assertEqual(resp.status_code, 400)


if __name__ == "__main__":
    unittest.main()
