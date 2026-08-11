# tests/test_byok.py
"""
BYOK: свої ключі до моделей.

Чому це взагалі є. Спільний ключ означає спільний ліміт: квота одна, обліку
по людях немає, і другий активний користувач не подвоює витрати, а ламає
фічу обом. Питання не «хто платить», а «в кого закінчиться».

Три межі, які тут стережуться, бо кожну легко перейти непомітно:

1. **Ключ не витікає.** Ні в лог, ні в `repr`, ні у відбиток кулдауну.
   Один `logger.debug("%s", task)` — і він назавжди в файлі.
2. **Чуже не стає спільним.** Судження, зроблене чужим ключем, не має
   потрапляти туди, звідки читають усі: інакше перший користувач вирішує,
   що побачать решта.
3. **Зламаний ключ не дає «OK».** Немає ключа або він відвалився —
   персонального вердикту просто немає, і людина бачить базовий. Мовчазне
   «чисто» тут було б тим самим «не знаю ≠ безпечно», лише на чужих ключах.
"""
from __future__ import annotations

import time
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from core.storage.llm_keys_repo import (
    MODE_ALWAYS, MODE_OFF, MODE_ONDEMAND, PROVIDERS, LLMCredentials, mask,
)
from core.storage.merchant_db import MerchantDB
from core.workers.llm_worker import _Cooldowns

SECRET = "gsk_verysecretkeyvalue1234567890"


class TestKeysDoNotLeak(unittest.TestCase):
    def test_repr_hides_the_key(self):
        creds = LLMCredentials(owner_id=7, keys={"groq": SECRET})
        self.assertNotIn(SECRET, repr(creds))
        self.assertNotIn(SECRET, f"{creds}")
        self.assertNotIn(SECRET, "%s" % (creds,))

    def test_fingerprint_hides_the_key(self):
        fp = _Cooldowns.fingerprint("groq", SECRET)
        self.assertNotIn(SECRET, fp)
        self.assertTrue(fp.startswith("groq:"))

    def test_fingerprint_is_stable_and_distinct(self):
        self.assertEqual(
            _Cooldowns.fingerprint("groq", SECRET), _Cooldowns.fingerprint("groq", SECRET)
        )
        self.assertNotEqual(
            _Cooldowns.fingerprint("groq", SECRET), _Cooldowns.fingerprint("groq", SECRET + "x")
        )

    def test_mask_shows_enough_to_recognise_and_no_more(self):
        masked = mask(SECRET)
        self.assertNotIn(SECRET, masked)
        self.assertTrue(masked.startswith(SECRET[:4]))
        self.assertTrue(masked.endswith(SECRET[-4:]))

    def test_short_value_is_not_partially_revealed(self):
        self.assertEqual(mask("abc123"), "…")
        self.assertEqual(mask(""), "")


class TestCooldownsAreNotShared(unittest.TestCase):
    """Чужий 429 не має глушити тих, хто чужу квоту не витрачав."""

    def setUp(self):
        self.cd = _Cooldowns()

    def test_rate_limit_on_one_key_leaves_others_working(self):
        self.cd.rate_limited("groq", "key-A")
        self.assertFalse(self.cd.ready("groq", "key-A"))
        self.assertTrue(self.cd.ready("groq", "key-B"))

    def test_same_key_other_provider_is_independent(self):
        self.cd.rate_limited("groq", SECRET)
        self.assertTrue(self.cd.ready("gemini", SECRET))

    def test_backoff_grows_and_is_capped(self):
        waits = [self.cd.rate_limited("groq", "k")[1] for _ in range(8)]
        self.assertLess(waits[0], waits[3])
        self.assertLessEqual(max(waits), 300.0)

    def test_success_resets_the_streak(self):
        self.cd.rate_limited("groq", "k")
        self.cd.rate_limited("groq", "k")
        self.cd.ok("groq", "k")
        first_again = self.cd.rate_limited("groq", "k")
        self.assertEqual(first_again[0], 1)

    def test_absent_key_is_never_ready(self):
        # Без ключа провайдера просто немає — і це не «пауза», а «нічим
        # дзвонити». Спроба все одно впала б, лише на секунду пізніше.
        self.assertFalse(self.cd.ready("groq", ""))


