"""
Відбитки розділів: чи справді вони міняються лише від «своїх» змін.

Дашборд опитував усі десять розділів на кожному тіку — 60 запитів на
хвилину при інтервалі 10 секунд, майже всі з тією самою відповіддю. Тепер
він тягне один відбиток і йде по дані лише туди, де сума інша.

Ціна помилки тут несиметрична, і тести перевіряють обидва боки:

  * відбиток, який НЕ змінився після реальної зміни, означає розділ, що
    мовчки перестав оновлюватись — дефект, який видно лише тоді, коли
    людина вже діє за старими даними;
  * відбиток, який змінюється від чужих правок, поверне те саме зайве
    оновлення, заради усунення якого все й робилось, ще й з повідомленням
    про зміни, яких не було.
"""
from __future__ import annotations

import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from api.sync_state import SECTIONS, build_sync_state
from core.storage.merchant_db import MerchantDB

USER_ID = 7070


class _Case(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self._tmp = TemporaryDirectory()
        self.db = MerchantDB(db_path=Path(self._tmp.name) / "t.db")
        await self.db.start()
        await self.db.register_user(USER_ID, USER_ID)

    async def asyncTearDown(self):
        await self.db.stop()
        self._tmp.cleanup()

    async def _state(self) -> dict:
        return await build_sync_state(self.db, USER_ID)

    async def _changed(self, before: dict, action) -> list[str]:
        await action()
        after = await self._state()
        return sorted(k for k in before if before[k] != after[k])

    async def _sql(self, query: str, params: tuple):
        await self.db._db.execute(query, params)
        await self.db._db.commit()


class TestShape(_Case):
    async def test_every_section_is_present(self):
        state = await self._state()
        self.assertEqual(sorted(state), sorted(SECTIONS))

    async def test_nothing_is_unreadable(self):
        # None означає «порахувати не вдалось» — найчастіше через колонку,
        # яку перейменували. Розділ у такому стані оновлюватиметься наосліп.
        state = await self._state()
        broken = [k for k, v in state.items() if v is None]
        self.assertEqual(broken, [], f"розділи без відбитка: {broken}")

    async def test_identical_reads_give_identical_digests(self):
        # Інакше «зміни» показувались би на кожному тіку.
        self.assertEqual(await self._state(), await self._state())


class TestIsolation(_Case):
    """Кожна правка чіпає рівно свій розділ."""

    async def test_capital_touches_only_filters(self):
        before = await self._state()
        changed = await self._changed(before, lambda: self._sql(
            "UPDATE scanner_users SET working_capital=99999 WHERE user_id=?", (USER_ID,)
        ))
        self.assertEqual(changed, ["filters"])

    async def test_taker_preset_touches_only_taker(self):
        before = await self._state()
        changed = await self._changed(before, lambda: self._sql(
            "UPDATE scanner_users SET taker_buy_amount=700 WHERE user_id=?", (USER_ID,)
        ))
        self.assertEqual(changed, ["taker"])

    async def test_sniper_touches_only_sniper(self):
        before = await self._state()
        changed = await self._changed(before, lambda: self._sql(
            "UPDATE scanner_users SET sniper_rules='[{\"pct\":2}]' WHERE user_id=?",
            (USER_ID,),
        ))
        self.assertEqual(changed, ["sniper"])

    async def test_new_card_touches_only_cards(self):
        before = await self._state()

        async def add():
            await self.db.add_card({
                "id": "c-1", "owner_id": USER_ID, "bank_name": "monobank",
                "last_four": "1111", "balance": 5000.0, "status": "active",
            })

        self.assertEqual(await self._changed(before, add), ["cards"])

    async def test_balance_change_touches_only_cards(self):
        await self.db.add_card({
            "id": "c-2", "owner_id": USER_ID, "bank_name": "pumb",
            "last_four": "2222", "balance": 1000.0, "status": "active",
        })
        before = await self._state()
        changed = await self._changed(before, lambda: self._sql(
            "UPDATE cards SET balance=7000 WHERE id=?", ("c-2",)
        ))
        self.assertEqual(changed, ["cards"])

    async def test_bank_limit_touches_only_limits(self):
        before = await self._state()

        async def edit():
            await self.db.set_user_bank_limit(USER_ID, "monobank", "daily_out_max", 50_000)

        self.assertEqual(await self._changed(before, edit), ["limits"])

    async def test_card_display_touches_only_display(self):
        before = await self._state()

        async def edit():
            current = await self.db.get_user_card_settings(USER_ID) or {}
            await self.db.update_user_card_settings(
                USER_ID, {**current, "card_split_mode": "inter_bank"}
            )

        self.assertEqual(await self._changed(before, edit), ["display"])

    async def test_feature_toggle_touches_only_features(self):
        before = await self._state()

        async def toggle():
            await self.db.toggle_feature_status(USER_ID, "inter_bank_matching")

        self.assertEqual(await self._changed(before, toggle), ["features"])

    async def test_other_users_edits_are_invisible(self):
        # Відбиток мусить бути персональним: чужа правка не має виглядати
        # як зміна твоїх налаштувань.
        await self.db.register_user(9090, 9090)
        before = await self._state()
        changed = await self._changed(before, lambda: self._sql(
            "UPDATE scanner_users SET working_capital=12345 WHERE user_id=?", (9090,)
        ))
        self.assertEqual(changed, [])


if __name__ == "__main__":
    unittest.main()
