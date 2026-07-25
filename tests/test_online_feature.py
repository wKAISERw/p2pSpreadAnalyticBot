import asyncio
import sys
import unittest
import time
from decimal import Decimal
from typing import Optional

sys.path.append('.')

from exchanges.base import Order
from exchanges.binance import BinanceExchange
from exchanges.bybit import BybitExchange
from exchanges.mexc import MexcExchange
from exchanges.wallet import WalletExchange
from core.engine.alert_dispatcher import AlertDispatcher
from bot.alert_builder import _online_badge

class TestMerchantLastOnline(unittest.TestCase):

    def test_binance_parsing(self):
        # Setup mock item and test parsing
        mock_item = {
            "adv": {
                "advNo": "12345",
                "price": "44.50",
                "surplusAmount": "100.0",
                "minSingleTransAmount": "500.0",
                "maxSingleTransAmount": "2000.0",
                "remarks": "hello",
            },
            "advertiser": {
                "userNo": "user123",
                "nickName": "BinanceMerch",
                "monthOrderCount": 150,
                "monthFinishRate": "0.98",
                "positiveRate": 0.99,
                "userType": "merchant",
                "activeTimeInSecond": 180  # 3 minutes ago
            }
        }
        # Instantiate exchange with dummy client
        exchange = BinanceExchange(client=None)
        order = exchange._parse_order(mock_item, "43")
        self.assertEqual(order.last_online_mins, 3)

    def test_bybit_parsing(self):
        # Case 1: Online
        mock_item_online = {
            "id": "bybit1",
            "price": "44.20",
            "lastQuantity": "200.0",
            "minAmount": "1000.0",
            "maxAmount": "5000.0",
            "userId": "u1",
            "nickName": "BybitMerch1",
            "recentOrderNum": 90,
            "recentExecuteRate": 97,
            "isOnline": True,
            "lastLogoutTime": str(int(time.time() - 300))
        }
        exchange = BybitExchange(client=None)
        order_online = exchange._parse_order(mock_item_online)
        self.assertEqual(order_online.last_online_mins, 0)

        # Case 2: Offline
        mock_item_offline = dict(mock_item_online)
        mock_item_offline["isOnline"] = False
        mock_item_offline["lastLogoutTime"] = str(int(time.time() - 300)) # 5 mins ago
        order_offline = exchange._parse_order(mock_item_offline)
        self.assertIsNotNone(order_offline.last_online_mins)
        self.assertAlmostEqual(order_offline.last_online_mins, 5, delta=1)

    def test_mexc_parsing(self):
        mock_item = {
            "id": "mexc1",
            "price": "44.30",
            "availableQuantity": "150.0",
            "minTradeLimit": "800.0",
            "maxTradeLimit": "3000.0",
            "remark": "terms",
            "merchant": {
                "memberId": "m1",
                "nickName": "MexcMerch",
                "isCertified": True,
                "lastOnlineTime": int((time.time() - 480) * 1000) # 8 minutes ago
            },
            "merchantStatistics": {
                "doneLastMonthCount": 110,
                "lastMonthCompleteRate": "0.99"
            }
        }
        exchange = MexcExchange(client=None)
        order = exchange._parse_order(mock_item, "43", "buy")
        self.assertIsNotNone(order.last_online_mins)
        self.assertAlmostEqual(order.last_online_mins, 8, delta=1)

    def test_wallet_parsing(self):
        # Case 1: Online
        mock_item_online = {
            "id": "w1",
            "price": "44.10",
            "lastQuantity": "300.0",
            "minAmount": "500.0",
            "maxAmount": "10000.0",
            "userId": "wu1",
            "nickname": "WalletMerch",
            "orderNum": 80,
            "executeRate": 0.95,
            "isOnline": True
        }
        exchange = WalletExchange(client=None)
        order_online = exchange._parse_order(mock_item_online)
        self.assertEqual(order_online.last_online_mins, 0)

        # Case 2: Offline
        mock_item_offline = dict(mock_item_online)
        mock_item_offline["isOnline"] = False
        order_offline = exchange._parse_order(mock_item_offline)
        self.assertEqual(order_offline.last_online_mins, 5)

    def test_online_badge_formatting(self):
        o1 = Order(id="1", price=Decimal("1"), available_amount=Decimal("1"), min_limit=Decimal("1"),
                   max_limit=Decimal("1"), merchant_id="1", merchant_name="N1", month_order_count=10,
                   finish_rate_pct=95.0, last_online_mins=0)
        self.assertEqual(_online_badge(o1), " (🟢 online)")

        o2 = Order(id="2", price=Decimal("1"), available_amount=Decimal("1"), min_limit=Decimal("1"),
                   max_limit=Decimal("1"), merchant_id="2", merchant_name="N2", month_order_count=10,
                   finish_rate_pct=95.0, last_online_mins=1)
        self.assertEqual(_online_badge(o2), " (🟢 1m)")

        o3 = Order(id="3", price=Decimal("1"), available_amount=Decimal("1"), min_limit=Decimal("1"),
                   max_limit=Decimal("1"), merchant_id="3", merchant_name="N3", month_order_count=10,
                   finish_rate_pct=95.0, last_online_mins=5)
        self.assertEqual(_online_badge(o3), " (🟡 5m)")

        o4 = Order(id="4", price=Decimal("1"), available_amount=Decimal("1"), min_limit=Decimal("1"),
                   max_limit=Decimal("1"), merchant_id="4", merchant_name="N4", month_order_count=10,
                   finish_rate_pct=95.0, last_online_mins=None)
        self.assertEqual(_online_badge(o4), "")

    def test_dispatcher_filtering(self):
        # Test alert dispatcher _user_wants filtering logic
        dispatcher = AlertDispatcher(db=None, notifier=None)
        
        user = {
            "scanner_mode": "SPREAD",
            "capital": 10000.0,
            "min_spread": 0.5,
            "bank_codes": ["43"],
            "merchant_filters": {
                "max_offline_mins": 5
            },
            "exchange_merchant_filters": {}
        }

        # Case 1: Online (0 mins) -> Passed
        buy_o = Order(id="b1", price=Decimal("40.0"), available_amount=Decimal("100"), min_limit=Decimal("100"),
                      max_limit=Decimal("10000"), merchant_id="m1", merchant_name="BuyMerch", month_order_count=100,
                      finish_rate_pct=99.0, last_online_mins=0, exchange="Bybit")
        sell_o = Order(id="s1", price=Decimal("41.0"), available_amount=Decimal("100"), min_limit=Decimal("100"),
                       max_limit=Decimal("10000"), merchant_id="m2", merchant_name="SellMerch", month_order_count=100,
                       finish_rate_pct=99.0, last_online_mins=3, exchange="Bybit")

        opp = {
            "buy_order": buy_o,
            "sell_order": sell_o,
            "actual_entry_uah": 5000.0,
            "net_spread_pct": 1.2,
            "buy_banks_fit": ["monobank"],
            "sell_banks_fit": ["monobank"]
        }

        wants, reason, _ = dispatcher._user_wants(user, opp)
        self.assertTrue(wants, f"Should pass. Reason: {reason}")

        # Case 2: One merchant offline 10 mins (threshold 5) -> Filtered
        sell_o.last_online_mins = 10
        wants, reason, _ = dispatcher._user_wants(user, opp)
        self.assertFalse(wants)
        self.assertIn("last online 10m > 5m", reason)


if __name__ == "__main__":
    unittest.main()