class TestStorage(unittest.IsolatedAsyncioTestCase):
    USER = 501
    OTHER = 502

    async def asyncSetUp(self):
        self._tmp = TemporaryDirectory()
        self.db = MerchantDB(Path(self._tmp.name) / "t.db")
        await self.db.start()
        await self.db.register_user(self.USER, self.USER)
        await self.db.register_user(self.OTHER, self.OTHER)

    async def asyncTearDown(self):
        await self.db.stop()
        self._tmp.cleanup()

    async def test_key_survives_the_round_trip(self):
        ok, _ = await self.db.save_llm_key(self.USER, "groq", SECRET)
        self.assertTrue(ok)
        creds = await self.db.credentials_for(self.USER)
        self.assertEqual(creds.key_for("groq"), SECRET)
        self.assertEqual(creds.owner_id, self.USER)

    async def test_stored_value_is_not_the_key(self):
        await self.db.save_llm_key(self.USER, "groq", SECRET)
        async with self.db._db.execute(
            "SELECT api_key FROM user_llm_keys WHERE user_id = ?", (self.USER,)
        ) as cur:
            row = await cur.fetchone()
        self.assertNotEqual(row["api_key"], SECRET)

    async def test_listing_never_returns_the_key(self):
        await self.db.save_llm_key(self.USER, "groq", SECRET)
        rows = await self.db.list_llm_keys(self.USER)
        self.assertEqual(len(rows), 1)
        self.assertNotIn(SECRET, str(rows))

    async def test_short_key_is_rejected_with_a_reason(self):
        ok, note = await self.db.save_llm_key(self.USER, "groq", "abc")
        self.assertFalse(ok)
        self.assertTrue(note)

    async def test_unknown_provider_is_rejected(self):
        ok, _ = await self.db.save_llm_key(self.USER, "skynet", SECRET)
        self.assertFalse(ok)

    async def test_other_user_gets_nothing(self):
        await self.db.save_llm_key(self.USER, "groq", SECRET)
        creds = await self.db.credentials_for(self.OTHER)
        self.assertFalse(creds.is_personal)

    async def test_mode_off_disables_the_key_without_deleting_it(self):
        await self.db.save_llm_key(self.USER, "groq", SECRET)
        await self.db.set_byok_mode(self.USER, MODE_OFF)
        self.assertFalse((await self.db.credentials_for(self.USER)).is_personal)
        await self.db.set_byok_mode(self.USER, MODE_ONDEMAND)
        self.assertTrue((await self.db.credentials_for(self.USER)).is_personal)

    async def test_default_mode_does_not_spend_the_key_silently(self):
        # Чужа квота — не те, що можна почати витрачати за людину мовчки.
        self.assertNotEqual(await self.db.get_byok_mode(self.USER), MODE_ALWAYS)

    async def test_always_mode_users_are_findable(self):
        await self.db.save_llm_key(self.USER, "groq", SECRET)
        await self.db.set_byok_mode(self.USER, MODE_ALWAYS)
        self.assertIn(self.USER, await self.db.users_with_always_mode())
        self.assertNotIn(self.OTHER, await self.db.users_with_always_mode())

    async def test_deleting_a_key_removes_it(self):
        await self.db.save_llm_key(self.USER, "groq", SECRET)
        self.assertTrue(await self.db.delete_llm_key(self.USER, "groq"))
        self.assertFalse((await self.db.credentials_for(self.USER)).is_personal)

    async def test_error_is_recorded_with_its_reason(self):
        await self.db.save_llm_key(self.USER, "groq", SECRET)
        await self.db.mark_key_error(self.USER, "groq", "401 Unauthorized")
        row = (await self.db.list_llm_keys(self.USER))[0]
        # «Не працює» людина полагодити не може, «401» — може.
        self.assertIn("401", row["last_error"])

    async def test_success_clears_the_error(self):
        await self.db.save_llm_key(self.USER, "groq", SECRET)
        await self.db.mark_key_error(self.USER, "groq", "429")
        await self.db.mark_key_ok(self.USER, "groq")
        row = (await self.db.list_llm_keys(self.USER))[0]
        self.assertEqual(row["last_error"], "")
        self.assertGreater(row["last_ok_at"], 0)


