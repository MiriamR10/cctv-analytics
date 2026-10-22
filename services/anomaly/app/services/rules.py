import logging
from datetime import datetime, timedelta

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.models import Finding, Rule
from app.rules.engine import evaluate_rule, required_lookback, validate_rule
from app.services.events import load_events, load_late_events
from app.services.findings import store_findings
from app.timeutils import utcnow

logger = logging.getLogger(__name__)

PER_EVENT_RULES = {"watchlist", "restricted_hours", "max_dwell"}


async def run_rules(
    session: AsyncSession, now: datetime | None = None
) -> list[Finding]:
    """Evaluate all enabled rules over the recent past. Safe to call repeatedly."""
    now = now or utcnow()
    rules = (await session.scalars(select(Rule).where(Rule.enabled))).all()
    if not rules:
        return []

    base = timedelta(minutes=settings.rules_lookback_minutes)
    lookback = max(
        base
        + required_lookback(rule.rule_type, validate_rule(rule.rule_type, rule.params))
        for rule in rules
    )
    events = await load_events(session, now - lookback, now + timedelta(seconds=1))
    # Per-event rules also check events that arrived late. Window rules don't:
    # a partial old window would be miscounted (e.g. a false "too few" alert).
    late = await load_late_events(session, now - base, now - lookback)

    created: list[Finding] = []
    for rule in rules:
        rule_events = late + events if rule.rule_type in PER_EVENT_RULES else events
        try:
            drafts = evaluate_rule(
                rule.id, rule.rule_type, rule.params, rule_events, now
            )
        except Exception:
            logger.exception("Rule %s (%s) failed", rule.id, rule.name)
            continue
        for draft in drafts:
            draft.explanation.setdefault("rule_name", rule.name)
        created += await store_findings(
            session,
            drafts,
            kind="rule",
            origin=f"rule:{rule.id}",
            severity=rule.severity,
        )
    return created
