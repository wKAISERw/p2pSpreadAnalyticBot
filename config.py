"""
config/settings.py — Єдине джерело конфігурації (перенесено з config.py).
Імпортуй звідси: from config.settings import settings
"""
import logging
from typing import Optional, Literal
from pydantic_settings import BaseSettings, SettingsConfigDict
from pathlib import Path

# Корінь проекту — на два рівні вгору від config/settings.py
BASE_DIR = Path(__file__).resolve().parent.parent


class Settings(BaseSettings):
    # Telegram
    telegram_bot_token: str
    telegram_chat_id: int
    wallet_token: str = ""
    telegram_api_id: int
    telegram_api_hash: str

    # Bybit
    bybit_base_url: str = "https://api2.bybit.com"
    proxy_url: Optional[str] = None

    # Сканування
    min_spread_pct: float = 0.5
    safety_buffer_pct: float = 0.3
    scan_interval_seconds: float = 5.0
    working_capital_uah: float = 5100.0
    search_amount_uah: float = 1000.0
    search_amounts_uah: list[float] = [1000.0, 2500.0, 5100.0]
    min_usdt_threshold: float = 50.0

    # Кеш і дедуплікація
    dedup_ttl_seconds: float = 600.0
    dedup_max_size: int = 1000
    dedup_strict: bool = False

    # Фільтр аномалій
    anomaly_threshold_multiplier: float = 2.5
    anomaly_method: Literal["mean", "median", "mad"] = "mean"

    # Risk Engine
    risk_mode: str = "WARNING"

    # Логування
    log_level: str = "INFO"

    # Review fetcher
    review_ttl_hours: float = 24.0

    model_config = SettingsConfigDict(
        env_file=str(BASE_DIR / ".env"),
        env_file_encoding="utf-8",
        extra="ignore",
    )


settings = Settings()