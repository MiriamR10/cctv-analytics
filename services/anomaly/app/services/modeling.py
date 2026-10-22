"""Training and scoring of the pluggable detector. The platform side of the contract."""

import asyncio
import json
import logging
import shutil
from datetime import datetime, timedelta
from pathlib import Path

import pandas as pd
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.detection.base import (
    EXPLANATION,
    IS_ANOMALY,
    LABEL,
    OUTPUT_COLUMNS,
    SCORE,
    SERIES_KEY,
    WINDOW_START,
    AnomalyDetector,
)
from app.detection.registry import resolve_detector_class
from app.features import build_features, split_series_key
from app.models import Finding, ModelVersion
from app.rules.engine import FindingDraft
from app.services.events import load_events
from app.services.findings import store_findings
from app.timeutils import as_utc, floor_to_window, utcnow

logger = logging.getLogger(__name__)

SERIES_FILE = "platform_series.json"
MODEL_SUBDIR = "model"
LABEL_BY_STATUS = {"confirmed": 1, "false_positive": 0}
SEVERITY_BY_SCORE = ((0.8, "critical"), (0.7, "high"), (0.6, "medium"), (0.0, "low"))


class TrainingError(RuntimeError):
    pass


class DetectorOutputError(RuntimeError):
    pass


_cache: dict[int, tuple[AnomalyDetector, list[str]]] = {}


def severity_for(score: float) -> str:
    return next(name for bound, name in SEVERITY_BY_SCORE if score >= bound)


async def _feedback_labels(session: AsyncSession, since: datetime) -> pd.DataFrame:
    rows = (
        await session.execute(
            select(Finding.series_key, Finding.occurred_at, Finding.status).where(
                Finding.kind == "model",
                Finding.status.in_(list(LABEL_BY_STATUS)),
                Finding.occurred_at >= since,
            ),
        )
    ).all()
    return pd.DataFrame(
        {
            SERIES_KEY: [row.series_key for row in rows],
            WINDOW_START: pd.to_datetime(
                [as_utc(row.occurred_at) for row in rows],
                utc=True,
            ),
            LABEL: [LABEL_BY_STATUS[row.status] for row in rows],
        },
    )


async def train(session: AsyncSession, now: datetime | None = None) -> ModelVersion:
    """Fit a new detector on the last ``training_days`` and activate it."""
    now = now or utcnow()
    end = floor_to_window(now, settings.window_minutes)
    start = end - timedelta(days=settings.training_days)

    events = await load_events(session, start, end)
    if not events:
        raise TrainingError("No events in the training period")
    span = end - min(event.timestamp for event in events)
    if span < timedelta(days=settings.min_training_days):
        raise TrainingError(
            f"Only {span.days} days of history; need {settings.min_training_days}",
        )

    features = build_features(events, start, end, settings.window_minutes)
    labels = await _feedback_labels(session, start)
    detector_class = resolve_detector_class(settings.detector_class)
    detector = detector_class(**settings.detector_params)

    metrics = await asyncio.to_thread(detector.fit, features, labels)
    series = sorted(features[SERIES_KEY].unique().tolist())

    trained_at = utcnow()
    artifact = Path(settings.model_dir) / (
        f"{detector.name}-{trained_at:%Y%m%dT%H%M%S}"
    )
    try:
        await asyncio.to_thread(detector.save, artifact / MODEL_SUBDIR)
        (artifact / SERIES_FILE).write_text(json.dumps(series), encoding="utf-8")
    except Exception:
        shutil.rmtree(artifact, ignore_errors=True)
        raise

    await session.execute(update(ModelVersion).values(is_active=False))
    version = ModelVersion(
        detector_class=settings.detector_class,
        version=detector.version,
        artifact_path=str(artifact),
        params=detector.params,
        metrics={**(metrics or {}), "labels_used": int(len(labels))},
        trained_from=start,
        trained_to=end,
        trained_at=trained_at,
        is_active=True,
    )
    session.add(version)
    await session.flush()
    _cache.clear()
    _cache[version.id] = (detector, series)
    logger.info("Activated model version %s (%s)", version.id, artifact)
    return version


async def active_version(session: AsyncSession) -> ModelVersion | None:
    return await session.scalar(
        select(ModelVersion)
        .where(ModelVersion.is_active)
        .order_by(ModelVersion.trained_at.desc())
        .limit(1),
    )


async def _load_active(
    session: AsyncSession,
) -> tuple[ModelVersion, AnomalyDetector, list[str]] | None:
    version = await active_version(session)
    if version is None:
        return None
    if version.id not in _cache:
        artifact = Path(version.artifact_path)
        detector_class = resolve_detector_class(version.detector_class)
        detector = await asyncio.to_thread(detector_class.load, artifact / MODEL_SUBDIR)
        series = json.loads((artifact / SERIES_FILE).read_text(encoding="utf-8"))
        _cache.clear()
        _cache[version.id] = (detector, series)
    detector, series = _cache[version.id]
    return version, detector, series


