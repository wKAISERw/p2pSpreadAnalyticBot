"""
GET /cards/match — чи є чим брати ордер.

Ендпоінт віддавав 500 на бойовому: картковий блок на сайті просто зникав,
і виглядало це як «функції немає», а не як помилка. Тестів на нього не
було зовсім — саме тому виняток і доїхав до продакшену.
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

USER_ID = 5252
BOT_TOKEN = "123456:TEST-TOKEN-FOR-SIGNING"


class TestCardMatch(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        from api.routers.control import router

        self._tmp = TemporaryDirectory()
        self.db = MerchantDB(db_path=Path(self._tmp.name) / "t.db")
        await self.db.start()
        await self.db.register_user(USER_ID, USER_ID)

        # Без цього движок одразу повертає `disabled`, і тест не дійшов би
        # до жодної реальної гілки — саме там і жила бойова помилка.
        #
        # `card_module_mode` не проходить через update_user_card_settings:
        # той метод пише все, крім нього, а вмикається модуль окремим UPDATE
        # у меню бота. Тому тут теж прямий запис.
        await self.db.update_user_card_settings(USER_ID, {})
        await self.db._db.execute(
            "UPDATE user_card_settings SET card_module_mode='on' WHERE user_id=?",
            (USER_ID,),
        )
        await self.db._db.commit()

        for i, (bank, bal) in enumerate(
            [("monobank", 21_000.0), ("sense", 4_800.0), ("pumb", 5_000.0)]
        ):
            await self.db.add_card({
                "id": f"c{i}", "owner_id": USER_ID, "bank_name": bank,
                "last_four": f"111{i}", "balance": bal, "status": "active",
                # Непрогріта картка обмежена cold_card_limit (2 000 ₴), і
                # без цього прапорця будь-яка сума більша за нього чесно
                # відпадала б — тест ловив би прогрів, а не те, що перевіряє.
                "is_warmed_up": 1,
            })

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

    def _get(self, **params):
        return self.client.get(
            "/api/v1/cards/match", params=params, headers=self._auth()
        )

    def test_plain_request_does_not_explode(self):
        # Рівно те, що шле дашборд.
        resp = self._get(bank="monobank", amount=7759, direction="buy", banks="monobank")
        self.assertEqual(resp.status_code, 200, resp.text)

    def test_response_has_everything_the_card_block_reads(self):
        body = self._get(
            bank="monobank", amount=5000, direction="buy", banks="monobank,pumb"
        ).json()

        for key in ("status", "bestCard", "splitOptions", "availableUah",
                    "rejections", "balances", "transferTips", "routeBanks"):
            with self.subTest(key=key):
                self.assertIn(key, body)

    def test_enough_money_gives_a_card(self):
        body = self._get(
            bank="monobank", amount=5000, direction="buy", banks="monobank"
        ).json()
        self.assertEqual(body["status"], "success")
        self.assertIsNotNone(body["bestCard"])

    def test_banks_param_is_optional(self):
        # Старий фронтенд шле без нього — 500 тут означав би, що деплой
        # двох гілок мусить бути атомарним.
        resp = self._get(bank="monobank", amount=5000, direction="buy")
        self.assertEqual(resp.status_code, 200, resp.text)

    def test_unknown_bank_is_answered_not_crashed(self):
        resp = self._get(bank="60", amount=5000, direction="buy", banks="60")
        self.assertEqual(resp.status_code, 200, resp.text)
        self.assertIn(resp.json()["status"], ("no_cards", "needs_split", "success"))

    def test_unlimited_card_does_not_break_json(self):
        # Одноразовий ліміт «без обмеження» движок тримає як float('inf'),
        # а JSON такого числа не має: відповідь падала з 500, і картковий
        # блок на сайті просто зникав, ніби функції немає.
        resp = self._get(bank="monobank", amount=7759, direction="buy", banks="monobank")
        self.assertEqual(resp.status_code, 200, resp.text)
        self.assertIn("bestCard", resp.json())

    def test_card_number_and_tokens_never_leave_the_backend(self):
        # Рядок картки містить повний номер і токени Monobank. Боту вони
        # потрібні, браузеру — ніколи.
        body = self._get(
            bank="monobank", amount=5000, direction="buy", banks="monobank"
        ).json()
        blob = str(body)
        for secret in ("card_number", "mono_x_token", "mono_webhook_secret"):
            with self.subTest(field=secret):
                self.assertNotIn(secret, blob)

    def test_bad_direction_is_400(self):
        self.assertEqual(
            self._get(bank="monobank", amount=100, direction="sideways").status_code,
            400,
        )


if __name__ == "__main__":
    unittest.main()
