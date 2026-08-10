"""
Контракт HTTP API дашборду.

Закриває дефекти, через які веб-дашборд рапортував успіх там, де його
не було:

  * ваги ризик-движка (W_REGEX, W_LLM…) не можна було записати взагалі —
    dict_to_camel/dict_to_snake ламали регістр ключа по дорозі;
  * кнопка «Disconnect» біля біржі не мала ендпоінта, тож ключі лишались
    у боті, і сканер продовжував ходити на біржу під ними;
  * назва біржі з веба нормалізувалась через .capitalize(), тому ключі
    OKX/MEXC/BingX лягали в базу під написанням, за яким бот їх не шукав;
  * спільні дії (глобальні налаштування, чорний список) не перевіряли, хто
    їх викликає — а ключ X-API-Key у типовому деплої має кожен відвідувач.
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
from config.runtime import ALLOWED_KEYS, runtime_config
from core.storage.merchant_db import MerchantDB

USER_ID = 777
OTHER_ID = 888
ADMIN_ID = 999
BOT_TOKEN = "123456:TEST-TOKEN-FOR-SIGNING"


def _client() -> TestClient:
    from api.routers.dashboard import router

    app = FastAPI()
    app.include_router(router)
    return TestClient(app, raise_server_exceptions=False)


def _auth(telegram_id: int) -> dict:
    return {"Authorization": f"Bearer {auth_lib.issue_session(telegram_id)}"}


class TestGlobalSettingsKeyCasing(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self._tmp = TemporaryDirectory()
        self.db = MerchantDB(db_path=Path(self._tmp.name) / "t.db")
        await self.db.start()

        self._prev_db = runtime_config._db
        self._prev_cache = dict(runtime_config._cache)
        runtime_config._db = self.db
        runtime_config._cache = {}
        await runtime_config.init_table()

        self._patchers = [
            patch("bot.handlers.core._db", self.db),
            patch.object(settings_obj, "telegram_bot_token", BOT_TOKEN),
            patch.object(settings_obj, "admin_id", ADMIN_ID),
        ]
        for p in self._patchers:
            p.start()

        self.client = _client()

    async def asyncTearDown(self):
        for p in self._patchers:
            p.stop()
        runtime_config._db = self._prev_db
        runtime_config._cache = self._prev_cache
        await self.db.stop()
        self._tmp.cleanup()

    def test_risk_weights_are_accepted_in_camel_case(self):
        # Фронтенд бачить ці ключі як WRegex/WLlm — саме такими їх робить
        # to_camel з W_REGEX/W_LLM на виході GET /settings/global.
        resp = self.client.post(
            "/api/v1/settings/global",
            json={"WRegex": 1.5, "WLlm": 2.0},
            headers=_auth(ADMIN_ID),
        )

        self.assertEqual(resp.status_code, 200)
        body = resp.json()
        self.assertEqual(body["rejected"], [])
        self.assertEqual(set(body["applied"]), {"W_REGEX", "W_LLM"})

    def test_weights_land_in_runtime_config_under_canonical_keys(self):
        self.client.post(
            "/api/v1/settings/global",
            json={"WReviewsPct": 3.5},
            headers=_auth(ADMIN_ID),
        )

        # Сканер читає саме верхній регістр — інакше значення не доїде.
        self.assertEqual(runtime_config.get("W_REVIEWS_PCT"), "3.5")

    def test_unknown_keys_are_still_rejected(self):
        resp = self.client.post(
            "/api/v1/settings/global",
            # riskMode тут стояв роками — і був одним із тих ключів, які
            # приймались, зберігались і ні на що не впливали: клас, який його
            # читав, спрацьовував ДО аналізу ризику й у дефолтному режимі
            # пропускав усе. Прибраний разом із MerchantFilter.
            json={"maxAlertsPerCycle": 7, "totallyMadeUp": 1},
            headers=_auth(ADMIN_ID),
        )

        body = resp.json()
        self.assertEqual(body["applied"], {"max_alerts_per_cycle": "7"})
        self.assertEqual(body["rejected"], ["totally_made_up"])

    def test_removed_keys_are_rejected_like_any_other_unknown(self):
        # Ключ, який більше нічим не керує, має чесно відхилятись, а не
        # мовчки зберігатись у базу. Коли в запиті НЕМАЄ жодного відомого
        # ключа, ендпоінт віддає 400 — це наявна поведінка, не нова.
        resp = self.client.post(
            "/api/v1/settings/global",
            json={"riskMode": "STRICT"},
            headers=_auth(ADMIN_ID),
        )
        self.assertEqual(resp.status_code, 400)
        self.assertIn("risk_mode", resp.json()["detail"])

    def test_removed_key_does_not_block_the_rest_of_the_payload(self):
        # Дашборд — окремий репозиторій і може ще слати riskMode. Решта
        # налаштувань у тому ж запиті мусить доїхати.
        resp = self.client.post(
            "/api/v1/settings/global",
            json={"riskMode": "STRICT", "maxAlertsPerCycle": 9},
            headers=_auth(ADMIN_ID),
        )
        body = resp.json()
        self.assertEqual(body["applied"], {"max_alerts_per_cycle": "9"})
        self.assertEqual(body["rejected"], ["risk_mode"])

    def test_every_allowed_key_survives_the_camel_round_trip(self):
        # GET віддає camelCase; те саме значення має прийматись назад у POST.
        from api.utils import to_camel

        for key in ALLOWED_KEYS:
            with self.subTest(key=key):
                resp = self.client.post(
                    "/api/v1/settings/global",
                    json={to_camel(key): "1"},
                    headers=_auth(ADMIN_ID),
                )
                self.assertEqual(resp.json()["rejected"], [], f"{key} відхилено")

    def test_non_admin_cannot_touch_global_settings(self):
        # Ці ключі керують сканером для всіх. UI ховав секцію від не-адмінів,
        # але HTTP лишався відкритим будь-кому з ключем — тобто всім.
        resp = self.client.post(
            "/api/v1/settings/global",
            json={"riskMode": "RELAXED"},
            headers=_auth(USER_ID),
        )
        self.assertEqual(resp.status_code, 403)

    def test_anonymous_cannot_touch_global_settings(self):
        resp = self.client.post("/api/v1/settings/global", json={"riskMode": "RELAXED"})
        self.assertEqual(resp.status_code, 401)


class _CredentialsCase(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
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

        self.client = _client()

    async def asyncTearDown(self):
        for p in self._patchers:
            p.stop()
        await self.db.stop()
        self._tmp.cleanup()


class TestCredentialsDeletion(_CredentialsCase):
    async def test_delete_removes_credentials_from_bot(self):
        await self.db.save_credentials("Binance", "k", "s", user_id=USER_ID)

        resp = self.client.delete("/api/v1/credentials/binance", headers=_auth(USER_ID))

        self.assertEqual(resp.status_code, 200)
        self.assertFalse(await self.db.has_credentials("Binance", user_id=USER_ID))

    async def test_delete_is_404_when_nothing_to_remove(self):
        resp = self.client.delete("/api/v1/credentials/okx", headers=_auth(USER_ID))
        self.assertEqual(resp.status_code, 404)

    async def test_delete_does_not_touch_other_users(self):
        await self.db.save_credentials("Binance", "k", "s", user_id=USER_ID)
        await self.db.save_credentials("Binance", "k2", "s2", user_id=OTHER_ID)

        resp = self.client.delete("/api/v1/credentials/binance", headers=_auth(USER_ID))

        self.assertEqual(resp.status_code, 200)
        self.assertTrue(await self.db.has_credentials("Binance", user_id=OTHER_ID))

    async def test_cannot_delete_someone_elses_credentials(self):
        await self.db.save_credentials("Binance", "k2", "s2", user_id=OTHER_ID)

        resp = self.client.delete(
            "/api/v1/credentials/binance",
            params={"telegram_id": OTHER_ID},
            headers=_auth(USER_ID),
        )

        self.assertEqual(resp.status_code, 403)
        self.assertTrue(await self.db.has_credentials("Binance", user_id=OTHER_ID))


class TestCredentialsExchangeNaming(_CredentialsCase):
    """
    Назва біржі — це ключ пошуку, і бот шукає рівно "OKX"/"MEXC"/"BingX".

    Ендпоінт робив .capitalize(), тож із веба ключі лягали як "Okx"/"Mexc"/
    "Bingx". Зберігалось успішно, дашборд показував «Підключено», а сканер
    цих ключів не бачив — і працював без них.
    """

    async def test_lowercase_names_land_under_canonical_spelling(self):
        for sent, canonical in [
            ("okx", "OKX"),
            ("mexc", "MEXC"),
            ("bingx", "BingX"),
            ("binance", "Binance"),
            ("bybit", "Bybit"),
        ]:
            with self.subTest(exchange=sent):
                resp = self.client.post(
                    f"/api/v1/credentials/{sent}",
                    json={"key": "k", "secret": "s", "passphrase": ""},
                    headers=_auth(USER_ID),
                )
                self.assertEqual(resp.status_code, 200)
                self.assertTrue(
                    await self.db.has_credentials(canonical, user_id=USER_ID),
                    f"{sent} не знайшовся як {canonical}",
                )

    async def test_telegram_wallet_maps_to_wallet(self):
        resp = self.client.post(
            "/api/v1/credentials/telegram wallet",
            json={"key": "k", "secret": "", "passphrase": ""},
            headers=_auth(USER_ID),
        )

        self.assertEqual(resp.status_code, 200)
        self.assertTrue(await self.db.has_credentials("Wallet", user_id=USER_ID))

    async def test_unknown_exchange_is_rejected_instead_of_invented(self):
        resp = self.client.post(
            "/api/v1/credentials/cryptobot",
            json={"key": "k", "secret": "s", "passphrase": ""},
            headers=_auth(USER_ID),
        )

        self.assertEqual(resp.status_code, 400)
        self.assertFalse(await self.db.has_credentials("Cryptobot", user_id=USER_ID))

    async def test_keys_saved_from_web_are_visible_to_the_bot(self):
        # Найважливіше твердження: те, що поклав дашборд, читає той самий
        # шлях, яким користується сканер.
        self.client.post(
            "/api/v1/credentials/okx",
            json={"key": "web-key", "secret": "web-secret", "passphrase": "p"},
            headers=_auth(USER_ID),
        )

        creds = await self.db.get_credentials_for_user("OKX", USER_ID)
        self.assertIsNotNone(creds)
        self.assertEqual(creds["api_key"], "web-key")


class TestPersonalEndpointsNeedSession(_CredentialsCase):
    """
    X-API-Key каже «клієнту можна стукати в API», а не «клієнт — це юзер 777».

    У бойовому деплої ключ підставляє Caddy перед статикою, тобто його має
    кожен відвідувач сайту. Поки персональні ендпоінти вірили ?telegram_id=,
    чужі баланси читались зміною цифри в URL.
    """

    def test_accounts_require_session(self):
        resp = self.client.get(f"/api/v1/accounts/{OTHER_ID}")
        self.assertEqual(resp.status_code, 401)

    def test_accounts_reject_other_users_id(self):
        resp = self.client.get(f"/api/v1/accounts/{OTHER_ID}", headers=_auth(USER_ID))
        self.assertEqual(resp.status_code, 403)

    def test_admin_may_read_other_users(self):
        resp = self.client.get(f"/api/v1/accounts/{OTHER_ID}", headers=_auth(ADMIN_ID))
        self.assertEqual(resp.status_code, 200)

    def test_telegram_sync_rejects_other_users_id(self):
        resp = self.client.get(f"/api/v1/telegram/sync/{OTHER_ID}", headers=_auth(USER_ID))
        self.assertEqual(resp.status_code, 403)

    def test_detailed_stats_require_session(self):
        resp = self.client.get("/api/v1/stats/detailed")
        self.assertEqual(resp.status_code, 401)

    def test_detailed_stats_default_to_own_trades(self):
        # scope за замовчуванням — 'mine'. Поки параметра не існувало,
        # StatsEngine отримував owner_user_id=0 і рахував усіх разом, тож
        # у власному PnL були чужі угоди.
        resp = self.client.get("/api/v1/stats/detailed", headers=_auth(USER_ID))
        self.assertEqual(resp.status_code, 200)
        self.assertIn("summary", resp.json())

    def test_cross_user_stats_are_admin_only(self):
        self.assertEqual(
            self.client.get(
                "/api/v1/stats/detailed", params={"scope": "all"}, headers=_auth(USER_ID)
            ).status_code,
            403,
        )
        self.assertEqual(
            self.client.get(
                "/api/v1/stats/detailed", params={"scope": "all"}, headers=_auth(ADMIN_ID)
            ).status_code,
            200,
        )

    async def test_telegram_sync_returns_canonical_exchange_names(self):
        # Фронтенд звіряє цей список зі своїм переліком бірж. Поки бекенд
        # віддавав його в нижньому регістрі, «BingX» не збігався ні з чим.
        await self.db.save_credentials("BingX", "k", "s", user_id=USER_ID)

        body = self.client.get(
            f"/api/v1/telegram/sync/{USER_ID}", headers=_auth(USER_ID)
        ).json()

        self.assertEqual(body["keys"], ["BingX"])


if __name__ == "__main__":
    unittest.main()


class TestPersonalBlacklist(_CredentialsCase):
    """
    Особистий чорний список.

    Раніше список був один на всіх: пересічний користувач не міг забанити
    нікого собі, а натиснувши «забанити» під алертом — вимикав мерчанта
    одразу всім користувачам бота.
    """

    def _ban(self, telegram_id: int, merchant_id: str, scope: str = "personal"):
        return self.client.post(
            "/api/v1/blacklist",
            params={"scope": scope},
            json={
                "exchange": "Bybit",
                "merchantId": merchant_id,
                "merchantName": f"Merchant {merchant_id}",
                "reason": "тест",
                "source": "test",
            },
            headers=_auth(telegram_id),
        )

    def test_regular_user_can_ban_for_himself(self):
        self.assertEqual(self._ban(USER_ID, "m-1").status_code, 200)

        mine = self.client.get("/api/v1/blacklist", headers=_auth(USER_ID)).json()
        self.assertEqual([e["merchantId"] for e in mine], ["m-1"])
        self.assertEqual(mine[0]["scope"], "personal")

    def test_personal_ban_is_invisible_to_others(self):
        self._ban(USER_ID, "m-2")

        theirs = self.client.get("/api/v1/blacklist", headers=_auth(OTHER_ID)).json()
        self.assertEqual(theirs, [])

    def test_regular_user_cannot_ban_globally(self):
        self.assertEqual(self._ban(USER_ID, "m-3", scope="global").status_code, 403)

    def test_admin_ban_is_visible_to_everyone(self):
        self.assertEqual(self._ban(ADMIN_ID, "m-4", scope="global").status_code, 200)

        theirs = self.client.get("/api/v1/blacklist", headers=_auth(USER_ID)).json()
        self.assertEqual([e["scope"] for e in theirs], ["global"])

    def test_user_removes_only_his_own_entry(self):
        self._ban(USER_ID, "m-5")
        self._ban(ADMIN_ID, "m-6", scope="global")

        # Свій — знімається.
        self.assertEqual(
            self.client.delete(
                "/api/v1/blacklist/Bybit/m-5", headers=_auth(USER_ID)
            ).status_code,
            200,
        )
        # Спільний — ні.
        self.assertEqual(
            self.client.delete(
                "/api/v1/blacklist/Bybit/m-6",
                params={"scope": "global"},
                headers=_auth(USER_ID),
            ).status_code,
            403,
        )

        remaining = self.client.get("/api/v1/blacklist", headers=_auth(USER_ID)).json()
        self.assertEqual([e["merchantId"] for e in remaining], ["m-6"])

    def test_unknown_exchange_is_rejected(self):
        resp = self.client.post(
            "/api/v1/blacklist",
            json={
                "exchange": "NoSuchExchange",
                "merchantId": "m-7",
                "merchantName": "x",
                "reason": "тест",
                "source": "test",
            },
            headers=_auth(USER_ID),
        )
        self.assertEqual(resp.status_code, 400)

    def test_blacklist_requires_session(self):
        self.assertEqual(self.client.get("/api/v1/blacklist").status_code, 401)
