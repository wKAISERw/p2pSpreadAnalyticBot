# core/storage/merchant_db.py
# =============================================================================
# Facade — backward-compatible шар поверх розбитих репозиторіїв.
# Всі існуючі імпорти `from core.storage.merchant_db import MerchantDB`
# продовжують працювати без змін.
# =============================================================================
from __future__ import annotations
from pathlib import Path
from typing import Optional

from core.storage.base_db     import MerchantDB as _BaseDB
from core.storage.merchant_repo import MerchantRepo
from core.storage.user_repo     import UserRepo
from core.storage.stats_repo    import StatsRepo
from core.storage.card_repo     import CardRepo
from core.storage.risk_repo     import RiskRepo

DB_PATH = Path("data/merchants.db")


class MerchantDB(_BaseDB, MerchantRepo, UserRepo, StatsRepo, CardRepo, RiskRepo):
    """
    Єдина точка входу — збирає всі репозиторії через множинне наслідування.
    Зовнішній API (назви методів) залишається незмінним.

    Використання:
        db = MerchantDB()
        await db.start()
        verdict = await db.get_verdict("binance", "abc123")   # MerchantRepo
        user    = await db.register_user(12345, 12345)        # UserRepo
        card    = await db.get_cards(owner_id=12345)          # CardRepo
        stats   = await db.get_proposals_summary()            # StatsRepo
    """