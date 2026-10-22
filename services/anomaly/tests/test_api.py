"""End-to-end through the HTTP API with the reference detector and SQLite."""

from datetime import UTC, datetime, timedelta

import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.services import modeling
from app.synthetic import generate_events


@pytest.fixture(scope="module")
def client():
    with TestClient(app) as test_client:
        yield test_client


def to_payload(events):
    return [
        {
            "source_id": e.source_id,
            "zone_id": e.zone_id,
            "timestamp": e.timestamp.isoformat(),
            "entity_type": e.entity_type,
            "entity_id": e.entity_id,
            "confidence": e.confidence,
            "dwell_seconds": e.dwell_seconds,
        }
        for e in events
    ]


def test_rejects_naive_timestamp(client):
    response = client.post(
        "/events",
        json=[
            {"source_id": "c", "timestamp": "2025-01-01T10:00:00", "entity_type": "v"}
        ],
    )
    assert response.status_code == 422


def test_rule_validation(client):
    response = client.post(
        "/rules",
        json={"name": "bad", "rule_type": "max_dwell", "params": {"max_seconds": -1}},
    )
    assert response.status_code == 422


def test_full_flow(client, monkeypatch):
    now = datetime.now(UTC).replace(second=0, microsecond=0)
    history_start = now - timedelta(days=15)
    spike_at = now - timedelta(minutes=40)
    events = generate_events(
        history_start,
        15,
        base_rate=20,
        spikes=[("cam-1", spike_at, 300)],
    )
    for index in range(0, len(events), 5000):
        payload = to_payload(events[index : index + 5000])
        assert client.post("/events", json=payload).status_code == 200

    rule = client.post(
        "/rules",
        json={
            "name": "רכב במעקב",
            "rule_type": "watchlist",
            "params": {"entity_ids": [events[-1].entity_id]},
            "severity": "high",
        },
    )
    assert rule.status_code == 201
    first = client.post("/run/rules").json()["new_findings"]
    assert first >= 1
    assert client.post("/run/rules").json()["new_findings"] == 0  # deduplicated

    # Train on everything before the spike, then score the recent windows.
    real_utcnow = modeling.utcnow
    monkeypatch.setattr(modeling, "utcnow", lambda: spike_at - timedelta(hours=2))
    trained = client.post("/model/train")
    assert trained.status_code == 200, trained.text
    monkeypatch.setattr(modeling, "utcnow", real_utcnow)

    assert client.post("/run/score").json()["new_findings"] >= 1
    findings = client.get("/findings", params={"kind": "model"}).json()
    assert any(f["source_id"] == "cam-1" for f in findings)

    target = findings[0]["id"]
    feedback = client.post(
        f"/findings/{target}/feedback",
        json={"status": "confirmed"},
    )
    assert feedback.json()["status"] == "confirmed"

    retrained = client.post("/model/train").json()
    assert retrained["metrics"]["labels_used"] >= 1
    versions = client.get("/model/versions").json()
    assert sum(v["is_active"] for v in versions) == 1
    rollback = client.post(f"/model/versions/{versions[-1]['id']}/activate")
    assert rollback.json()["is_active"]
