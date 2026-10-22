"""Synthetic events with daily/weekly routine and injected anomalies.

Used by tests and for demos until real data arrives:
``python -m app.synthetic --days 21 --url http://localhost:8000``.
"""

import argparse
import math
import random
from datetime import UTC, datetime, timedelta

from app.domain import EventRecord

DEFAULT_SOURCES = (("cam-1", "gate-north"), ("cam-2", "parking-b"), ("cam-3", None))


def expected_rate(moment: datetime, base: float) -> float:
    """Events per hour: daytime peak, quiet nights, quieter weekends."""
    hour = moment.hour + moment.minute / 60
    daily = (
        0.15
        + math.exp(-((hour - 9) ** 2) / 6)
        + 0.8
        * math.exp(
            -((hour - 17) ** 2) / 6,
        )
    )
    weekly = 0.3 if moment.weekday() in (4, 5) else 1.0  # Fri / Sat
    return base * daily * weekly


def generate_events(
    start: datetime,
    days: int,
    base_rate: float = 40.0,
    sources=DEFAULT_SOURCES,
    seed: int = 7,
    spikes: list[tuple[str, datetime, int]] = (),
) -> list[EventRecord]:
    """``spikes``: (source_id, moment, extra_events) injected on top of routine."""
    rng = random.Random(seed)
    events: list[EventRecord] = []
    next_id = 1
    step = timedelta(minutes=5)
    moment = start
    end = start + timedelta(days=days)
    plates = [
        f"{rng.randint(10, 99)}-{rng.randint(100, 999)}-{rng.randint(10, 99)}"
        for _ in range(300)
    ]

    def add(source_id, zone_id, at):
        nonlocal next_id
        events.append(
            EventRecord(
                id=next_id,
                source_id=source_id,
                zone_id=zone_id,
                timestamp=at,
                entity_type="vehicle",
                entity_id=rng.choice(plates),
                confidence=round(rng.uniform(0.8, 0.99), 3),
                dwell_seconds=round(rng.expovariate(1 / 60), 1),
            ),
        )
        next_id += 1

    while moment < end:
        for source_id, zone_id in sources:
            lam = expected_rate(moment, base_rate) * step.total_seconds() / 3600
            for _ in range(_poisson(rng, lam)):
                add(source_id, zone_id, moment + timedelta(seconds=rng.uniform(0, 299)))
        moment += step

    for source_id, at, extra in spikes:
        zone_id = dict(sources)[source_id]
        for _ in range(extra):
            add(source_id, zone_id, at + timedelta(seconds=rng.uniform(0, 600)))
    events.sort(key=lambda event: event.timestamp)
    return events


def _poisson(rng: random.Random, lam: float) -> int:
    if lam <= 0:
        return 0
    limit, count, product = math.exp(-lam), 0, rng.random()
    while product > limit:
        count += 1
        product *= rng.random()
    return count


def main() -> None:
    import httpx

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--days", type=int, default=21)
    parser.add_argument("--url", default="http://localhost:8000")
    args = parser.parse_args()

    now = datetime.now(UTC).replace(second=0, microsecond=0)
    events = generate_events(now - timedelta(days=args.days), args.days)
    payload = [
        {
            "source_id": event.source_id,
            "zone_id": event.zone_id,
            "timestamp": event.timestamp.isoformat(),
            "entity_type": event.entity_type,
            "entity_id": event.entity_id,
            "confidence": event.confidence,
            "dwell_seconds": event.dwell_seconds,
        }
        for event in events
    ]
    with httpx.Client(base_url=args.url, timeout=120) as client:
        for index in range(0, len(payload), 5000):
            client.post(
                "/events", json=payload[index : index + 5000]
            ).raise_for_status()
    print(f"Sent {len(payload)} events")


if __name__ == "__main__":
    main()
