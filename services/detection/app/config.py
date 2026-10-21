
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    storage_dir: Path = Path("./data")
    database_path: Path = Path("./data/cctv_analytics.db")

    detector: str = "opencv_hog"
    yolo_model_path: str = "/models/approved-model.pt"

    max_upload_mb: int = 500

    model_config = SettingsConfigDict(
        env_file=".env",
        extra="ignore",
    )
    detection_confidence: float = 0.35
    detection_classes: str = ""

    # Default camera for events sent to the forensic service
    camera_name: str = "Detection camera"
    camera_zone: str = "general"


settings = Settings()

settings.storage_dir.mkdir(parents=True, exist_ok=True)

for folder in ("uploads", "results"):
    (settings.storage_dir / folder).mkdir(
        parents=True,
        exist_ok=True,
    )

settings.database_path.parent.mkdir(
    parents=True,
    exist_ok=True,
)
