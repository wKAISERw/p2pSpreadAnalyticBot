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
    # Наскільки має підрости спред, щоб про ту саму пару мерчантів сказати
    # ще раз (у відсоткових пунктах). Дедуп ключується парою, а не ціною:
    # у P2P ціна тікає щосекунди, і ключ із ціною робив кожен тік «новим
    # спредом» — той самий CHRØME HEARTS → Saint_Frank летів у чат знову і
    # знову з різницею в сотих. Див. core/engine/alert_dedup.py.
    alert_improvement_pp: float = 0.5
    dedup_strict: bool = False
    # Скільки ордер вважається «вже надісланим». Тейкерський великий
    # навмисно: у чат не має сипатись те саме оголошення щоцикла.
    taker_dedup_ttl: float = 43200.0
    maker_dedup_ttl: float = 300.0

    # ── Темп циклу сканера ────────────────────────────────────────────────
    #
    # Ці значення код читав через getattr із дефолтом, але полів тут не було —
    # тобто задати їх у .env було неможливо, хоч виглядало навпаки. Дефолти
    # лишились ті самі, що стояли в getattr, тож поведінка не змінюється.
    cycle_min_sleep: float = 0.5
    cycle_max_sleep: float = 3.0
    cycle_error_sleep: float = 10.0
    watchdog_interval: float = 30.0

    # ── Circuit breaker ───────────────────────────────────────────────────
    cb_failure_threshold: int = 3
    cb_recovery_timeout: float = 60.0
    # Як часто юзербот CryptoBot оновлює tgWebAppData.
    cb_userbot_interval: float = 45.0

    # ── Фільтр аномалій ───────────────────────────────────────────────────
    #
    # УВАГА: filters/anomaly_filter.py наразі ніде не імпортується, тож ці
    # два значення нікуди не йдуть. Лишені, щоб не ламати .env тих, хто їх
    # уже прописав; підключати фільтр — окреме рішення.
    anomaly_threshold_multiplier: float = 2.5
    anomaly_method: Literal["mean", "median", "mad"] = "mean"

    # ── Risk Engine та Безпека ──────────────────────────────────────────────
    dry_run_mode: bool = False  # Блокує реальні POST/PUT запити на біржі
    public_url: Optional[str] = None
    use_cryptobot_userbot_scraper: bool = False  # Резервний/експериментальний клік-парсер

    # ── HTTP API ──────────────────────────────────────────────────────────
    # Спільний секрет для /api/v1/*. Передається заголовком X-API-Key
    # (або ?api_key= для букмарклета, який не вміє слати заголовки).
    # Порожній рядок = API відкритий; допустимо лише коли порт 8000 нікуди
    # не проброшений. При старті на це буде гучне попередження в лозі.
    api_key: str = ""
    # Дозволені Origin для браузерного фронтенду. "*" разом з увімкненим
    # api_key прийнятний (ключ і так у заголовку), але краще вказати домен.
    cors_origins: list[str] = ["*"]
    # Ключ підпису session-токенів дашборду. Порожньо = похідна від
    # telegram_bot_token (див. api/auth.py). Задавати окремо варто, якщо
    # плануєш міняти токен бота, не розлогінюючи всіх користувачів.
    session_secret: str = ""
    # Чи вважати сам факт наявності X-API-Key достатнім, щоб читати й писати
    # дані будь-якого користувача через ?telegram_id=.
    #
    # За замовчуванням — ні, і це важливо: у типовому деплої ключ підставляє
    # зворотний проксі (deploy/Caddyfile, `header_up X-API-Key`), тобто його
    # отримує КОЖЕН, хто відкрив сайт. Поки фолбек був увімкнений, будь-який
    # відвідувач читав чужі баланси, просто змінивши цифру в URL.
    #
    # Вмикати варто лише коли порт 8000 не проброшений і ключ знає тільки
    # оператор — наприклад, для разових скриптів. Букмарклета це не
    # стосується: /session/receive автентифікується ключем окремо.
    api_key_is_identity: bool = False


    # ── Логування ─────────────────────────────────────────────────────────
    log_level: str = "INFO"
    show_spread_logs: bool = True

    # ── Review Fetcher ────────────────────────────────────────────────────
    review_ttl_hours: float = 24.0

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
    snapshot_retention_hours: int = 24
    snapshot_heartbeat_minutes: int = 10
    db_async_analyze_concurrency: int = 8

    model_config = SettingsConfigDict(
        env_file=str(BASE_DIR / ".env"),
        env_file_encoding="utf-8",
        extra="ignore",
    )


settings = Settings()