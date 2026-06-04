import asyncio
import sys
import unittest
from unittest.mock import AsyncMock, MagicMock

# Ensure we import from project root
sys.path.append(".")

from core.engine.alert_dispatcher import AlertDispatcher

class TestAlertDispatcherCards(unittest.TestCase):
    def test_select_best_bank_from_owned(self):
        # Case 1: no cards or no allowed banks
        self.assertIsNone(AlertDispatcher._select_best_bank_from_owned(set(), []))
        
        # Case 2: allowed banks is {monobank, pumb}, cards owned: monobank (inactive), privatbank (active)
        allowed = {"monobank", "pumb"}
        cards = [
            {"bank_name": "monobank", "status": "inactive"},
            {"bank_name": "privatbank", "status": "active"} # Privatbank not in allowed
        ]
        self.assertEqual(AlertDispatcher._select_best_bank_from_owned(allowed, cards), "monobank")
        
        # Case 3: allowed {monobank, privatbank}, cards: monobank (inactive), privatbank (frozen_funds)
        allowed = {"monobank", "privatbank"}
        cards = [
            {"bank_name": "monobank", "status": "inactive"},     # score 5
            {"bank_name": "privatbank", "status": "frozen_funds"} # score 2
        ]
        self.assertEqual(AlertDispatcher._select_best_bank_from_owned(allowed, cards), "monobank")

        # Case 4: tie breaker alphabetically: allowed {monobank, a-bank}, cards: both inactive (score 5)
        allowed = {"monobank", "a-bank"}
        cards = [
            {"bank_name": "monobank", "status": "inactive"},
            {"bank_name": "a-bank", "status": "inactive"}
        ]
        # "a-bank" starts with "a", should be chosen alphabetically
        self.assertEqual(AlertDispatcher._select_best_bank_from_owned(allowed, cards), "a-bank")

if __name__ == "__main__":
    unittest.main()
