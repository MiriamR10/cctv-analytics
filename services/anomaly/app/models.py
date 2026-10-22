from datetime import datetime
from typing import Any

from sqlalchemy import JSON, BigInteger, Boolean, DateTime, Float, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base
from app.timeutils import utcnow

PK = BigInteger().with_variant(Integer, "sqlite")


class Event(Base):
    """One detection produced by the upstream vision layer (object / plate)."""

    __tablename__ = "events"

    id: Mapped[int] = mapped_column(PK, primary_key=True)
    source_id: Mapped[str] = mapped_column(String(100), index=True)
    zone_id: Mapped[str | None] = mapped_column(String(100), index=True)
    timestamp: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    entity_type: Mapped[str] = mapped_column(String(50), index=True)
    entity_id: Mapped[str | None] = mapped_column(String(100), index=True)
    confidence: Mapped[float | None] = mapped_column(Float)
    dwell_seconds: Mapped[float | None] = mapped_column(Float)
    attributes: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    # Id in the source system (e.g. forensic event UUID), for idempotent sync.
    external_id: Mapped[str | None] = mapped_column(String(64), unique=True)
    ingested_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=utcnow,
        index=True,
    )


class SyncState(Base):
    """Keyset cursor of an incremental sync: last (created_at, id) consumed."""

    __tablename__ = "sync_state"

    name: Mapped[str] = mapped_column(String(50), primary_key=True)
    cursor_ts: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    cursor_id: Mapped[str] = mapped_column(String(64))
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=utcnow,
        onupdate=utcnow,
    )


class Rule(Base):
    """A deterministic, operator-defined rule ("חוקיות מוגדרת")."""

    __tablename__ = "rules"

    id: Mapped[int] = mapped_column(PK, primary_key=True)
    name: Mapped[str] = mapped_column(String(200))
    rule_type: Mapped[str] = mapped_column(String(50))
    params: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    severity: Mapped[str] = mapped_column(String(20), default="medium")
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow
    )


class Finding(Base):
    """Unified output of rules and the anomaly model. Alerts are sent from here."""

    __tablename__ = "findings"

    id: Mapped[int] = mapped_column(PK, primary_key=True)
    kind: Mapped[str] = mapped_column(String(20), index=True)  # "rule" | "model"
    origin: Mapped[str] = mapped_column(String(200))  # "rule:3" / "model:7"
    dedup_key: Mapped[str] = mapped_column(String(400), unique=True)
    title: Mapped[str] = mapped_column(String(400))
    severity: Mapped[str] = mapped_column(String(20), index=True)
    score: Mapped[float] = mapped_column(Float)
    occurred_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    series_key: Mapped[str | None] = mapped_column(String(300), index=True)
    source_id: Mapped[str | None] = mapped_column(String(100))
    zone_id: Mapped[str | None] = mapped_column(String(100))
    entity_id: Mapped[str | None] = mapped_column(String(100), index=True)
    explanation: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    event_ids: Mapped[list[int]] = mapped_column(JSON, default=list)
    status: Mapped[str] = mapped_column(String(20), default="new", index=True)
    feedback_note: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow
    )


class ModelVersion(Base):
    __tablename__ = "model_versions"

    id: Mapped[int] = mapped_column(PK, primary_key=True)
    detector_class: Mapped[str] = mapped_column(String(300))
    version: Mapped[str] = mapped_column(String(50))
    artifact_path: Mapped[str] = mapped_column(String(500))
    params: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    metrics: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    trained_from: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    trained_to: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    trained_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow
    )
    is_active: Mapped[bool] = mapped_column(Boolean, default=False, index=True)