def validate_output(features: pd.DataFrame, scores: pd.DataFrame) -> None:
    """Enforce the output contract, so a broken model fails loudly, not silently."""
    missing = [column for column in OUTPUT_COLUMNS if column not in scores.columns]
    if missing:
        raise DetectorOutputError(f"Missing output columns: {missing}")
    if len(scores) != len(features):
        raise DetectorOutputError(
            f"Expected {len(features)} rows, got {len(scores)}",
        )
    values = pd.to_numeric(scores[SCORE], errors="coerce")
    if values.isna().any() or ((values < 0) | (values > 1)).any():
        raise DetectorOutputError("score must be a number in [0, 1]")
    if not scores[EXPLANATION].map(lambda item: isinstance(item, dict)).all():
        raise DetectorOutputError("explanation must be a dict for every row")
    expected = set(zip(features[SERIES_KEY], features[WINDOW_START]))
    returned = set(
        zip(scores[SERIES_KEY], pd.to_datetime(scores[WINDOW_START], utc=True))
    )
    if expected != returned:
        raise DetectorOutputError("Output rows do not match input (series, window)")


def _title(explanation: dict) -> str:
    contributions = explanation.get("contributions") or []
    if not contributions:
        return "חריגה משגרה"
    top = contributions[0]
    direction = "עלייה" if top.get("direction") == "above" else "ירידה"
    return (
        f"חריגה משגרה: {direction} ב־{top.get('feature')} "
        f"({top.get('observed')} לעומת {top.get('expected')} צפוי)"
    )


async def score_recent(
    session: AsyncSession,
    now: datetime | None = None,
) -> list[Finding]:
    """Score the most recent closed windows and store anomalies as findings."""
    end = floor_to_window(now or utcnow(), settings.window_minutes)
    start = end - timedelta(minutes=settings.scoring_lookback_minutes)
    return await score_range(session, start, end)


async def score_range(
    session: AsyncSession,
    start: datetime,
    end: datetime,
) -> list[Finding]:
    """Score every window in [start, end) and store anomalies as findings.

    Used for the live cycle (last hour) and for back-scoring history, e.g. right
    after the first training. Re-scoring a range never duplicates findings.
    """
    loaded = await _load_active(session)
    if loaded is None:
        logger.info("No active model; skipping scoring")
        return []
    version, detector, known_series = loaded

    start = floor_to_window(start, settings.window_minutes)
    end = floor_to_window(end, settings.window_minutes)
    if end <= start:
        return []
    events = await load_events(session, start, end)
    features = build_features(
        events,
        start,
        end,
        settings.window_minutes,
        known_series=known_series,
    )
    if features.empty:
        return []

    context = None
    if detector.context_windows > 0:
        context_start = start - timedelta(
            minutes=detector.context_windows * settings.window_minutes,
        )
        context = build_features(
            await load_events(session, context_start, start),
            context_start,
            start,
            settings.window_minutes,
        )

    scores = await asyncio.to_thread(detector.score_with_context, features, context)
    validate_output(features, scores)

    anomalies = scores.loc[scores[IS_ANOMALY].astype(bool)]
    drafts, severities = [], []
    for row in anomalies.itertuples(index=False):
        key = getattr(row, SERIES_KEY)
        window = pd.Timestamp(getattr(row, WINDOW_START)).to_pydatetime()
        source_id, zone_id, entity_type = split_series_key(key)
        explanation = {
            **getattr(row, EXPLANATION),
            "entity_type": entity_type,
            "model_version": version.id,
            "window_minutes": settings.window_minutes,
        }
        score = float(getattr(row, SCORE))
        drafts.append(
            FindingDraft(
                dedup_key=f"model:{key}:{window.isoformat()}",
                title=_title(explanation),
                occurred_at=window,
                score=score,
                source_id=source_id,
                zone_id=zone_id,
                series_key=key,
                explanation=explanation,
            ),
        )
        severities.append(severity_for(score))

    return await store_findings(
        session,
        drafts,
        kind="model",
        origin=f"model:{version.id}",
        severity=severities,
    )


async def activate_version(session: AsyncSession, version_id: int) -> ModelVersion:
    """Rollback / roll forward to a stored version."""
    version = await session.get(ModelVersion, version_id)
    if version is None:
        raise LookupError(f"Model version {version_id} not found")
    await session.execute(update(ModelVersion).values(is_active=False))
    version.is_active = True
    await session.flush()
    _cache.clear()
    return version
