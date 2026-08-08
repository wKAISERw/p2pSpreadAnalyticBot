"""
Персональні розділи: снайпер, фічі, ліміти, картковий модуль, звіти,
адміністрування юзерів.
"""
from __future__ import annotations

import json
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from fastapi import FastAPI
from fastapi.testclient import TestClient

from api import auth as auth_lib
from config import settings as settings_obj
from core.storage.merchant_db import MerchantDB

USER_ID = 8100
OTHER_ID = 8200
ADMIN_ID = 8300
BOT_TOKEN = "123456:TEST-TOKEN-FOR-SIGNING"


class _PersonalCase(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        from api.routers.control import router as control_router
        from api.routers.personal import router as personal_router

        self._tmp = TemporaryDirectory()
        self.db = MerchantDB(db_path=Path(self._tmp.name) / "t.db")
        await self.db.start()
        for uid in (USER_ID, OTHER_ID, ADMIN_ID):
            await self.db.register_user(uid, uid)

        self._patchers = [
            patch("bot.handlers.core._db", self.db),
            patch.object(settings_obj, "telegram_bot_token", BOT_TOKEN),
            patch.object(settings_obj, "admin_id", ADMIN_ID),
        ]
        for p in self._patchers:
            p.start()

        app = FastAPI()
        app.include_router(personal_router)
        app.include_router(control_router)
        self.client = TestClient(app, raise_server_exceptions=False)

    async def asyncTearDown(self):
        for p in self._patchers:
            p.stop()
        await self.db.stop()
        self._tmp.cleanup()

    def _auth(self, telegram_id: int) -> dict:
        return {"Authorization": f"Bearer {auth_lib.issue_session(telegram_id)}"}


class TestMerchantFilters(_PersonalCase):
    async def test_all_thresholds_are_stored(self):
        resp = self.client.post(
            "/api/v1/user/merchant-filters",
            json={
                "minOrders": 50,
                "minRate": 90.0,
                "verifiedFilter": "verified",
                "minAccountAgeDays": 30,
                "minPositiveRate": 95.0,
                "maxOfflineMins": 15,
                "blacklistMode": "hide",
            },
            headers=self._auth(USER_ID),
        )

        self.assertEqual(resp.status_code, 200)
        user = await self.db.get_user_by_id(USER_ID)
        mf = user["merchant_filters"]
        # Саме ці ключі читають alert_dispatcher і taker_scanner.
        self.assertEqual(mf["min_orders"], 50)
        self.assertEqual(mf["verified_filter"], "verified")
        self.assertEqual(mf["min_account_age_days"], 30)
        self.assertEqual(mf["min_positive_rate"], 95.0)
        self.assertEqual(mf["max_offline_mins"], 15)
        self.assertEqual(mf["blacklist_mode"], "hide")

    async def test_partial_update_keeps_previous_thresholds(self):
        self.client.post(
            "/api/v1/user/merchant-filters",
            json={"minOrders": 50, "maxOfflineMins": 15},
            headers=self._auth(USER_ID),
        )
        self.client.post(
            "/api/v1/user/merchant-filters",
            json={"minRate": 95.0},
            headers=self._auth(USER_ID),
        )

        mf = (await self.db.get_user_by_id(USER_ID))["merchant_filters"]
        self.assertEqual(mf["min_orders"], 50, "поріг угод не мав зникнути")
        self.assertEqual(mf["max_offline_mins"], 15)
        self.assertEqual(mf["min_rate"], 95.0)

    def test_invalid_verified_filter_is_rejected(self):
        resp = self.client.post(
            "/api/v1/user/merchant-filters",
            json={"verifiedFilter": "maybe"},
            headers=self._auth(USER_ID),
        )
        self.assertEqual(resp.status_code, 400)

    def test_invalid_blacklist_mode_is_rejected(self):
        resp = self.client.post(
            "/api/v1/user/merchant-filters",
            json={"blacklistMode": "burn"},
            headers=self._auth(USER_ID),
        )
        self.assertEqual(resp.status_code, 400)


class TestSniperRules(_PersonalCase):
    async def test_rules_round_trip(self):
        resp = self.client.post(
            "/api/v1/user/sniper",
            json={"rules": [
                {"exchange": "Bybit", "direction": "buy", "minSpread": 1.5, "minVolume": 20000},
            ]},
            headers=self._auth(USER_ID),
        )
        self.assertEqual(resp.status_code, 200)

        user = await self.db.get_user_by_id(USER_ID)
        rule = user["sniper_rules"][0]
        # Движок читає snake_case і порівнює direction у верхньому регістрі.
        self.assertEqual(rule["direction"], "BUY")
        self.assertEqual(rule["min_spread"], 1.5)
        self.assertEqual(rule["min_volume"], 20000)

        body = self.client.get("/api/v1/user/sniper", headers=self._auth(USER_ID)).json()
        self.assertEqual(body[0]["minSpread"], 1.5)

    def test_rule_without_any_threshold_is_rejected(self):
        # Інакше правило пробивало б беззвучний режим на кожному алерті.
        resp = self.client.post(
            "/api/v1/user/sniper",
            json={"rules": [{"exchange": "Bybit", "direction": "BUY"}]},
            headers=self._auth(USER_ID),
        )
        self.assertEqual(resp.status_code, 400)

    def test_bad_direction_is_rejected(self):
        resp = self.client.post(
            "/api/v1/user/sniper",
            json={"rules": [{"exchange": "Bybit", "direction": "SIDEWAYS", "minSpread": 1}]},
            headers=self._auth(USER_ID),
        )
        self.assertEqual(resp.status_code, 400)

    async def test_empty_list_clears_rules(self):
        self.client.post(
            "/api/v1/user/sniper",
            json={"rules": [{"exchange": "OKX", "direction": "SELL", "minSpread": 2}]},
            headers=self._auth(USER_ID),
        )
        self.client.post("/api/v1/user/sniper", json={"rules": []}, headers=self._auth(USER_ID))

        self.assertEqual((await self.db.get_user_by_id(USER_ID))["sniper_rules"], [])


class TestFeatures(_PersonalCase):
    def test_catalog_lists_known_features(self):
        body = self.client.get("/api/v1/user/features", headers=self._auth(USER_ID)).json()

        keys = {f["key"] for group in body for f in group["features"]}
        self.assertIn("hybrid_routes", keys)
        self.assertIn("asymmetric_spread", keys)

    def test_toggle_flips_state(self):
        first = self.client.post(
            "/api/v1/user/features/toggle",
            json={"key": "asymmetric_spread"},
            headers=self._auth(USER_ID),
        ).json()
        second = self.client.post(
            "/api/v1/user/features/toggle",
            json={"key": "asymmetric_spread"},
            headers=self._auth(USER_ID),
        ).json()

        self.assertNotEqual(first["enabled"], second["enabled"])

    def test_unknown_feature_is_404(self):
        resp = self.client.post(
            "/api/v1/user/features/toggle",
            json={"key": "teleportation"},
            headers=self._auth(USER_ID),
        )
        self.assertEqual(resp.status_code, 404)


class TestCardDisplay(_PersonalCase):
    def test_defaults_when_never_configured(self):
        body = self.client.get("/api/v1/user/card-display", headers=self._auth(USER_ID)).json()

        self.assertEqual(body["cardModuleMode"], "off")
        self.assertEqual(body["cardOutputMode"], "inline")
        self.assertEqual(body["coldCardLimit"], 2000.0)

    def test_partial_update_keeps_other_fields(self):
        # update_user_card_settings робить UPSERT усього рядка — без merge
        # решта полів поверталась би у дефолти.
        self.client.post(
            "/api/v1/user/card-display",
            json={"cardOutputMode": "reply", "coldCardLimit": 10000},
            headers=self._auth(USER_ID),
        )
        self.client.post(
            "/api/v1/user/card-display",
            json={"cardDetailLevel": "compact"},
            headers=self._auth(USER_ID),
        )

        body = self.client.get("/api/v1/user/card-display", headers=self._auth(USER_ID)).json()
        self.assertEqual(body["cardOutputMode"], "reply")
        self.assertEqual(body["coldCardLimit"], 10000)
        self.assertEqual(body["cardDetailLevel"], "compact")

    def test_invalid_output_mode_is_rejected(self):
        resp = self.client.post(
            "/api/v1/user/card-display",
            json={"cardOutputMode": "carrier-pigeon"},
            headers=self._auth(USER_ID),
        )
        self.assertEqual(resp.status_code, 400)


class TestLimits(_PersonalCase):
    async def asyncSetUp(self):
        await super().asyncSetUp()
        await self.db.add_card({
            "id": "c-mine", "owner_id": USER_ID, "bank_name": "monobank",
            "last_four": "1111", "label": "моя", "balance": 5000.0,
        })
        await self.db.add_card({
            "id": "c-theirs", "owner_id": OTHER_ID, "bank_name": "monobank",
            "last_four": "2222", "label": "чужа", "balance": 5000.0,
        })

    async def test_bank_limits_round_trip(self):
        self.client.post(
            "/api/v1/user/bank-limits",
            json={"bankName": "monobank", "limits": {"daily_out_max": 100000, "max_tx_per_day": 30}},
            headers=self._auth(USER_ID),
        )

        body = self.client.get("/api/v1/user/bank-limits", headers=self._auth(USER_ID)).json()
        self.assertEqual(len(body), 1)
        self.assertEqual(body[0]["dailyOutMax"], 100000)
        self.assertEqual(body[0]["maxTxPerDay"], 30)

    def test_unknown_limit_field_is_rejected(self):
        # Репозиторій на невідоме поле просто робить return — мовчазна
        # відмова виглядала б як успіх.
        resp = self.client.post(
            "/api/v1/user/bank-limits",
            json={"bankName": "monobank", "limits": {"daily_vibes": 1}},
            headers=self._auth(USER_ID),
        )
        self.assertEqual(resp.status_code, 400)

    async def test_card_override_is_stored(self):
        resp = self.client.post(
            "/api/v1/cards/c-mine/limits",
            json={"limits": {"daily_out_max": 7777}},
            headers=self._auth(USER_ID),
        )

        self.assertEqual(resp.status_code, 200)
        async with self.db._db.execute(
            "SELECT limits_override_json, is_custom_limits FROM cards WHERE id = 'c-mine'"
        ) as cur:
            row = await cur.fetchone()
        self.assertEqual(json.loads(row["limits_override_json"])["daily_out_max"], 7777)
        self.assertEqual(row["is_custom_limits"], 1)

    def test_cannot_touch_someone_elses_card(self):
        resp = self.client.post(
            "/api/v1/cards/c-theirs/limits",
            json={"limits": {"daily_out_max": 1}},
            headers=self._auth(USER_ID),
        )
        self.assertEqual(resp.status_code, 403)

    def test_missing_card_is_404(self):
        resp = self.client.post(
            "/api/v1/cards/nope/limits",
            json={"limits": {"daily_out_max": 1}},
            headers=self._auth(USER_ID),
        )
        self.assertEqual(resp.status_code, 404)

    async def test_custom_limits_toggle(self):
        self.client.post(
            "/api/v1/cards/c-mine/custom-limits",
            json={"enabled": False},
            headers=self._auth(USER_ID),
        )
        async with self.db._db.execute(
            "SELECT is_custom_limits FROM cards WHERE id = 'c-mine'"
        ) as cur:
            row = await cur.fetchone()
        self.assertEqual(row["is_custom_limits"], 0)


class TestMonoTracker(_PersonalCase):
    async def asyncSetUp(self):
        await super().asyncSetUp()
        await self.db.add_card({
            "id": "c-mono", "owner_id": USER_ID, "bank_name": "monobank",
            "last_four": "3333", "label": "моно", "balance": 1000.0,
        })

    def test_defaults_and_no_secrets_leak(self):
        body = self.client.get(
            "/api/v1/cards/c-mono/mono-tracker", headers=self._auth(USER_ID)
        ).json()

        self.assertIn("enabled", body)
        self.assertIn("fields", body)
        # Токен і секрет вебхука назовні не віддаються.
        self.assertNotIn("xTokenEncrypted", body)
        self.assertNotIn("webhookSecret", body)

    def test_mode_and_fields_are_saved(self):
        resp = self.client.post(
            "/api/v1/cards/c-mono/mono-tracker",
            json={"mode": "ALL", "fields": {"comment": False, "balance": True}},
            headers=self._auth(USER_ID),
        )
        self.assertEqual(resp.status_code, 200)

        body = self.client.get(
            "/api/v1/cards/c-mono/mono-tracker", headers=self._auth(USER_ID)
        ).json()
        self.assertEqual(body["mode"], "ALL")
        self.assertFalse(body["fields"]["comment"])

    def test_invalid_mode_is_rejected(self):
        resp = self.client.post(
            "/api/v1/cards/c-mono/mono-tracker",
            json={"mode": "SOMETIMES"},
            headers=self._auth(USER_ID),
        )
        self.assertEqual(resp.status_code, 400)

    def test_unknown_field_is_rejected(self):
        resp = self.client.post(
            "/api/v1/cards/c-mono/mono-tracker",
            json={"fields": {"horoscope": True}},
            headers=self._auth(USER_ID),
        )
        self.assertEqual(resp.status_code, 400)


class TestCardsReport(_PersonalCase):
    async def asyncSetUp(self):
        await super().asyncSetUp()
        await self.db.add_card({
            "id": "c-rep", "owner_id": USER_ID, "bank_name": "monobank",
            "last_four": "4444", "label": "звіт", "balance": 3000.0,
        })

    def test_report_covers_own_cards_only(self):
        body = self.client.get("/api/v1/cards/report", headers=self._auth(USER_ID)).json()
        self.assertEqual([c["cardId"] for c in body], ["c-rep"])

        other = self.client.get("/api/v1/cards/report", headers=self._auth(OTHER_ID)).json()
        self.assertEqual(other, [])


class TestAdminUsers(_PersonalCase):
    def test_non_admin_is_forbidden(self):
        self.assertEqual(
            self.client.get("/api/v1/admin/users", headers=self._auth(USER_ID)).status_code, 403
        )

    def test_anonymous_is_unauthorized(self):
        self.assertEqual(self.client.get("/api/v1/admin/users").status_code, 401)

    def test_admin_sees_every_user(self):
        body = self.client.get("/api/v1/admin/users", headers=self._auth(ADMIN_ID)).json()
        self.assertEqual({u["userId"] for u in body}, {USER_ID, OTHER_ID, ADMIN_ID})

    async def test_admin_can_disable_alerts(self):
        resp = self.client.post(
            f"/api/v1/admin/users/{USER_ID}",
            json={"isAlertsActive": False},
            headers=self._auth(ADMIN_ID),
        )

        self.assertEqual(resp.status_code, 200)
        async with self.db._db.execute(
            "SELECT is_alerts_active FROM scanner_users WHERE user_id = ?", (USER_ID,)
        ) as cur:
            row = await cur.fetchone()
        self.assertEqual(row["is_alerts_active"], 0)

    def test_admin_cannot_deactivate_self(self):
        resp = self.client.post(
            f"/api/v1/admin/users/{ADMIN_ID}",
            json={"isActive": False},
            headers=self._auth(ADMIN_ID),
        )
        self.assertEqual(resp.status_code, 400)

    def test_unknown_user_is_404(self):
        resp = self.client.post(
            "/api/v1/admin/users/999999",
            json={"isActive": True},
            headers=self._auth(ADMIN_ID),
        )
        self.assertEqual(resp.status_code, 404)


if __name__ == "__main__":
    unittest.main()


class TestCardCrud(_PersonalCase):
    """
    Заведення, редагування й видалення картки.

    До цього HTTP умів лише читати картки й правити ліміти: щоб додати нову
    чи перейменувати стару, доводилось іти в Telegram.
    """

    async def _card_ids(self, owner: int = USER_ID) -> set[str]:
        return {c["id"] for c in await self.db.get_cards(owner_id=owner)}

    async def test_create_and_read_back(self):
        resp = self.client.post(
            "/api/v1/cards",
            json={
                "bankName": "Monobank",
                "cardNumber": "1234567812345678",
                "label": "основна",
                "category": "self",
                "balance": 1500.0,
            },
            headers=self._auth(USER_ID),
        )

        self.assertEqual(resp.status_code, 200, resp.text)
        card_id = resp.json()["cardId"]

        cards = await self.db.get_cards(owner_id=USER_ID)
        created = next(c for c in cards if c["id"] == card_id)
        self.assertEqual(created["bank_name"], "monobank")
        self.assertEqual(created["last_four"], "5678")
        self.assertEqual(created["is_own"], 1)

    async def test_last_four_is_enough_without_full_number(self):
        resp = self.client.post(
            "/api/v1/cards",
            json={"bankName": "pumb", "lastFour": "4321"},
            headers=self._auth(USER_ID),
        )
        self.assertEqual(resp.status_code, 200, resp.text)

    def test_broken_number_is_rejected(self):
        for payload in (
            {"bankName": "monobank", "cardNumber": "12345"},
            {"bankName": "monobank"},
            {"bankName": "monobank", "lastFour": "12"},
            {"bankName": "", "lastFour": "1234"},
        ):
            with self.subTest(payload=payload):
                resp = self.client.post(
                    "/api/v1/cards", json=payload, headers=self._auth(USER_ID)
                )
                self.assertEqual(resp.status_code, 400)

    def test_unknown_category_is_rejected(self):
        resp = self.client.post(
            "/api/v1/cards",
            json={"bankName": "monobank", "lastFour": "1111", "category": "нонсенс"},
            headers=self._auth(USER_ID),
        )
        self.assertEqual(resp.status_code, 400)

    async def test_partial_update_keeps_other_fields(self):
        card_id = self.client.post(
            "/api/v1/cards",
            json={"bankName": "monobank", "lastFour": "1111", "label": "стара мітка"},
            headers=self._auth(USER_ID),
        ).json()["cardId"]

        resp = self.client.post(
            f"/api/v1/cards/{card_id}",
            json={"note": "тримати для виводу"},
            headers=self._auth(USER_ID),
        )
        self.assertEqual(resp.status_code, 200)

        card = next(c for c in await self.db.get_cards(owner_id=USER_ID) if c["id"] == card_id)
        self.assertEqual(card["note"], "тримати для виводу")
        self.assertEqual(card["label"], "стара мітка", "мітка не мала змінитись")

    async def test_category_drives_is_own(self):
        card_id = self.client.post(
            "/api/v1/cards",
            json={"bankName": "monobank", "lastFour": "2222", "category": "self"},
            headers=self._auth(USER_ID),
        ).json()["cardId"]

        self.client.post(
            f"/api/v1/cards/{card_id}",
            json={"category": "drop"},
            headers=self._auth(USER_ID),
        )

        card = next(c for c in await self.db.get_cards(owner_id=USER_ID) if c["id"] == card_id)
        self.assertEqual(card["category"], "drop")
        self.assertEqual(card["is_own"], 0, "дропівська картка не може лишатись власною")

    async def test_delete_removes_card_and_its_transactions(self):
        card_id = self.client.post(
            "/api/v1/cards",
            json={"bankName": "monobank", "lastFour": "3333"},
            headers=self._auth(USER_ID),
        ).json()["cardId"]

        await self.db._db.execute(
            "INSERT INTO card_transactions (id, card_id, amount, direction, type, source, timestamp)"
            " VALUES ('tx-1', ?, 100.0, 'in', 'work', 'test', 0)",
            (card_id,),
        )
        await self.db._db.commit()

        resp = self.client.delete(f"/api/v1/cards/{card_id}", headers=self._auth(USER_ID))
        self.assertEqual(resp.status_code, 200)

        self.assertNotIn(card_id, await self._card_ids())
        async with self.db._db.execute(
            "SELECT COUNT(*) FROM card_transactions WHERE card_id = ?", (card_id,)
        ) as cur:
            self.assertEqual((await cur.fetchone())[0], 0, "транзакції лишились сиротами")

    async def test_cannot_touch_someone_elses_card(self):
        card_id = self.client.post(
            "/api/v1/cards",
            json={"bankName": "monobank", "lastFour": "4444"},
            headers=self._auth(OTHER_ID),
        ).json()["cardId"]

        self.assertEqual(
            self.client.post(
                f"/api/v1/cards/{card_id}", json={"label": "чуже"},
                headers=self._auth(USER_ID),
            ).status_code,
            403,
        )
        self.assertEqual(
            self.client.delete(f"/api/v1/cards/{card_id}", headers=self._auth(USER_ID)).status_code,
            403,
        )
        self.assertIn(card_id, await self._card_ids(OTHER_ID))

    def test_card_secrets_never_leave_the_backend(self):
        # Повний номер і токен Monobank зберігаються, але у відповідь не
        # потрапляють: SELECT * тягнув їх просто в браузер.
        self.client.post(
            "/api/v1/cards",
            json={"bankName": "monobank", "cardNumber": "4444555566667777"},
            headers=self._auth(USER_ID),
        )

        body = self.client.get("/api/v1/cards", headers=self._auth(USER_ID)).json()
        self.assertTrue(body)
        for card in body:
            self.assertNotIn("cardNumber", card)
            self.assertNotIn("monoXTokenEncrypted", card)
            self.assertNotIn("monoWebhookSecret", card)
            self.assertNotIn("4444555566667777", str(card))
        self.assertTrue(body[0]["hasCardNumber"])
