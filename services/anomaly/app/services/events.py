from datetime import datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.domain import EventRecord
from app.models import Event


async def load_events(
    session: AsyncSession,
    start: datetime,
    end: datetime,
) -> list[EventRecord]:
    result = await session.scalars(
        select(Event)
        .where(Event.timestamp >= start, Event.timestamp < end)
        .order_by(Event.timestamp),
    )
    return [EventRecord.from_orm(event) for event in result]


async def load_late_events(
    session: AsyncSession,
    ingested_since: datetime,
    before: datetime,
) -> list[EventRecord]:
    """Events that arrived recently but happened before ``before``.

    E.g. a recorded video processed hours later, or a sync backlog.
    """
    result = await session.scalars(
        select(Event)
        .where(Event.ingested_at >= ingested_since, Event.timestamp < before)
        .order_by(Event.timestamp),
    )
    return [EventRecord.from_orm(event) for event in result]
