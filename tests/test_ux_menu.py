import asyncio
import sys
import unittest
import types
from unittest.mock import MagicMock, AsyncMock

# Mock dependency modules that require DB or config startup
sys.modules['core.storage.merchant_db'] = MagicMock()
sys.modules['core.utils.cache'] = MagicMock()
sys.modules['core.utils'] = MagicMock()

# Setup config as a package module
config_mock = types.ModuleType('config')
config_mock.__path__ = []
config_mock.settings = MagicMock()
config_mock.settings.working_capital_uah = 5000.0
config_mock.settings.min_spread_pct = 0.5
config_mock.settings.admin_id = 123456
sys.modules['config'] = config_mock

# Setup default bank codes mock
config_banks_mock = MagicMock()
config_banks_mock.DEFAULT_BANK_CODES = ["43", "14"]
config_banks_mock.BANK_NAMES = {"43": "Monobank", "14": "PrivatBank"}
sys.modules['config.banks'] = config_banks_mock

# Setup config.runtime mock
sys.modules['config.runtime'] = MagicMock()

# Import keyboards to verify layout and builders
from bot.keyboards.menu import main_menu_kb, system_menu_kb
from bot.keyboards.exchanges import exchanges_menu_kb, keys_menu_kb, exchanges_status_kb
from bot.keyboards.filters import filters_menu_kb
from bot.keyboards.cards import cards_dashboard_kb
from bot.keyboards.monitoring import monitoring_menu_kb
from bot.keyboards.common import back_to_balance_kb, back_to_sessions_kb, back_to_monitoring_kb, back_to_system_kb, back_to_exchanges_kb


