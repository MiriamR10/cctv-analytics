import uuid
from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field


class ORM(BaseModel):
    model_config = ConfigDict(from_attributes=True)


class CameraIn(BaseModel):
    name: str
    zone: str
    retained_days: int | None = None


class CameraOut(ORM):
    id: uuid.UUID
    name: str
    zone: str
    retained_days: int | None


class SegmentIn(BaseModel):
    camera_id: uuid.UUID
    start_ts: datetime
    end_ts: datetime
    s3_key: str
    sha256: str
    duration_s: float = 0.0


class SegmentOut(ORM):
    id: uuid.UUID
    camera_id: uuid.UUID
    start_ts: datetime
    end_ts: datetime
    s3_key: str
    sha256: str
    duration_s: float


class EventIn(BaseModel):
    event_type: str
    ts: datetime
    camera_id: uuid.UUID
    zone: str = ""
    entity_key: str | None = None
    plate: str | None = None
    object_class: str | None = None
    confidence: float = 0.0
    severity: Literal["info", "low", "medium", "high", "critical"] = "info"
    payload: dict[str, Any] | None = None


class EventOut(ORM):
    id: uuid.UUID
    event_type: str
    ts: datetime
    camera_id: uuid.UUID
    zone: str
    entity_key: str | None
    plate: str | None
    object_class: str | None
    confidence: float
    severity: str
    payload: dict | None


class SearchQuery(BaseModel):
    ts_from: datetime | None = None
    ts_to: datetime | None = None
    camera_ids: list[uuid.UUID] | None = None
    zones: list[str] | None = None
    event_types: list[str] | None = None
    object_class: str | None = None
    plate: str | None = None
    entity_key: str | None = None
    min_confidence: float | None = None
    limit: int = Field(default=50, le=500)
    offset: int = 0
    order: Literal["asc", "desc"] = "desc"


class SearchResult(BaseModel):
    total: int
    items: list[EventOut]
    by_type: dict[str, int] = {}
    by_camera: dict[str, int] = {}


class TimelineQuery(BaseModel):
    ts_from: datetime
    ts_to: datetime
    camera_ids: list[uuid.UUID] | None = None
    zones: list[str] | None = None
    entity_key: str | None = None


class TimelineItem(BaseModel):
    ts: datetime
    camera_name: str
    zone: str
    event_type: str
    entity_key: str | None
    plate: str | None


class TimelineOut(BaseModel):
    items: list[TimelineItem]
    tracks: dict[str, list[TimelineItem]] = {}
    gaps: list[dict] = []


class Window(BaseModel):
    ts_from: datetime
    ts_to: datetime


class CompareIn(BaseModel):
    mode: Literal["event_vs_event", "period_vs_period", "vs_baseline"]
    left_window: Window | None = None
    right_window: Window | None = None
    target_window: Window | None = None
    baseline_window: Window | None = None
    left_entity: str | None = None
    right_entity: str | None = None
    zones: list[str] | None = None


class CompareOut(BaseModel):
    mode: str
    left: dict[str, Any]
    right: dict[str, Any]
    deltas: dict[str, Any]
    verdict: str
    summary: str