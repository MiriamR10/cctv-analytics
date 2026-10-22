from collections.abc import Sequence

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Finding
from app.rules.engine import FindingDraft


async def store_findings(
    session: AsyncSession,
    drafts: Sequence[FindingDraft],
    kind: str,
    origin: str,
    severity: str | Sequence[str],
) -> list[Finding]:
    """Insert drafts whose dedup_key is new. Returns only newly created findings.

    ``severity`` is one value for all drafts or one value per draft.
    """
    if not drafts:
        return []
    severities = (
        [severity] * len(drafts) if isinstance(severity, str) else list(severity)
    )
    unique = {}
    for draft, draft_severity in zip(drafts, severities, strict=True):
        unique.setdefault(draft.dedup_key, (draft, draft_severity))

    existing = set(
        (
            await session.scalars(
                select(Finding.dedup_key).where(Finding.dedup_key.in_(list(unique))),
            )
        ).all(),
    )
    created = [
        Finding(
            kind=kind,
            origin=origin,
            dedup_key=draft.dedup_key,
            title=draft.title,
            severity=draft_severity,
            score=draft.score,
            occurred_at=draft.occurred_at,
            series_key=draft.series_key,
            source_id=draft.source_id,
            zone_id=draft.zone_id,
            entity_id=draft.entity_id,
            explanation=draft.explanation,
            event_ids=draft.event_ids,
        )
        for key, (draft, draft_severity) in unique.items()
        if key not in existing
    ]
    session.add_all(created)
    await session.flush()
    return created
