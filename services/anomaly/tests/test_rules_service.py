"""Rules against the database: events that arrive after the fact."""

from datetime import timedelta

from app.db import SessionLocal, init_db
from app.models import Event, Rule
from app.services.rules import run_rules
from app.timeutils import utcnow


async def run_with(rule: Rule, event: Event):
    await init_db()
    async with SessionLocal() as session:
        session.add_all([rule, event])
        await session.flush()
        created = await run_rules(session, utcnow())
        rule_id = rule.id
        await session.rollback()
    return [finding for finding in created if finding.origin == f"rule:{rule_id}"]


async def test_watchlist_catches_old_event_that_arrived_late():
    # e.g. a recorded video from this morning, processed now
    rule = Rule(
        name="late watchlist",
        rule_type="watchlist",
        params={"entity_ids": ["77-123-45"]},
        severity="high",
    )
    event = Event(
        source_id="cam-late",
        timestamp=utcnow() - timedelta(hours=6),
        entity_type="vehicle",
        entity_id="7712345",
    )
    assert len(await run_with(rule, event)) == 1


async def test_window_rules_ignore_late_events():
    rule = Rule(
        name="late count",
        rule_type="count_threshold",
        params={
            "window_minutes": 15,
            "operator": "gt",
            "threshold": 0,
            "group_by": "source",
            "scope": {"source_ids": ["cam-late-count"]},
        },
    )
    event = Event(
        source_id="cam-late-count",
        timestamp=utcnow() - timedelta(hours=6),
        entity_type="vehicle",
    )
    assert await run_with(rule, event) == []
