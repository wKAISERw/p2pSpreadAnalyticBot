import logging
from typing import Optional, Literal
from pydantic_settings import BaseSettings, SettingsConfigDict

class Settings(BaseSettings):
    # Telegram налаштування
    telegram_bot_token: str
    telegram_chat_id: int

    # Bybit налаштування
    bybit_base_url: str = "https://api2.bybit.com"
    proxy_url: Optional[str] = None

    # Параметри сканування
    min_spread_pct: float = 0.5  # Повернули 0.5
    safety_buffer_pct: float = 0.3  # Повернули буфер 0.3
    scan_interval_seconds: float = 5.0  # Повернули адекватну паузу (було 2.0)
    working_capital_uah: float = 3000.0  # Перевизначимо у .env на твої 3100
    search_amount_uah: float = 1000.0
    search_amounts_uah: list[float] = [1000.0, 2000.0, 3100.0]
    min_usdt_threshold: float = 50.0

    # Кеш і Дедуплікація
    dedup_ttl_seconds: float = 600.0
    dedup_max_size: int = 1000
    dedup_strict: bool = False

    # Фільтр аномалій
    anomaly_threshold_multiplier: float = 2.5
    anomaly_method: Literal["mean", "median", "mad"] = "mean"

    # Логування
    log_level: str = "INFO"

    # Конфігурація Pydantic
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore"
    )


settings = Settings()