from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from app.models import Event
from app.timeutils import as_utc


@dataclass(frozen=True, slots=True)
class EventRecord:
    """Plain, DB-independent view of an event, used by rules and features."""

    id: int
    source_id: str
    zone_id: str | None
    timestamp: datetime
    entity_type: str
    entity_id: str | None = None
    confidence: float | None = None
    dwell_seconds: float | None = None
    attributes: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_orm(cls, event: Event) -> "EventRecord":
        return cls(
            id=event.id,
            source_id=event.source_id,
            zone_id=event.zone_id,
            timestamp=as_utc(event.timestamp),
            entity_type=event.entity_type,
            entity_id=event.entity_id,
            confidence=event.confidence,
            dwell_seconds=event.dwell_seconds,
            attributes=event.attributes or {},
        )


SEVERITIES = ("low", "medium", "high", "critical")


def severity_rank(severity: str) -> int:
    return SEVERITIES.index(severity)
