from datetime import datetime, timedelta
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.db import get_session
from app.detection.registry import DetectorLoadError
from app.models import Event, Finding, ModelVersion, Rule
from app.rules.engine import RULE_PARAMS, RuleValidationError, validate_rule
from app.schemas import (
    EventIn,
    FeedbackIn,
    FindingOut,
    IngestResult,
    ModelVersionOut,
    RuleIn,
    RuleOut,
    RunResult,
    SyncResult,
)
from app.services import alerts, forensic_sync, modeling
from app.services.rules import run_rules
from app.timeutils import utcnow

router = APIRouter()
Session = Annotated[AsyncSession, Depends(get_session)]


# --- events ------------------------------------------------------------------------


@router.post("/events", response_model=IngestResult, tags=["events"])
async def ingest_events(events: list[EventIn], session: Session) -> IngestResult:
    if len(events) > settings.max_events_per_request:
        raise HTTPException(413, f"Max {settings.max_events_per_request} events")
    session.add_all(Event(**event.model_dump()) for event in events)
    await session.commit()
    return IngestResult(inserted=len(events))


# --- rules -------------------------------------------------------------------------


@router.get("/rules/types", tags=["rules"])
async def rule_types() -> dict:
    """JSON schema of every rule type, for building the rule editor UI."""
    return {name: schema.model_json_schema() for name, schema in RULE_PARAMS.items()}


@router.get("/rules", response_model=list[RuleOut], tags=["rules"])
async def list_rules(session: Session) -> list[Rule]:
    return list((await session.scalars(select(Rule).order_by(Rule.id))).all())


@router.post("/rules", response_model=RuleOut, status_code=201, tags=["rules"])
async def create_rule(rule: RuleIn, session: Session) -> Rule:
    params = _validated_params(rule)
    created = Rule(**rule.model_dump(exclude={"params"}), params=params)
    session.add(created)
    await session.commit()
    return created


@router.put("/rules/{rule_id}", response_model=RuleOut, tags=["rules"])
async def update_rule(rule_id: int, rule: RuleIn, session: Session) -> Rule:
    existing = await _get_or_404(session, Rule, rule_id)
    params = _validated_params(rule)
    for key, value in rule.model_dump(exclude={"params"}).items():
        setattr(existing, key, value)
    existing.params = params
    await session.commit()
    return existing


@router.delete("/rules/{rule_id}", status_code=204, tags=["rules"])
async def delete_rule(rule_id: int, session: Session) -> None:
    await session.delete(await _get_or_404(session, Rule, rule_id))
    await session.commit()


def _validated_params(rule: RuleIn) -> dict:
    try:
        return validate_rule(rule.rule_type, rule.params).model_dump(mode="json")
    except RuleValidationError as error:
        raise HTTPException(422, str(error)) from error


# --- findings ----------------------------------------------------------------------


@router.get("/findings", response_model=list[FindingOut], tags=["findings"])
async def list_findings(
    session: Session,
    kind: str | None = None,
    severity: str | None = None,
    status: str | None = None,
    source_id: str | None = None,
    zone_id: str | None = None,
    entity_id: str | None = None,
    since: datetime | None = None,
    until: datetime | None = None,
    limit: Annotated[int, Query(ge=1, le=1000)] = 100,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> list[Finding]:
    query = select(Finding)
    filters = {
        Finding.kind: kind,
        Finding.severity: severity,
        Finding.status: status,
        Finding.source_id: source_id,
        Finding.zone_id: zone_id,
        Finding.entity_id: entity_id,
    }
    for column, value in filters.items():
        if value is not None:
            query = query.where(column == value)
    if since:
        query = query.where(Finding.occurred_at >= since)
    if until:
        query = query.where(Finding.occurred_at < until)
    query = query.order_by(Finding.occurred_at.desc()).limit(limit).offset(offset)
    return list((await session.scalars(query)).all())


@router.post(
    "/findings/{finding_id}/feedback",
    response_model=FindingOut,
    tags=["findings"],
)
async def finding_feedback(
    finding_id: int,
    feedback: FeedbackIn,
    session: Session,
) -> Finding:
    """Operator feedback. confirmed / false_positive are passed to the next training."""
    finding = await _get_or_404(session, Finding, finding_id)
    finding.status = feedback.status
    finding.feedback_note = feedback.note
    await session.commit()
    return finding


# --- model -------------------------------------------------------------------------


@router.get("/model/versions", response_model=list[ModelVersionOut], tags=["model"])
async def model_versions(session: Session) -> list[ModelVersion]:
    query = select(ModelVersion).order_by(ModelVersion.trained_at.desc())
    return list((await session.scalars(query)).all())


@router.post("/model/train", response_model=ModelVersionOut, tags=["model"])
async def train_model(session: Session) -> ModelVersion:
    try:
        version = await modeling.train(session)
    except (modeling.TrainingError, DetectorLoadError) as error:
        raise HTTPException(409, str(error)) from error
    await session.commit()
    return version


@router.post(
    "/model/versions/{version_id}/activate",
    response_model=ModelVersionOut,
    tags=["model"],
)
async def activate_model(version_id: int, session: Session) -> ModelVersion:
    try:
        version = await modeling.activate_version(session, version_id)
    except LookupError as error:
        raise HTTPException(404, str(error)) from error
    await session.commit()
    return version


# --- manual runs (the worker does this on a schedule) --------------------------------


@router.post("/run/rules", response_model=RunResult, tags=["run"])
async def run_rules_now(session: Session) -> RunResult:
    created = await run_rules(session)
    await session.commit()
    await alerts.dispatch(created, alerts.default_channels())
    return RunResult(new_findings=len(created))


@router.post("/run/score", response_model=RunResult, tags=["run"])
async def run_scoring_now(
    session: Session,
    since: datetime | None = None,
    until: datetime | None = None,
) -> RunResult:
    """Score the last hour, or every window in [since, until) (back-scoring)."""
    try:
        if since is None:
            created = await modeling.score_recent(session)
        else:
            until = until or utcnow()
            if until - since > timedelta(days=settings.max_score_range_days):
                raise HTTPException(
                    422,
                    f"Range longer than {settings.max_score_range_days} days",
                )
            created = await modeling.score_range(session, since, until)
    except modeling.DetectorOutputError as error:
        raise HTTPException(
            500, f"Detector broke the output contract: {error}"
        ) from error
    await session.commit()
    await alerts.dispatch(created, alerts.default_channels())
    return RunResult(new_findings=len(created))


@router.post("/run/sync", response_model=SyncResult, tags=["run"])
async def run_sync_now(session: Session) -> SyncResult:
    """Import new events from the forensic service (Postgres only)."""
    if not settings.database_url.startswith("postgresql"):
        raise HTTPException(409, "Forensic sync requires the shared Postgres database")
    imported = await forensic_sync.sync_all(session)
    return SyncResult(imported=imported)


@router.get("/sync/status", tags=["run"])
async def sync_status(session: Session) -> dict:
    if not settings.database_url.startswith("postgresql"):
        return {"enabled": False}
    return await forensic_sync.sync_status(session)


async def _get_or_404(session: AsyncSession, model, item_id: int):
    item = await session.get(model, item_id)
    if item is None:
        raise HTTPException(404, f"{model.__name__} {item_id} not found")
    return item
