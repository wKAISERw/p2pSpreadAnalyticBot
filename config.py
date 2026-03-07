import logging
from typing import Optional, Literal
from pydantic_settings import BaseSettings, SettingsConfigDict
from pathlib import Path

# Визначаємо абсолютний шлях до кореня проєкту (там де лежить config.py)
BASE_DIR = Path(__file__).resolve().parent

class Settings(BaseSettings):
    # Telegram налаштування
    telegram_bot_token: str
    telegram_chat_id: int
    wallet_token: str = ""

    telegram_api_id: int  # з my.telegram.org
    telegram_api_hash: str

    # Bybit налаштування
    bybit_base_url: str = "https://api2.bybit.com"
    proxy_url: Optional[str] = None

    # Параметри сканування
    min_spread_pct: float = 0.5  # Повернули 0.5
    safety_buffer_pct: float = 0.3  # Повернули буфер 0.3
    scan_interval_seconds: float = 5.0  # Повернули адекватну паузу (було 2.0)
    working_capital_uah: float = 5100.0
    search_amount_uah: float = 1000.0  # мінімальна сума для пошуку
    search_amounts_uah: list[float] = [1000.0, 2500.0, 5100.0]
    min_usdt_threshold: float = 50.0

    # Кеш і Дедуплікація
    dedup_ttl_seconds: float = 600.0
    dedup_max_size: int = 1000
    dedup_strict: bool = False

    # Фільтр аномалій
    anomaly_threshold_multiplier: float = 2.5
    anomaly_method: Literal["mean", "median", "mad"] = "mean"

    # Risk Engine
    risk_mode: str = "WARNING"  # STRICT або WARNING

    # Логування
    log_level: str = "INFO"

    # Конфігурація Pydantic
    model_config = SettingsConfigDict(
        env_file=str(BASE_DIR / ".env"),  # <--- ТЕПЕР ШЛЯХ АБСОЛЮТНИЙ
        env_file_encoding="utf-8",
        extra="ignore"
    )


settings = Settings()