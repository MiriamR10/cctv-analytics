from typing import Any

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    database_url: str = "sqlite+aiosqlite:///./anomaly.db"
    # Postgres only: keep this service's tables in their own schema, so they can
    # share a database with the forensic service (which also has an "events" table).
    db_schema: str | None = None
    auto_create_tables: bool = True
    timezone: str = "Asia/Jerusalem"
    cors_origins: list[str] = ["http://localhost:5173"]

    # Pluggable model: "package.module:ClassName" + JSON constructor params
    detector_class: str = "app.detection.isolation_forest:IsolationForestDetector"
    detector_params: dict[str, Any] = {}
    model_dir: str = "./model_store"

    # Feature windows
    window_minutes: int = 15
    training_days: int = 28
    min_training_days: int = 7
    scoring_lookback_minutes: int = 60

    # Worker schedule
    rules_poll_seconds: int = 30
    rules_lookback_minutes: int = 10
    scoring_interval_minutes: int = 15
    retrain_hour: int = 2  # local time

    # Alerts
    alert_min_severity: str = "low"
    webhook_url: str | None = None
    webhook_timeout_seconds: float = 10.0

    max_events_per_request: int = 10_000

    # Integration with the CCTV forensic service
    forensic_url: str | None = None  # findings are published there as events
    forensic_sync: bool = False  # pull new events from the forensic tables
    forensic_schema: str = "public"
    forensic_sync_seconds: int = 30
    forensic_sync_batch: int = 5_000
    max_score_range_days: int = 31


settings = Settings()
