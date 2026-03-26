"""
config/defaults.py — Всі константи проекту з коментарями.
Змінювати тут або через .env (Settings) або через UI бота (RuntimeConfig).
"""

# ── Поведінковий аналіз ────────────────────────────────────────────────
BEHAVIOR_HISTORY_MINUTES: int = 60
BEHAVIOR_ALERT_SCORE: int = 60
BEHAVIOR_LLM_THRESHOLD: int = 60

EXACT_LIMITS_SCORE: int = 20
STICKY_LIMITS_SCORE: int = 40
VELOCITY_SPIKE_SCORE: int = 30

STICKY_MIN_CHAIN: int = 3
VELOCITY_MIN_WINDOW_HOURS: float = 0.16
VELOCITY_SPIKE_PER_HOUR: float = 20.0

# ── Risk Engine ────────────────────────────────────────────────────────
MIN_ORDERS: dict[str, int] = {
    "Binance": 50, "Bybit": 30, "OKX": 30,
    "Wallet": 10, "MEXC": 20, "CryptoBot": 15,
}
MIN_COMPLETION: dict[str, float] = {
    "Binance": 95.0, "Bybit": 92.0, "OKX": 92.0,
    "Wallet": 88.0, "MEXC": 90.0, "CryptoBot": 85.0,
}
TRUSTED_MIN_ORDERS: int = 500
TRUSTED_MIN_COMPLETION: float = 95.0
TRUSTED_MAX_RISK_SCORE: int = 30
TRUSTED_LLM_MIN_SCORE: int = 60
BOT_ALERT_COOLDOWN_SEC: int = 900
DB_ASYNC_ANALYZE_CONCURRENCY: int = 8

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
LLM_WORKERS: int = 3

# ── Identity аналіз ────────────────────────────────────────────────────
LIMIT_EPSILON: float = 0.01
IDENTITY_TWIN_MINUTES: int = 15

# ── DB / Storage ──────────────────────────────────────────────────────
SNAPSHOT_RETENTION_HOURS: int = 24
SNAPSHOT_HEARTBEAT_MINUTES: int = 10

# ── Scanner ────────────────────────────────────────────────────────────
ADAPTIVE_SLEEP_MIN: float = 0.5
ADAPTIVE_SLEEP_MAX: float = 3.0
ADAPTIVE_SLEEP_TARGET: float = 3.0
MAX_ALERTS_PER_CYCLE: int = 5
STABILITY_REQUIRED_HITS: int = 2
STABILITY_TTL_SECONDS: float = 15.0