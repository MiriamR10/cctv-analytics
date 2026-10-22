"""Deterministic rule evaluation. Pure functions over EventRecords, no DB access.

Every evaluator returns ``FindingDraft``s with a stable ``dedup_key``, so evaluating
overlapping lookback windows never creates duplicate findings.
"""

from collections import defaultdict
from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass, field
from datetime import datetime, time, timedelta
from typing import Any, Literal

from pydantic import BaseModel, Field, ValidationError, model_validator

from app.domain import EventRecord
from app.timeutils import floor_to_window, local_tz


@dataclass(slots=True)
class FindingDraft:
    dedup_key: str
    title: str
    occurred_at: datetime
    score: float = 1.0
    source_id: str | None = None
    zone_id: str | None = None
    entity_id: str | None = None
    series_key: str | None = None
    explanation: dict[str, Any] = field(default_factory=dict)
    event_ids: list[int] = field(default_factory=list)


# --- shared filters ----------------------------------------------------------------


class Scope(BaseModel):
    """Restrict a rule to some sources / zones / entity types. Empty = all."""

    source_ids: list[str] = []
    zone_ids: list[str] = []
    entity_types: list[str] = []

    def matches(self, event: EventRecord) -> bool:
        return (
            (not self.source_ids or event.source_id in self.source_ids)
            and (not self.zone_ids or event.zone_id in self.zone_ids)
            and (not self.entity_types or event.entity_type in self.entity_types)
        )


class TimeRange(BaseModel):
    """Local-time range. ``start > end`` wraps midnight (e.g. 22:00-06:00)."""

    start: time
    end: time
    days_of_week: list[int] = Field(default=[], description="0=Mon..6=Sun; empty=all")

    def contains(self, moment: datetime) -> bool:
        local = moment.astimezone(local_tz())
        current = local.time()
        if self.start <= self.end:
            inside = self.start <= current < self.end
            day = local.weekday()
        else:
            inside = current >= self.start or current < self.end
            # a night range belongs to the day it started on
            day = (
                local.weekday() if current >= self.start else (local.weekday() - 1) % 7
            )
        return inside and (not self.days_of_week or day in self.days_of_week)


# --- rule parameter schemas --------------------------------------------------------


class WatchlistParams(BaseModel):
    entity_ids: list[str] = Field(min_length=1)
    scope: Scope = Scope()


class RestrictedHoursParams(BaseModel):
    """Alert on any matching event inside the time range ("outside allowed hours")."""

    time_range: TimeRange
    scope: Scope = Scope()


class MaxDwellParams(BaseModel):
    max_seconds: float = Field(gt=0)
    scope: Scope = Scope()


class CountThresholdParams(BaseModel):
    """Count events (or distinct entities) per fixed window and compare to a bound."""

    window_minutes: int = Field(gt=0)
    operator: Literal["gt", "lt"] = "gt"
    threshold: int = Field(ge=0)
    count_distinct_entities: bool = False
    group_by: Literal["source", "zone"] = "zone"
    scope: Scope = Scope()

    @model_validator(mode="after")
    def _lt_needs_scope(self) -> "CountThresholdParams":
        if self.operator == "lt" and not (self.scope.source_ids or self.scope.zone_ids):
            raise ValueError(
                "operator 'lt' requires scope.source_ids or scope.zone_ids"
            )
        return self


class EntityFrequencyParams(BaseModel):
    """Same entity (e.g. plate) seen at least ``min_occurrences`` times in a period."""

    period_minutes: int = Field(gt=0)
    min_occurrences: int = Field(ge=2)
    scope: Scope = Scope()


RULE_PARAMS: dict[str, type[BaseModel]] = {
    "watchlist": WatchlistParams,
    "restricted_hours": RestrictedHoursParams,
    "max_dwell": MaxDwellParams,
    "count_threshold": CountThresholdParams,
    "entity_frequency": EntityFrequencyParams,
}


class RuleValidationError(ValueError):
    pass


def validate_rule(rule_type: str, params: dict[str, Any]) -> BaseModel:
    schema = RULE_PARAMS.get(rule_type)
    if schema is None:
        raise RuleValidationError(
            f"Unknown rule_type {rule_type!r}. Allowed: {sorted(RULE_PARAMS)}",
        )
    try:
        return schema.model_validate(params)
    except ValidationError as error:
        raise RuleValidationError(str(error)) from error


def required_lookback(rule_type: str, params: BaseModel) -> timedelta:
    if isinstance(params, CountThresholdParams):
        return timedelta(minutes=params.window_minutes)
    if isinstance(params, EntityFrequencyParams):
        return timedelta(minutes=params.period_minutes)
    return timedelta()


# --- evaluators --------------------------------------------------------------------


def _norm_entity(value: str) -> str:
    """Same normalisation forensic applies to plates: '12-345-67' == '1234567'."""
    return "".join(value.split()).replace("-", "").replace(".", "").upper()


def _watchlist(rule_id: int, p: WatchlistParams, events, now) -> list[FindingDraft]:
    wanted = {_norm_entity(entity) for entity in p.entity_ids}
    return [
        FindingDraft(
            dedup_key=f"rule:{rule_id}:event:{event.id}",
            title=f"זוהה פריט מרשימת מעקב: {event.entity_id}",
            occurred_at=event.timestamp,
            source_id=event.source_id,
            zone_id=event.zone_id,
            entity_id=event.entity_id,
            explanation={"reason": "watchlist_match", "entity_id": event.entity_id},
            event_ids=[event.id],
        )
        for event in events
        if p.scope.matches(event)
        and event.entity_id
        and _norm_entity(event.entity_id) in wanted
    ]


