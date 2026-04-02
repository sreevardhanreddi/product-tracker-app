from typing import Optional

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env", env_file_encoding="utf-8", extra="ignore"
    )

    # Core
    APP_ENV: str = "development"
    SECRET_KEY: str = "changeme"
    LOG_LEVEL: str = "INFO"

    # Database
    DATABASE_URL: str = "postgresql://tracker:tracker@localhost:5432/tracker"

    # Redis / Celery
    REDIS_URL: str = "redis://localhost:6379/0"
    CELERY_BROKER_URL: str = "redis://localhost:6379/0"
    CELERY_RESULT_BACKEND: str = "redis://localhost:6379/1"

    # Price check schedule
    PRICE_CHECK_INTERVAL_MINUTES: int = 60
    PRICE_CHANGE_NOTIFY_MIN_PERCENT: float = 1.0

    # Alerts (optional — falls back to logging if unset)
    GMAIL_USER: Optional[str] = None
    GMAIL_PASSWORD: Optional[str] = None
    ALERT_TO_EMAIL: Optional[str] = None

    TELEGRAM_BOT_TOKEN: Optional[str] = None
    TELEGRAM_CHAT_ID: Optional[str] = None

    # Sentry
    SENTRY_DSN: Optional[str] = None

    # Playwright
    PLAYWRIGHT_HEADLESS: bool = True
    PLAYWRIGHT_TIMEOUT_MS: int = 30000


settings = Settings()
