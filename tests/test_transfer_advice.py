"""
Поради «перекажи звідси туди» — і чому їх часом не буває.

Розрахунок жив усередині `card_notifier` і повертав готовий HTML, тож на
сайті картковий блок був порожній, хоча в чат приходило «Перекажіть
3 655 ₴ з Monobank *6251 на Pumb *9664». Тепер числа рахуються окремо, а
форматує їх кожен по-своєму.

Головне, що тут перевіряється, — коли поради НЕ давати. Порада, яка
впирається в ліміт на другому кроці, гірша за її відсутність: людина
почне переказ і дізнається про межу вже в застосунку банку.
"""
from __future__ import annotations

import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from core.engine.transfer_advice import suggest_transfers
from core.storage.merchant_db import MerchantDB

USER_ID = 6060


class TestSuggestTransfers(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self._tmp = TemporaryDirectory()
        self.db = MerchantDB(db_path=Path(self._tmp.name) / "t.db")
        await self.db.start()
        await self.db.register_user(USER_ID, USER_ID)

    async def asyncTearDown(self):
        await self.db.stop()
        self._tmp.cleanup()

    async def _card(self, card_id: str, bank: str, balance: float, last_four: str = "1111"):
        await self.db.add_card({
            "id": card_id, "owner_id": USER_ID, "bank_name": bank,
            "last_four": last_four, "balance": balance, "status": "active",
        })

    async def _limit(self, card_id: str, field: str, value: float):
        await self.db.update_card_limit_override(card_id, field, value)
        await self.db._db.execute(
            "UPDATE cards SET is_custom_limits=1 WHERE id=?", (card_id,)
        )
        await self.db._db.commit()

    async def test_gap_is_covered_from_another_bank(self):
        await self._card("pumb", "pumb", 5_000.0, "9664")
        await self._card("mono", "monobank", 21_000.0, "6251")

        tips = await suggest_transfers(self.db, USER_ID, "pumb", 8_660.0)

        self.assertEqual(len(tips), 1)
        self.assertEqual(tips[0].from_card_id, "mono")
        self.assertEqual(tips[0].to_card_id, "pumb")
        # Рівно нестача, а не «переклади все»: зайве на цільовій картці
        # з'їдає її місячний ліміт без потреби.
        self.assertAlmostEqual(tips[0].amount_uah, 3_660.0, places=2)

    async def test_enough_balance_needs_no_advice(self):
        await self._card("pumb", "pumb", 50_000.0)
        await self._card("mono", "monobank", 21_000.0)
        self.assertEqual(await suggest_transfers(self.db, USER_ID, "pumb", 8_000.0), [])

    async def test_no_card_of_that_bank_gives_nothing(self):
        # Переказувати нема куди — це не «порада», а інша проблема.
        await self._card("mono", "monobank", 90_000.0)
        self.assertEqual(await suggest_transfers(self.db, USER_ID, "pumb", 8_000.0), [])

    async def test_source_without_money_is_not_offered(self):
        await self._card("pumb", "pumb", 1_000.0)
        await self._card("sense", "sense", 500.0)
        self.assertEqual(await suggest_transfers(self.db, USER_ID, "pumb", 8_000.0), [])

    async def test_source_single_tx_limit_blocks_advice(self):
        # Джерело має гроші, але за раз стільки не відправить.
        await self._card("pumb", "pumb", 1_000.0)
        await self._card("mono", "monobank", 90_000.0)
        await self._limit("mono", "max_single_tx_out", 2_000.0)

        self.assertEqual(await suggest_transfers(self.db, USER_ID, "pumb", 20_000.0), [])

    async def test_destination_single_tx_limit_blocks_advice(self):
        # Дзеркальний випадок: гроші відправити можна, а прийняти — ні.
        await self._card("pumb", "pumb", 1_000.0)
        await self._card("mono", "monobank", 90_000.0)
        await self._limit("pumb", "max_single_tx_in", 2_000.0)

        self.assertEqual(await suggest_transfers(self.db, USER_ID, "pumb", 20_000.0), [])

    async def test_every_capable_source_is_offered(self):
        # Вибір лишається за людиною: одна картка може бути «гарячішою» за
        # іншу з причин, яких бот не знає.
        await self._card("pumb", "pumb", 5_000.0)
        await self._card("mono", "monobank", 21_000.0)
        await self._card("sense", "sense", 9_000.0)

        tips = await suggest_transfers(self.db, USER_ID, "pumb", 8_660.0)
        self.assertEqual({t.from_card_id for t in tips}, {"mono", "sense"})

    async def test_no_cards_at_all_is_not_a_crash(self):
        self.assertEqual(await suggest_transfers(self.db, USER_ID, "pumb", 1_000.0), [])

    async def test_zero_amount_is_ignored(self):
        await self._card("pumb", "pumb", 100.0)
        self.assertEqual(await suggest_transfers(self.db, USER_ID, "pumb", 0.0), [])


if __name__ == "__main__":
    unittest.main()