def _restricted_hours(
    rule_id: int,
    p: RestrictedHoursParams,
    events,
    now,
) -> list[FindingDraft]:
    return [
        FindingDraft(
            dedup_key=f"rule:{rule_id}:event:{event.id}",
            title=f"פעילות בשעות אסורות ({event.entity_type})",
            occurred_at=event.timestamp,
            source_id=event.source_id,
            zone_id=event.zone_id,
            entity_id=event.entity_id,
            explanation={
                "reason": "restricted_hours",
                "range": f"{p.time_range.start:%H:%M}-{p.time_range.end:%H:%M}",
            },
            event_ids=[event.id],
        )
        for event in events
        if p.scope.matches(event) and p.time_range.contains(event.timestamp)
    ]


def _max_dwell(rule_id: int, p: MaxDwellParams, events, now) -> list[FindingDraft]:
    return [
        FindingDraft(
            dedup_key=f"rule:{rule_id}:event:{event.id}",
            title=f"שהייה חריגה: {event.dwell_seconds:.0f} שניות",
            occurred_at=event.timestamp,
            score=min(1.0, event.dwell_seconds / (2 * p.max_seconds)),
            source_id=event.source_id,
            zone_id=event.zone_id,
            entity_id=event.entity_id,
            explanation={
                "reason": "max_dwell_exceeded",
                "observed": event.dwell_seconds,
                "limit": p.max_seconds,
            },
            event_ids=[event.id],
        )
        for event in events
        if p.scope.matches(event)
        and event.dwell_seconds is not None
        and event.dwell_seconds > p.max_seconds
    ]


def _count_threshold(
    rule_id: int,
    p: CountThresholdParams,
    events,
    now,
) -> list[FindingDraft]:
    def group_of(event: EventRecord) -> str:
        return event.source_id if p.group_by == "source" else (event.zone_id or "-")

    buckets: dict[tuple[str, datetime], list[EventRecord]] = defaultdict(list)
    for event in events:
        if p.scope.matches(event):
            window = floor_to_window(event.timestamp, p.window_minutes)
            buckets[(group_of(event), window)].append(event)

    if p.operator == "lt":
        # Only closed windows, and also windows with zero events.
        targets = p.scope.source_ids if p.group_by == "source" else p.scope.zone_ids
        last_closed = floor_to_window(now, p.window_minutes) - timedelta(
            minutes=p.window_minutes,
        )
        earliest = min((event.timestamp for event in events), default=last_closed)
        window = max(floor_to_window(earliest, p.window_minutes), last_closed)
        for target in targets:
            buckets.setdefault((target, window), [])
        buckets = {
            key: value
            for key, value in buckets.items()
            if key[1] <= last_closed and key[0] in targets
        }

    drafts = []
    for (group, window), bucket in buckets.items():
        value = (
            len({event.entity_id for event in bucket if event.entity_id})
            if p.count_distinct_entities
            else len(bucket)
        )
        violated = value > p.threshold if p.operator == "gt" else value < p.threshold
        if not violated:
            continue
        sample = bucket[0] if bucket else None
        drafts.append(
            FindingDraft(
                dedup_key=f"rule:{rule_id}:{group}:{window.isoformat()}",
                title=(
                    f"{'עומס' if p.operator == 'gt' else 'מיעוט פעילות'}: "
                    f"{value} ב־{p.window_minutes} דקות"
                ),
                occurred_at=window,
                source_id=group
                if p.group_by == "source"
                else (sample.source_id if sample else None),
                zone_id=group if p.group_by == "zone" else None,
                explanation={
                    "reason": "count_threshold",
                    "observed": value,
                    "operator": p.operator,
                    "threshold": p.threshold,
                    "window_start": window.isoformat(),
                },
                event_ids=[event.id for event in bucket][:100],
            ),
        )
    return drafts


def _entity_frequency(
    rule_id: int,
    p: EntityFrequencyParams,
    events,
    now,
) -> list[FindingDraft]:
    by_entity: dict[str, list[EventRecord]] = defaultdict(list)
    for event in events:
        if p.scope.matches(event) and event.entity_id:
            by_entity[_norm_entity(event.entity_id)].append(event)

    period = timedelta(minutes=p.period_minutes)
    drafts = []
    for entity_id, seen in by_entity.items():
        seen.sort(key=lambda event: event.timestamp)
        recent = [event for event in seen if event.timestamp > now - period]
        if len(recent) < p.min_occurrences:
            continue
        bucket = floor_to_window(recent[-1].timestamp, p.period_minutes)
        drafts.append(
            FindingDraft(
                dedup_key=f"rule:{rule_id}:{entity_id}:{bucket.isoformat()}",
                title=f"הופעה חוזרת: {entity_id} ({len(recent)} פעמים)",
                occurred_at=recent[-1].timestamp,
                entity_id=entity_id,
                source_id=recent[-1].source_id,
                zone_id=recent[-1].zone_id,
                explanation={
                    "reason": "entity_frequency",
                    "observed": len(recent),
                    "min_occurrences": p.min_occurrences,
                    "period_minutes": p.period_minutes,
                },
                event_ids=[event.id for event in recent][:100],
            ),
        )
    return drafts


Evaluator = Callable[[int, Any, Sequence[EventRecord], datetime], list[FindingDraft]]
EVALUATORS: dict[str, Evaluator] = {
    "watchlist": _watchlist,
    "restricted_hours": _restricted_hours,
    "max_dwell": _max_dwell,
    "count_threshold": _count_threshold,
    "entity_frequency": _entity_frequency,
}


def evaluate_rule(
    rule_id: int,
    rule_type: str,
    params: dict[str, Any],
    events: Iterable[EventRecord],
    now: datetime,
) -> list[FindingDraft]:
    parsed = validate_rule(rule_type, params)
    return EVALUATORS[rule_type](rule_id, parsed, list(events), now)
