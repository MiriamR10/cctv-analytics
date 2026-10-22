from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.domain import SEVERITIES

Severity = Literal["low", "medium", "high", "critical"]
assert set(Severity.__args__) == set(SEVERITIES)


class EventIn(BaseModel):
    source_id: str = Field(min_length=1, max_length=100)
    zone_id: str | None = Field(default=None, max_length=100)
    timestamp: datetime
    entity_type: str = Field(min_length=1, max_length=50)
    entity_id: str | None = Field(default=None, max_length=100)
    confidence: float | None = Field(default=None, ge=0, le=1)
    dwell_seconds: float | None = Field(default=None, ge=0)
    attributes: dict[str, Any] = {}

    @field_validator("timestamp")
    @classmethod
    def _require_timezone(cls, value: datetime) -> datetime:
        if value.tzinfo is None:
            raise ValueError("timestamp must include a timezone offset")
        return value


class IngestResult(BaseModel):
    inserted: int


class RuleIn(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    rule_type: str
    params: dict[str, Any]
    severity: Severity = "medium"
    enabled: bool = True


class RuleOut(RuleIn):
    model_config = ConfigDict(from_attributes=True)

    id: int
    created_at: datetime


class FindingOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    kind: str
    origin: str
    title: str
    severity: str
    score: float
    occurred_at: datetime
    series_key: str | None
    source_id: str | None
    zone_id: str | None
    entity_id: str | None
    explanation: dict[str, Any]
    event_ids: list[int]
    status: str
    feedback_note: str | None


class FeedbackIn(BaseModel):
    status: Literal["acknowledged", "confirmed", "false_positive"]
    note: str | None = Field(default=None, max_length=2000)


class ModelVersionOut(BaseModel):
    model_config = ConfigDict(from_attributes=True, protected_namespaces=())

    id: int
    detector_class: str
    version: str
    params: dict[str, Any]
    metrics: dict[str, Any]
    trained_from: datetime
    trained_to: datetime
    trained_at: datetime
    is_active: bool


class RunResult(BaseModel):
    new_findings: int


class SyncResult(BaseModel):
    imported: int
