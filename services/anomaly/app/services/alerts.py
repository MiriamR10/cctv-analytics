"""Alert dispatch. Add channels (email, SMS, syslog...) by implementing AlertChannel."""

import logging
from typing import Protocol

import httpx

from app.config import settings
from app.domain import severity_rank
from app.models import Finding

logger = logging.getLogger(__name__)


class AlertChannel(Protocol):
    async def send(self, finding: Finding) -> None: ...


class LogChannel:
    async def send(self, finding: Finding) -> None:
        logger.warning(
            "ALERT [%s] %s (finding=%s, score=%.2f)",
            finding.severity,
            finding.title,
            finding.id,
            finding.score,
        )


class WebhookChannel:
    def __init__(self, url: str, timeout: float) -> None:
        self.url = url
        self.timeout = timeout

    async def send(self, finding: Finding) -> None:
        payload = {
            "id": finding.id,
            "kind": finding.kind,
            "origin": finding.origin,
            "title": finding.title,
            "severity": finding.severity,
            "score": finding.score,
            "occurred_at": finding.occurred_at.isoformat(),
            "source_id": finding.source_id,
            "zone_id": finding.zone_id,
            "entity_id": finding.entity_id,
            "explanation": finding.explanation,
        }
        async with httpx.AsyncClient(timeout=self.timeout) as client:
            response = await client.post(self.url, json=payload)
            response.raise_for_status()


def default_channels() -> list[AlertChannel]:
    channels: list[AlertChannel] = [LogChannel()]
    if settings.forensic_url:
        from app.services.forensic_publish import ForensicChannel

        channels.append(
            ForensicChannel(settings.forensic_url, settings.webhook_timeout_seconds),
        )
    if settings.webhook_url:
        channels.append(
            WebhookChannel(settings.webhook_url, settings.webhook_timeout_seconds),
        )
    return channels


async def dispatch(findings: list[Finding], channels: list[AlertChannel]) -> None:
    minimum = severity_rank(settings.alert_min_severity)
    for finding in findings:
        if severity_rank(finding.severity) < minimum:
            continue
        for channel in channels:
            try:
                await channel.send(finding)
            except Exception:  # one failing channel must not block the others
                logger.exception("Alert channel %s failed", type(channel).__name__)
