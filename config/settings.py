"""
config/settings.py — Єдине джерело конфігурації.
Оригінальний Settings з config.py + нові поля для v2.
"""
from typing import Optional, Literal
from pydantic_settings import BaseSettings, SettingsConfigDict
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent  # корінь проекту


class Settings(BaseSettings):
    # ── Telegram ──────────────────────────────────────────────────────────
    telegram_bot_token: str
    telegram_chat_id: int
    wallet_token: str = ""
    telegram_api_id: int
    telegram_api_hash: str
    admin_id: int = 0

    # ── LLM ───────────────────────────────────────────────────────────────
    groq_api_key: str = ""
    gemini_api_key: str = ""

    # ── Bybit ─────────────────────────────────────────────────────────────
    bybit_base_url: str = "https://api2.bybit.com"
    proxy_url: Optional[str] = None

    # ── Сканування ────────────────────────────────────────────────────────
    min_spread_pct: float = 0.5
    safety_buffer_pct: float = 0.3
    scan_interval_seconds: float = 5.0
    working_capital_uah: float = 5100.0
    search_amount_uah: float = 1000.0
    search_amounts_uah: list[float] = [1000.0, 2500.0, 5100.0]
    min_usdt_threshold: float = 50.0

    # ── Кеш і дедуплікація ────────────────────────────────────────────────
    dedup_ttl_seconds: float = 600.0
    dedup_max_size: int = 1000
    dedup_strict: bool = False

    # ── Фільтр аномалій ───────────────────────────────────────────────────
    anomaly_threshold_multiplier: float = 2.5
    anomaly_method: Literal["mean", "median", "mad"] = "mean"

    # ── Risk Engine ───────────────────────────────────────────────────────
    risk_mode: str = "WARNING"

    # ── Логування ─────────────────────────────────────────────────────────
    log_level: str = "INFO"

    # ── Review Fetcher ────────────────────────────────────────────────────
    review_ttl_hours: float = 24.0
    review_max_queue: int = 500

    # ── Behavioral (Deep Research) ────────────────────────────────────────
    behavior_history_minutes: int = 60
    behavior_alert_score: int = 60
    sticky_min_chain: int = 3
    velocity_spike_per_hour: float = 20.0

    # ── Alerts ────────────────────────────────────────────────────────────
    max_alerts_per_cycle: int = 5
    stability_required_hits: int = 2
    stability_ttl_seconds: float = 15.0

    # ── DB ────────────────────────────────────────────────────────────────
    snapshot_retention_hours: int = 168
    snapshot_heartbeat_minutes: int = 10
    db_async_analyze_concurrency: int = 8

    model_config = SettingsConfigDict(
        env_file=str(BASE_DIR / ".env"),
        env_file_encoding="utf-8",
        extra="ignore",
    )


settings = Settings()