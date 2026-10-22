"""Incremental import of events from the forensic service's tables.

Both services share one Postgres database: forensic owns ``public.events`` and
``public.cameras``; this service keeps its own tables in ``DB_SCHEMA`` (default
``anomaly``). Events are read in keyset order on (created_at, id), so each run
continues where the previous one stopped, and ``external_id`` makes re-reads safe.

Events this service published itself (``payload.source = 'anomaly'``) are skipped,
so alerts never feed back into the model.
"""

import logging
import re
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select, text
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.models import Event, SyncState

logger = logging.getLogger(__name__)

SYNC_NAME = "forensic_events"
OWN_SOURCE = "anomaly"
IDENTIFIER = re.compile(r"^[A-Za-z_][A-Za-z0-9_]{0,62}$")
EPOCH = datetime(1970, 1, 1, tzinfo=UTC)
NIL_UUID = "00000000-0000-0000-0000-000000000000"
# ~10 bind parameters per row; asyncpg allows at most 32767 per statement.
INSERT_CHUNK = 1_000
# forensic object_class / event_type -> entity_type used to group series
ENTITY_TYPE_BY_EVENT = {"vehicle_seen": "vehicle", "person_seen": "person"}


def entity_type_for(event_type: str, object_class: str | None) -> str:
    if event_type in ENTITY_TYPE_BY_EVENT:
        return ENTITY_TYPE_BY_EVENT[event_type]
    return object_class or event_type


def to_event_row(row: Any) -> dict[str, Any]:
    payload = row.payload or {}
    dwell = payload.get("dwell_seconds")
    return {
        "external_id": str(row.id),
        "source_id": row.camera_name,
        "zone_id": row.zone or None,
        "timestamp": row.ts,
        "entity_type": entity_type_for(row.event_type, row.object_class),
        "entity_id": row.plate or row.entity_key,
        "confidence": row.confidence,
        "dwell_seconds": float(dwell) if isinstance(dwell, int | float) else None,
        "attributes": {
            "event_type": row.event_type,
            "severity": row.severity,
            "forensic_camera_id": str(row.camera_id),
            **({"source": payload["source"]} if "source" in payload else {}),
        },
    }


def _query(schema: str) -> str:
    if not IDENTIFIER.match(schema):
        raise ValueError(f"Invalid FORENSIC_SCHEMA {schema!r}")
    return f"""
        SELECT e.id, e.event_type, e.ts, e.camera_id, e.zone, e.entity_key,
               e.plate, e.object_class, e.confidence, e.severity, e.payload,
               e.created_at, c.name AS camera_name
        FROM "{schema}".events e
        JOIN "{schema}".cameras c ON c.id = e.camera_id
        WHERE (e.created_at, e.id) > (:cursor_ts, CAST(:cursor_id AS uuid))
          AND COALESCE(e.payload->>'source', '') <> :own_source
        ORDER BY e.created_at, e.id
        LIMIT :batch
    """


async def sync_once(session: AsyncSession) -> int:
    """Import one batch of new forensic events. Returns how many were read."""
    state = await session.get(SyncState, SYNC_NAME)
    cursor_ts = state.cursor_ts if state else EPOCH
    cursor_id = state.cursor_id if state else NIL_UUID

    rows = (
        await session.execute(
            text(_query(settings.forensic_schema)),
            {
                "cursor_ts": cursor_ts,
                "cursor_id": cursor_id,
                "own_source": OWN_SOURCE,
                "batch": settings.forensic_sync_batch,
            },
        )
    ).all()
    if not rows:
        return 0

    for offset in range(0, len(rows), INSERT_CHUNK):
        chunk = rows[offset : offset + INSERT_CHUNK]
        statement = pg_insert(Event).values([to_event_row(row) for row in chunk])
        await session.execute(
            statement.on_conflict_do_nothing(index_elements=[Event.external_id]),
        )

    last = rows[-1]
    if state is None:
        state = SyncState(name=SYNC_NAME, cursor_ts=last.created_at, cursor_id="")
        session.add(state)
    state.cursor_ts = last.created_at
    state.cursor_id = str(last.id)
    await session.flush()
    return len(rows)


async def sync_all(session: AsyncSession, max_batches: int = 1_000) -> int:
    """Drain everything new (committing after each batch). Returns rows read."""
    total = 0
    for _ in range(max_batches):
        count = await sync_once(session)
        await session.commit()
        total += count
        if count < settings.forensic_sync_batch:
            break
    return total


async def sync_status(session: AsyncSession) -> dict[str, Any]:
    state = await session.get(SyncState, SYNC_NAME)
    imported = await session.scalar(
        select(Event.id).where(Event.external_id.is_not(None)).limit(1),
    )
    return {
        "enabled": settings.forensic_sync,
        "cursor_ts": state.cursor_ts if state else None,
        "updated_at": state.updated_at if state else None,
        "has_imported_events": imported is not None,
    }
