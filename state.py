from collections import deque

# Скільки останніх записів логу тримаємо в пам'яті для /api/v1/logs.
LOG_BUFFER_SIZE = 500


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
        # Ті самі зв'язки в сирому вигляді, ключ — id із opportunities.
        #
        # `opportunities` уже серіалізовані під фронт, а персональні фільтри
        # (капітал, спред, банки, пороги мерчанта, чорний список) написані
        # для сирого opp з об'єктами Order — саме їх читає
        # AlertDispatcher._user_wants. Без цієї пари сайт міг би або
        # показувати всім однаковий глобальний список, або отримати другу
        # копію логіки фільтрації, яка неминуче розійдеться з першою.
        self.opportunities_raw = {}
        self.current_alerts = []      # SpreadAlert objects for /active command
        self.last_buy_grouped = {}
        self.last_sell_grouped = {}
        # Кільцевий буфер останніх подій логу. Наповнюється StateLogHandler
        # (див. main.setup_logging) — до цього список лишався порожнім,
        # і /api/v1/logs завжди віддавав [].
        self.logs = deque(maxlen=LOG_BUFFER_SIZE)
        self.blacklist = []

        # Тут раніше лежали global_settings і user_settings — дублікати
        # конфігу, які писались через /api/v1/settings/* і не читались ніде.
        # Джерела правди: bot_settings (глобальні) та scanner_users
        # (персональні). Ендпоінти тепер ходять напряму туди.


state = AppState()