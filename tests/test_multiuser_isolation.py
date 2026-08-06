"""
Ізоляція юзерів між собою.

Закриває три дефекти, які в сумі давали найгірший сценарій: чужа людина
торгувала ключами власника інсталяції.
"""
from __future__ import annotations

import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from config import settings
from core.storage import user_repo as user_repo_mod
from core.storage.merchant_db import MerchantDB

OWNER_ID = 111111
STRANGER_ID = 222222


class _DBCase(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self._tmp = TemporaryDirectory()
        self.db = MerchantDB(db_path=Path(self._tmp.name) / "t.db")
        await self.db.start()
        # Власник інсталяції — OWNER_ID
        self._patch = patch.object(settings, "admin_id", OWNER_ID)
        self._patch.start()

    async def asyncTearDown(self):
        self._patch.stop()
        await self.db.stop()
        self._tmp.cleanup()


class TestCredentialIsolation(_DBCase):
    """
    Дефект: при відсутності своїх ключів код мовчки падав на user_id=0 —
    легасі-слот власника. Юзер створював оголошення на чужому акаунті.
    """

    async def asyncSetUp(self):
        await super().asyncSetUp()
        # Ключі власника лежать у легасі-слоті 0 (як після single-user режиму)
        await self.db.save_credentials("Bybit", "OWNER_KEY", "OWNER_SECRET", user_id=0)

    async def test_stranger_without_keys_gets_nothing(self):
        creds = await self.db.get_credentials_for_user("Bybit", STRANGER_ID)
        self.assertIsNone(creds, "чужому юзеру віддали ключі з легасі-слота власника")

    async def test_owner_still_reaches_legacy_slot(self):
        creds = await self.db.get_credentials_for_user("Bybit", OWNER_ID)
        self.assertIsNotNone(creds)
        self.assertEqual(creds["api_key"], "OWNER_KEY")

    async def test_stranger_gets_own_keys(self):
        await self.db.save_credentials("Bybit", "STRANGER_KEY", "S", user_id=STRANGER_ID)
        creds = await self.db.get_credentials_for_user("Bybit", STRANGER_ID)
        self.assertEqual(creds["api_key"], "STRANGER_KEY")

    async def test_own_keys_win_over_legacy_for_owner(self):
        await self.db.save_credentials("Bybit", "OWNER_NEW", "N", user_id=OWNER_ID)
        creds = await self.db.get_credentials_for_user("Bybit", OWNER_ID)
        self.assertEqual(creds["api_key"], "OWNER_NEW")


class TestSessionIsolation(_DBCase):
    """
    Дефект: якщо сесії для запитаного user_id не було, бралася БУДЬ-ЯКА
    остання активна — тобто фонові задачі ходили під кукі випадкового юзера.
    """

    # OKX не зберігається без authorization (save_auth_session це валідує),
    # тому в тестах сесії мають бути "справжніми" — інакше вони проходили б
    # вхолосту, порівнюючи порожнє з порожнім.
    OWNER_HDR = {"authorization": "owner-token"}
    STRANGER_HDR = {"authorization": "stranger-token"}

    async def _save(self, user_id: int, headers: dict) -> None:
        saved = await self.db.save_auth_session("OKX", headers, {"c": "1"}, user_id=user_id)
        self.assertTrue(saved, "сесія не збереглась — тест був би беззмістовним")

    async def test_background_slot_does_not_borrow_stranger_session(self):
        await self._save(STRANGER_ID, self.STRANGER_HDR)

        headers, cookies, _ = await self.db.get_auth_session("OKX", user_id=0)
        self.assertEqual(headers, {}, "фоновий слот підхопив сесію чужого юзера")
        self.assertEqual(cookies, {})

    async def test_background_slot_uses_owner_session(self):
        await self._save(OWNER_ID, self.OWNER_HDR)

        headers, _, _ = await self.db.get_auth_session("OKX", user_id=0)
        self.assertEqual(headers, self.OWNER_HDR, "сесія власника має обслуговувати фонові задачі")

    async def test_user_gets_only_own_session(self):
        await self._save(OWNER_ID, self.OWNER_HDR)

        headers, _, _ = await self.db.get_auth_session("OKX", user_id=STRANGER_ID)
        self.assertEqual(headers, {}, "юзеру віддали чужу сесію")

    async def test_owner_session_preferred_over_stranger(self):
        await self._save(STRANGER_ID, self.STRANGER_HDR)
        await self._save(OWNER_ID, self.OWNER_HDR)

        headers, _, _ = await self.db.get_auth_session("OKX", user_id=0)
        self.assertEqual(headers, self.OWNER_HDR)


class TestSessionInvalidationScope(_DBCase):
    """
    Дефект: invalidate_auth_session(exchange, user_id=0) виконував
    UPDATE ... WHERE exchange=? без user_id — один AuthError у фетчера
    відгуків розлогінював усіх юзерів одразу.
    """

    async def test_owner_burnout_does_not_logout_everyone(self):
        await self.db.save_auth_session("Binance", {"h": "o"}, {}, user_id=OWNER_ID)
        await self.db.save_auth_session("Binance", {"h": "s"}, {}, user_id=STRANGER_ID)

        await self.db.invalidate_auth_session("Binance", user_id=0)

        owner_h, _, _ = await self.db.get_auth_session("Binance", user_id=OWNER_ID)
        stranger_h, _, _ = await self.db.get_auth_session("Binance", user_id=STRANGER_ID)

        self.assertEqual(owner_h, {}, "сесію власника мали погасити")
        self.assertEqual(stranger_h, {"h": "s"}, "сесію іншого юзера гасити не можна")

    async def test_targeted_invalidation_unchanged(self):
        await self.db.save_auth_session("Binance", {"h": "o"}, {}, user_id=OWNER_ID)
        await self.db.save_auth_session("Binance", {"h": "s"}, {}, user_id=STRANGER_ID)

        await self.db.invalidate_auth_session("Binance", user_id=STRANGER_ID)

        owner_h, _, _ = await self.db.get_auth_session("Binance", user_id=OWNER_ID)
        stranger_h, _, _ = await self.db.get_auth_session("Binance", user_id=STRANGER_ID)

        self.assertEqual(owner_h, {"h": "o"})
        self.assertEqual(stranger_h, {})


class TestEncryptionKeyGuard(_DBCase):
    """
    Дефект: без ENCRYPTION_KEY crypto генерував тимчасовий ключ, decrypt()
    мовчки повертав "" — і код читав це як "у юзера немає ключів", після чого
    спрацьовував фолбек на слот власника.
    """

    async def test_passes_when_no_credentials_stored(self):
        await self.db.verify_encryption_key()  # не має кидати

    async def test_fails_when_key_missing_but_credentials_exist(self):
        from core.utils.crypto import EncryptionKeyError

        await self.db.save_credentials("Bybit", "K", "S", user_id=OWNER_ID)
        with patch.object(user_repo_mod, "_owner_user_id", return_value=OWNER_ID), \
             patch("core.utils.crypto.has_explicit_key", return_value=False):
            with self.assertRaises(EncryptionKeyError):
                await self.db.verify_encryption_key()

    async def test_fails_when_key_does_not_match_stored_data(self):
        from core.utils.crypto import EncryptionKeyError

        await self.db.save_credentials("Bybit", "K", "S", user_id=OWNER_ID)
        with patch("core.utils.crypto.has_explicit_key", return_value=True), \
             patch("core.utils.crypto.can_decrypt", return_value=False):
            with self.assertRaises(EncryptionKeyError):
                await self.db.verify_encryption_key()

    async def test_passes_when_key_matches(self):
        await self.db.save_credentials("Bybit", "K", "S", user_id=OWNER_ID)
        with patch("core.utils.crypto.has_explicit_key", return_value=True):
            await self.db.verify_encryption_key()  # не має кидати


if __name__ == "__main__":
    unittest.main()