class TestPersonalVerdictsStayPersonal(unittest.IsolatedAsyncioTestCase):
    USER = 601
    OTHER = 602
    EX, MID = "Binance", "m-1"

    async def asyncSetUp(self):
        self._tmp = TemporaryDirectory()
        self.db = MerchantDB(Path(self._tmp.name) / "t.db")
        await self.db.start()
        for uid in (self.USER, self.OTHER):
            await self.db.register_user(uid, uid)
        await self.db.save_verdict(
            self.EX, self.MID, "Мерчант", "умови", "OK", "NONE", "спільна причина",
            "groq", trade_recommendation="APPROVE", terms_summary="спільна вижимка",
        )

    async def asyncTearDown(self):
        await self.db.stop()
        self._tmp.cleanup()

    async def _save_personal(self, **over):
        payload = dict(
            verdict="BLOCK", risk_type="TRIANGLE", reason="моя причина",
            trade_recommendation="REJECT", terms_summary="моя вижимка", source="groq",
        )
        payload.update(over)
        await self.db.save_personal_verdict(self.USER, self.EX, self.MID, **payload)

    async def test_personal_verdict_does_not_touch_the_shared_one(self):
        await self._save_personal()
        rec, verdict, reason, *_ = await self.db.get_trade_recommendation_full(self.EX, self.MID)
        self.assertEqual(rec, "APPROVE")
        self.assertEqual(reason, "спільна причина")

    async def test_owner_sees_their_own_verdict(self):
        await self._save_personal()
        rec, verdict, reason, *_ = await self.db.get_trade_recommendation_full(
            self.EX, self.MID, user_id=self.USER
        )
        self.assertEqual(rec, "REJECT")
        self.assertEqual(reason, "моя причина")

    async def test_another_user_sees_the_shared_verdict(self):
        await self._save_personal()
        rec, *_ = await self.db.get_trade_recommendation_full(
            self.EX, self.MID, user_id=self.OTHER
        )
        self.assertEqual(rec, "APPROVE")

    async def test_missing_personal_falls_back_not_blank(self):
        # Базову оцінку ховати за наявністю ключа не можна ніколи: без
        # ключа бот не має працювати гірше, ніж працював.
        rec, *_ = await self.db.get_trade_recommendation_full(
            self.EX, self.MID, user_id=self.USER
        )
        self.assertEqual(rec, "APPROVE")

    async def test_unknown_personal_verdict_never_replaces_the_base(self):
        await self._save_personal(verdict="UNKNOWN", trade_recommendation="PENDING")
        rec, *_ = await self.db.get_trade_recommendation_full(
            self.EX, self.MID, user_id=self.USER
        )
        self.assertEqual(rec, "APPROVE")

    async def test_stale_personal_verdict_is_ignored(self):
        from core.storage import llm_keys_repo

        await self._save_personal()
        await self.db._db.execute(
            "UPDATE merchant_verdict_user SET updated_at = ? WHERE user_id = ?",
            (time.time() - (llm_keys_repo.PERSONAL_TTL_HOURS + 1) * 3600, self.USER),
        )
        await self.db._db.commit()
        self.assertIsNone(await self.db.get_personal_verdict(self.USER, self.EX, self.MID))

    async def test_changed_terms_invalidate_the_personal_verdict(self):
        # Мерчант переписав умови — старе судження про новий текст показувати
        # не можна, скільки б воно не було свіжим.
        await self._save_personal(terms_hash="hash-A")
        self.assertIsNone(
            await self.db.get_personal_verdict(self.USER, self.EX, self.MID, "hash-B")
        )
        self.assertIsNotNone(
            await self.db.get_personal_verdict(self.USER, self.EX, self.MID, "hash-A")
        )

    async def test_extras_follow_the_same_rule(self):
        await self._save_personal(terms_facts='[{"topic":"моє","quote":"q","meaning":"m"}]')
        mine = await self.db.get_verdict_extras(self.EX, self.MID, user_id=self.USER)
        shared = await self.db.get_verdict_extras(self.EX, self.MID)
        self.assertIn("моє", mine["terms_facts"])
        self.assertNotIn("моє", shared["terms_facts"])

    async def test_dropping_keys_drops_personal_verdicts(self):
        await self._save_personal()
        self.assertEqual(await self.db.drop_personal_verdicts(self.USER), 1)
        rec, *_ = await self.db.get_trade_recommendation_full(
            self.EX, self.MID, user_id=self.USER
        )
        self.assertEqual(rec, "APPROVE")


