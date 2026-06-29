from datetime import datetime


class AppState:
    def __init__(self):
        self.stats = {
            "cycles": 0,
            "last_cycle_ms": 0,
            "bots_detected_today": 0,
            "spreads_found_today": 0,
            "llm_queue": 0,
            "review_queue": 0,
            "totalScanned": 0,
            "opportunitiesFound": 0,
            "cb_status": {
                "Bybit": "CLOSED",
                "Binance": "CLOSED",
                "OKX": "CLOSED",
                "MEXC": "CLOSED",
                "Wallet": "CLOSED"
            },
            "is_scanner_active": False,
            "internet_connected": True
        }

        self.opportunities = []
        self.current_alerts = []      # SpreadAlert objects for /active command
        self.last_buy_grouped = {}
        self.last_sell_grouped = {}
        self.logs = []
        self.blacklist = []

        # 🔥 Додаємо глобальні налаштування
        self.global_settings = {
            "risk_mode": "WARNING",
            "behavior_alert_score": 60,
            "velocity_spike_per_hour": 20.0,
            "sticky_min_chain": 3,
            "review_ttl_hours": 24.0,
            "max_alerts_per_cycle": 5
        }

        # 🔥 Додаємо налаштування користувача
        self.user_settings = {
            "min_capital": 5000,
            "max_capital": 15000,
            "min_spread": 0.5,
            "banks": ["43", "14"],  # 43 - Mono, 14 - Privat
            "merchant_filters": {
                "min_orders": 50,
                "min_rate": 95.0,
                "max_offline_mins": 0
            },
            "auto_trade": {
                "enabled": False,
                "max_trade_amount": 5000,
                "min_spread": 0.8,
                "allowed_exchanges": ["Bybit", "OKX"],
                "max_risk_score": 30
            }
        }


state = AppState()