class TestUXMenus(unittest.TestCase):
    def test_main_menu_kb_structure(self):
        # Admin menu (5 options + help = 6 buttons)
        kb = main_menu_kb(is_admin=True)
        self.assertIsNotNone(kb)
        buttons = [btn.text for row in kb.inline_keyboard for btn in row]
        self.assertIn("📡 Біржі", buttons)
        self.assertIn("🎛 Фільтри", buttons)
        self.assertIn("💳 Картки", buttons)
        self.assertIn("📊 Моніторинг", buttons)
        self.assertIn("⚙️ Система", buttons)
        self.assertIn("ℹ️ Допомога", buttons)

        # Normal user menu (no System button)
        kb_user = main_menu_kb(is_admin=False)
        buttons_user = [btn.text for row in kb_user.inline_keyboard for btn in row]
        self.assertNotIn("⚙️ Система", buttons_user)

    def test_exchanges_menu_kb(self):
        statuses = [
            {"name": "Bybit", "enabled": True},
            {"name": "OKX", "enabled": False, "cooldown_remaining_h": 2.5}
        ]
        kb = exchanges_menu_kb(statuses)
        self.assertIsNotNone(kb)
        buttons = [btn.text for row in kb.inline_keyboard for btn in row]
        self.assertIn("🟢 🟠 Bybit", buttons)
        self.assertIn("⏱ ⚫ OKX (2.5г)", buttons)
        self.assertIn("💰 Баланси", buttons)
        self.assertIn("🔑 API Ключі", buttons)
        self.assertIn("🔐 Сесії", buttons)
        self.assertIn("📣 Створити оголошення", buttons)
        self.assertIn("🔙 В головне меню", buttons)

    def test_filters_menu_kb(self):
        kb = filters_menu_kb(scanner_mode="MAKER_SELL", is_alerts_active=True)
        buttons = [btn.text for row in kb.inline_keyboard for btn in row]
        self.assertIn("💰 Капітал", buttons)
        self.assertIn("🏦 Банки", buttons)
        self.assertIn("💲 Ціна купівлі (maker)", buttons)
        self.assertIn("🔕 Вимкнути алерти", buttons)

        kb2 = filters_menu_kb(scanner_mode="MAKER_BUY", is_alerts_active=False)
        buttons2 = [btn.text for row in kb2.inline_keyboard for btn in row]
        self.assertIn("📊 Цільова маржа (maker)", buttons2)
        self.assertIn("🔔 Увімкнути алерти", buttons2)

    def test_cards_dashboard_kb(self):
        cards = [
            {"id": "c1", "bank_name": "monobank", "last_four": "1234", "is_own": True, "balance": 1500.0, "status": "active"},
            {"id": "c2", "bank_name": "privatbank", "last_four": "5678", "is_own": False, "balance": 0.0, "status": "frozen_funds"}
        ]
        kb = cards_dashboard_kb(cards, module_mode="full")
        buttons = [btn.text for row in kb.inline_keyboard for btn in row]
        self.assertIn("🟢 Monobank 1234 — 1500 ₴", buttons)
        self.assertIn("❄️ Privatbank 5678 (Дроп) — 0 ₴", buttons)
        self.assertIn("➕ Додати картку", buttons)
        self.assertIn("🏦 Ліміти банків", buttons)
        self.assertIn("🖨 Налашт. виводу", buttons)
        self.assertIn("📊 Звіт по картках", buttons)
        self.assertIn("🔍 Діагностика", buttons)

    def test_monitoring_menu_kb(self):
        kb_admin = monitoring_menu_kb(is_admin=True)
        buttons_admin = [btn.text for row in kb_admin.inline_keyboard for btn in row]
        self.assertIn("📈 Стан системи", buttons_admin)
        self.assertIn("📝 Логи / аудит", buttons_admin)
        self.assertIn("🏥 Health check", buttons_admin)

        kb_user = monitoring_menu_kb(is_admin=False)
        buttons_user = [btn.text for row in kb_user.inline_keyboard for btn in row]
        self.assertNotIn("📝 Логи / аудит", buttons_user)
        self.assertNotIn("🏥 Health check", buttons_user)

    def test_system_menu_kb(self):
        kb = system_menu_kb(is_scanner_active=True, is_muted=False)
        buttons = [btn.text for row in kb.inline_keyboard for btn in row]
        self.assertIn("⏸ Зупинити ядро", buttons)
        self.assertIn("🔕 Пауза 1г", buttons)
        self.assertIn("🔕 Пауза 4г", buttons)
        self.assertIn("🔕 Вимк. назавжди (глобально)", buttons)
        self.assertIn("🛡️ Антифрод", buttons)
        self.assertIn("👥 Управління юзерами", buttons)
        self.assertIn("🔄 Debug / перезапуск", buttons)

    def test_back_buttons(self):
        for kb_fn in [back_to_balance_kb, back_to_sessions_kb, back_to_monitoring_kb, back_to_system_kb, back_to_exchanges_kb]:
            kb = kb_fn()
            self.assertIsNotNone(kb)
            buttons = [btn.text for row in kb.inline_keyboard for btn in row]
            self.assertGreater(len(buttons), 0)

    def test_router_priority_order(self):
        from bot.handlers import get_router
        r = get_router()
        self.assertEqual(len(r.callback_query.handlers), 0)
        self.assertEqual(len(r.message.handlers), 0)
        self.assertGreater(len(r.sub_routers), 1)
        # Ensure fallback_router is last
        fallback_r = r.sub_routers[-1]
        self.assertEqual(len(fallback_r.callback_query.handlers), 1)
        self.assertEqual(len(fallback_r.message.handlers), 1)

    def test_global_proxy_transparency(self):
        from bot.handlers.core import _db, setup
        # Before setup, it should evaluate to False (None target)
        self.assertFalse(bool(_db))
        
        # Call setup with a mock database
        mock_db = MagicMock()
        setup(db=mock_db, account_clients={})
        
        # After setup, it should evaluate to True (has target)
        self.assertTrue(bool(_db))
        
        # Method calls on _db should route transparently to mock_db
        _db.register_user(user_id=123, chat_id=456)
        mock_db.register_user.assert_called_once_with(user_id=123, chat_id=456)


if __name__ == "__main__":
    unittest.main()