class TestSchedulingIsPerOwner(unittest.TestCase):
    """Персональна перевірка не має виглядати дублем спільної."""

    def test_owner_is_part_of_the_dedup_key(self):
        import inspect

        from core.workers.llm_worker import LLMWorkerPool

        src = inspect.getsource(LLMWorkerPool.schedule)
        self.assertIn("owner", src)
        self.assertIn("cache_key = f\"{owner}:{exchange}:{merchant_id}\"", src)


class TestUiContract(unittest.TestCase):
    def test_every_provider_has_a_title_and_a_hint(self):
        from core.storage.llm_keys_repo import PROVIDER_HINTS, PROVIDER_TITLES

        for provider in PROVIDERS:
            with self.subTest(provider=provider):
                self.assertIn(provider, PROVIDER_TITLES)
                self.assertIn(provider, PROVIDER_HINTS)

    def test_callbacks_fit_telegram(self):
        from bot.keyboards.byok import byok_key_kb, byok_main_kb, byok_mode_kb

        kbs = [
            byok_main_kb([], MODE_ONDEMAND),
            byok_main_kb(
                [{"provider": "groq", "title": "Groq", "masked": "gsk_…7890",
                  "enabled": True, "readable": True, "last_ok_at": 0.0,
                  "last_error": "", "last_error_at": 0.0}],
                MODE_ALWAYS,
            ),
            byok_mode_kb(MODE_ONDEMAND),
            byok_key_kb("groq"),
        ]
        for kb in kbs:
            for row in kb.inline_keyboard:
                for button in row:
                    if button.callback_data:
                        with self.subTest(data=button.callback_data):
                            self.assertLessEqual(len(button.callback_data.encode()), 64)

    def test_long_merchant_id_drops_the_button_instead_of_the_alert(self):
        # Telegram відхиляє callback_data понад 64 байти разом з усім
        # повідомленням. Втратити алерт через додаткову кнопку — найгірший
        # з можливих обмінів.
        import asyncio

        from bot.alert_builder import _byok_buttons

        class _Order:
            exchange = "Binance"
            merchant_id = "x" * 90

        class _Alert:
            buy_order = sell_order = _Order()

        class _DB:
            async def credentials_for(self, uid):
                return LLMCredentials(owner_id=uid, keys={"groq": SECRET})

        class _Notifier:
            _db = _DB()
            _chat_id = 1

        row = asyncio.run(_byok_buttons(_Notifier(), 1, _Alert()))
        self.assertEqual(row, [])

    def test_proxy_is_checked_by_truthiness_not_identity(self):
        """
        `_llm_pool` — це `GlobalProxy`, і він НІКОЛИ не `None`.

        Перевірка `is None` тут завжди хибна, тож до старту сканера хендлер
        провалився б далі й упав на `.schedule()` з AttributeError. Правильна
        перевірка — `__bool__`, який віддає False, поки ціль не проставлена.
        """
        import inspect

        from bot.handlers import byok
        from bot.handlers.core import _llm_pool

        self.assertIsNotNone(_llm_pool)
        self.assertFalse(bool(_llm_pool))
        self.assertIn("if not _llm_pool", inspect.getsource(byok.on_ai_check))

    def test_key_menu_offers_replace_and_delete(self):
        from bot.keyboards.byok import byok_key_kb

        data = [b.callback_data for row in byok_key_kb("groq").inline_keyboard for b in row]
        self.assertIn("byok:add:groq", data)
        self.assertIn("byok:del:groq", data)


if __name__ == "__main__":
    unittest.main()
