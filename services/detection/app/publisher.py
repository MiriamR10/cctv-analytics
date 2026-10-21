"""Publish detection results to the central forensic service.

Optional: when FORENSIC_URL is not set, nothing is sent.
One event per tracked object (first appearance); untracked detections
(images, HOG detector) produce one event each.
"""
import json
import logging
import os
import urllib.request
from datetime import datetime, timedelta, timezone

from .db import db

logger = logging.getLogger(__name__)

FORENSIC_URL = os.environ.get("FORENSIC_URL", "").rstrip("/")

VEHICLE_LABELS = {"car", "truck", "bus", "motorcycle", "bicycle"}


def _request(method: str, path: str, body=None):
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(
        FORENSIC_URL + path,
        data=data,
        method=method,
        headers={"Content-Type": "application/json"},
    )
    with urllib.request.urlopen(req, timeout=30) as response:
        return json.loads(response.read())


def _ensure_camera(name: str, zone: str) -> str:
    for camera in _request("GET", "/cameras"):
        if camera["name"] == name:
            return camera["id"]
    return _request("POST", "/cameras", {"name": name, "zone": zone})["id"]


def _event_type(label: str) -> str:
    label = label.lower()
    if label == "person":
        return "person_seen"
    if label in VEHICLE_LABELS:
        return "vehicle_seen"
    return "object_seen"


def publish_job(job_id: str, camera_name: str, zone: str) -> int:
    """Send a completed job's detections. Returns the number of events stored."""
    if not FORENSIC_URL:
        return 0

    with db() as conn:
        rows = conn.execute(
            """
            SELECT frame_number, timestamp_seconds, label, confidence, track_id
            FROM detections
            WHERE job_id = ?
            ORDER BY frame_number, id
            """,
            (job_id,),
        ).fetchall()

    started = datetime.now(timezone.utc)
    seen_tracks = set()
    events = []

    for row in rows:
        track_id = row["track_id"]
        if track_id is not None:
            if track_id in seen_tracks:
                continue
            seen_tracks.add(track_id)

        entity_key = (
            f"{row['label']}-{job_id[:8]}-{track_id}"
            if track_id is not None
            else None
        )

        events.append({
            "event_type": _event_type(row["label"]),
            "ts": (started + timedelta(seconds=row["timestamp_seconds"])).isoformat(),
            "zone": zone,
            "entity_key": entity_key,
            "object_class": row["label"],
            "confidence": round(float(row["confidence"]), 3),
            "payload": {
                "source": "detection",
                "job_id": job_id,
                "frame": row["frame_number"],
                "track_id": track_id,
            },
        })

    if not events:
        return 0

    camera_id = _ensure_camera(camera_name, zone)
    for event in events:
        event["camera_id"] = camera_id

    stored = _request("POST", "/events/bulk", events)
    logger.info("Published %d events for job %s", len(stored), job_id)
    return len(stored)
