from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    database_url: str = "postgresql+asyncpg://forensic:forensic@db:5432/forensic"
    retention_days: int = 365
    # Pattern & anomaly service, proxied under /anomaly-api for the UI.
    anomaly_url: str = ""


settings = Settings()