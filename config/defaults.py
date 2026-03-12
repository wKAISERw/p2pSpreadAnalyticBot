"""
config/defaults.py — Всі константи проекту з коментарями.
Змінювати тут або через .env (Settings) або через UI бота (RuntimeConfig).
Автоматично згенеровано migrate_to_v2.py — 2026-03-12
"""

# ── Поведінковий аналіз ────────────────────────────────────────────────
BEHAVIOR_HISTORY_MINUTES: int = 60       # глибина history для аналізу
BEHAVIOR_ALERT_SCORE: int = 60           # поріг для логування підозри
BEHAVIOR_LLM_THRESHOLD: int = 60         # поріг для ескалації в LLM

EXACT_LIMITS_SCORE: int = 20
STICKY_LIMITS_SCORE: int = 40
VELOCITY_SPIKE_SCORE: int = 30

STICKY_MIN_CHAIN: int = 3               # мін. кількість snapshot-ів підряд
VELOCITY_MIN_WINDOW_HOURS: float = 0.16  # ~10 хв
VELOCITY_SPIKE_PER_HOUR: float = 20.0

# ── Risk Engine ────────────────────────────────────────────────────────
MIN_ORDERS: dict[str, int] = {
    "Binance": 30, "Bybit": 30, "OKX": 30,
    "Wallet": 10, "MEXC": 10, "CryptoBot": 5,
}
MIN_COMPLETION: dict[str, float] = {
    "Binance": 90.0, "Bybit": 90.0, "OKX": 90.0,
    "Wallet": 85.0, "MEXC": 85.0, "CryptoBot": 80.0,
}
BOT_ALERT_COOLDOWN_SEC: int = 900        # 15 хв між повторними алертами

# ── Regex аналіз ──────────────────────────────────────────────────────
LLM_SCORE_THRESHOLD: int = 30
MIN_MULTI_SIGNAL_SCORE: int = 20

# ── Review Fetcher ─────────────────────────────────────────────────────
REVIEW_TTL_HOURS: float = 24.0
REVIEW_MAX_QUEUE: int = 500
BAD_REVIEW_THRESHOLD_PCT: float = 15.0
REVIEW_WARN_NEG_PCT: float = 15.0
REVIEW_WARN_MIN_NEG: int = 3
REVIEW_BLOCK_NEG_PCT: float = 25.0
REVIEW_BLOCK_MIN_NEG: int = 5
RATE_LIMITS: dict[str, float] = {
    "Binance": 2.0, "Bybit": 1.5, "OKX": 1.5,
}

# ── LLM Worker ────────────────────────────────────────────────────────
LLM_TIMEOUT: float = 20.0
LLM_MAX_QUEUE: int = 100
LLM_WORKERS: int = 2

# ── Identity аналіз ────────────────────────────────────────────────────
LIMIT_EPSILON: float = 0.01             # допуск для порівняння лімітів
IDENTITY_TWIN_MINUTES: int = 15         # вікно пошуку двійників

# ── DB / Storage ──────────────────────────────────────────────────────
SNAPSHOT_RETENTION_HOURS: int = 168     # 7 днів
SNAPSHOT_HEARTBEAT_MINUTES: int = 10
DB_ASYNC_ANALYZE_CONCURRENCY: int = 8

# ── Scanner ────────────────────────────────────────────────────────────
ADAPTIVE_SLEEP_MIN: float = 0.5
ADAPTIVE_SLEEP_MAX: float = 3.0
ADAPTIVE_SLEEP_TARGET: float = 3.0
MAX_ALERTS_PER_CYCLE: int = 5
STABILITY_REQUIRED_HITS: int = 2
STABILITY_TTL_SECONDS: float = 15.0
