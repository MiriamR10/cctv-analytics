"""Publish findings to the forensic service as events (alert channel).

Each finding becomes an event of type ``anomaly`` (model) or ``rule_alert``
(rule) on the camera it belongs to, so it appears in forensic search, timeline,
statistics and audit like any other event.
"""

import logging
from typing import Any

import httpx

from app.models import Finding

logger = logging.getLogger(__name__)

EVENT_TYPE_BY_KIND = {"model": "anomaly", "rule": "rule_alert"}
FORENSIC_SEVERITIES = {"low", "medium", "high", "critical"}


def finding_to_event(finding: Finding, camera_id: str) -> dict[str, Any]:
    explanation = finding.explanation or {}
    return {
        "event_type": EVENT_TYPE_BY_KIND.get(finding.kind, "anomaly"),
        "ts": finding.occurred_at.isoformat(),
        "camera_id": camera_id,
        "zone": finding.zone_id or "",
        "entity_key": finding.entity_id or finding.series_key,
        "plate": finding.entity_id if finding.kind == "rule" else None,
        "object_class": explanation.get("entity_type"),
        "confidence": round(float(finding.score), 3),
        "severity": (
            finding.severity if finding.severity in FORENSIC_SEVERITIES else "medium"
        ),
        "payload": {
            "source": "anomaly",
            "finding_id": finding.id,
            "origin": finding.origin,
            "title": finding.title,
            "explanation": explanation,
        },
    }


class ForensicChannel:
    """Alert channel that writes findings back into the forensic event store."""

    def __init__(self, base_url: str, timeout: float = 10.0) -> None:
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout
        self._cameras: dict[str, str] = {}

    async def _camera_id(self, client: httpx.AsyncClient, name: str) -> str | None:
        if name not in self._cameras:
            response = await client.get(f"{self.base_url}/cameras")
            response.raise_for_status()
            self._cameras = {camera["name"]: camera["id"] for camera in response.json()}
        return self._cameras.get(name)

    async def send(self, finding: Finding) -> None:
        if not finding.source_id:
            logger.info("Finding %s has no camera; not published", finding.id)
            return
        async with httpx.AsyncClient(timeout=self.timeout) as client:
            camera_id = await self._camera_id(client, finding.source_id)
            if camera_id is None:
                logger.warning(
                    "Camera %r not in forensic; finding %s not published",
                    finding.source_id,
                    finding.id,
                )
                return
            response = await client.post(
                f"{self.base_url}/events",
                json=finding_to_event(finding, camera_id),
            )
            response.raise_for_status()
