from datetime import UTC, datetime
from types import SimpleNamespace

from sqlalchemy.dialects import postgresql

from app.models import Finding
from app.services.forensic_publish import finding_to_event
from app.services.forensic_sync import entity_type_for, sync_once, to_event_row

TS = datetime(2026, 10, 21, 8, 0, tzinfo=UTC)


def forensic_row(**overrides):
    values = dict(
        id="4f1c2b9e-0000-4000-8000-000000000001",
        event_type="vehicle_seen",
        ts=TS,
        camera_id="c0ffee00-0000-4000-8000-000000000002",
        zone="שער",
        entity_key="12345678",
        plate="12345678",
        object_class="vehicle",
        confidence=0.91,
        severity="info",
        payload={"source": "lpr", "dwell_seconds": 42},
        created_at=TS,
        camera_name="שער כניסה דרומי",
    )
    return SimpleNamespace(**{**values, **overrides})


def test_vehicle_event_maps_to_platform_event():
    row = to_event_row(forensic_row())
    assert row["external_id"] == "4f1c2b9e-0000-4000-8000-000000000001"
    assert row["source_id"] == "שער כניסה דרומי"
    assert row["zone_id"] == "שער"
    assert row["entity_type"] == "vehicle"
    assert row["entity_id"] == "12345678"
    assert row["dwell_seconds"] == 42.0
    assert row["attributes"]["event_type"] == "vehicle_seen"


def test_missing_plate_falls_back_to_entity_key_and_empty_zone_to_none():
    row = to_event_row(
        forensic_row(plate=None, entity_key="person-007", zone="", payload=None),
    )
    assert row["entity_id"] == "person-007"
    assert row["zone_id"] is None
    assert row["dwell_seconds"] is None


def test_entity_type_groups_series():
    assert entity_type_for("person_seen", None) == "person"
    assert entity_type_for("object_seen", "backpack") == "backpack"
    assert entity_type_for("crowd", None) == "crowd"


def test_finding_becomes_forensic_event():
    finding = Finding(
        id=7,
        kind="model",
        origin="model:1",
        dedup_key="k",
        title="חריגה משגרה",
        severity="high",
        score=0.74,
        occurred_at=TS,
        series_key="שער כניסה דרומי|שער|vehicle",
        source_id="שער כניסה דרומי",
        zone_id="שער",
        entity_id=None,
        explanation={"entity_type": "vehicle", "contributions": []},
    )
    event = finding_to_event(finding, camera_id="cam-uuid")
    assert event["event_type"] == "anomaly"
    assert event["camera_id"] == "cam-uuid"
    assert event["severity"] == "high"
    assert event["entity_key"] == "שער כניסה דרומי|שער|vehicle"
    assert event["payload"]["source"] == "anomaly"  # never re-imported by the sync
    assert event["payload"]["finding_id"] == 7


class FakeSession:
    """Returns ``rows`` for the forensic SELECT and records every INSERT."""

    def __init__(self, rows):
        self.rows = rows
        self.inserts = []

    async def get(self, *_):
        return None

    async def execute(self, statement, params=None):
        if params is not None:  # the forensic SELECT
            return SimpleNamespace(all=lambda: self.rows)
        self.inserts.append(statement)

    def add(self, _):
        pass

    async def flush(self):
        pass


async def test_large_batch_is_inserted_in_chunks_under_postgres_limit():
    rows = [
        forensic_row(id=f"00000000-0000-4000-8000-{index:012d}")
        for index in range(2_500)
    ]
    session = FakeSession(rows)

    assert await sync_once(session) == 2_500
    assert len(session.inserts) == 3
    for statement in session.inserts:
        bind_count = len(statement.compile(dialect=postgresql.dialect()).params)
        assert bind_count <= 32_767  # asyncpg hard limit per statement
