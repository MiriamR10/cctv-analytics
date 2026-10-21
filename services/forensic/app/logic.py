"""ליבת התחקור — פונקציות טהורות, בלי DB ובלי FastAPI."""
from __future__ import annotations

import math
from collections import Counter
from datetime import timedelta

EPS = 1e-9


def norm_plate(value: str | None) -> str | None:
    if not value:
        return None
    cleaned = value.replace("-", "").replace(" ", "").replace(".", "").upper()
    return cleaned or None


def pct_change(new: float, old: float) -> float | None:
    if abs(old) < EPS:
        return None if abs(new) < EPS else float("inf")
    return round((new - old) / old * 100.0, 2)


def window_stats(events: list[dict], ts_from, ts_to) -> dict:
    hours = max((ts_to - ts_from).total_seconds() / 3600.0, EPS)
    entities = sorted({e["entity_key"] for e in events if e.get("entity_key")})
    return {
        "event_count": len(events),
        "duration_hours": round(hours, 2),
        "events_per_hour": round(len(events) / hours, 2),
        "by_type": dict(Counter(e["event_type"] for e in events)),
        "by_zone": dict(Counter(e.get("zone") for e in events if e.get("zone"))),
        "by_severity": dict(Counter(e.get("severity", "info") for e in events)),
        "entities": entities,
    }


def entity_profile(events: list[dict]) -> dict:
    return {
        "entity_key": events[0].get("entity_key") if events else None,
        "event_count": len(events),
        "first_seen": events[0]["ts"] if events else None,
        "last_seen": events[-1]["ts"] if events else None,
        "by_type": dict(Counter(e["event_type"] for e in events)),
        "zones": sorted({e["zone"] for e in events if e.get("zone")}),
        "plates": sorted({e["plate"] for e in events if e.get("plate")}),
    }


def diff_counters(a: dict, b: dict) -> dict:
    return {
        k: {"left": a.get(k, 0), "right": b.get(k, 0), "delta": a.get(k, 0) - b.get(k, 0)}
        for k in sorted(set(a) | set(b))
    }


def deviations(target: dict, baseline: dict, threshold: float = 30.0) -> list[dict]:
    out = []
    for dim in ("by_type", "by_zone"):
        for key, val in target.get(dim, {}).items():
            base = baseline.get(dim, {}).get(key, 0)
            pct = pct_change(val, base)
            if pct is None or pct == float("inf") or abs(pct) >= threshold:
                out.append({
                    "dimension": dim,
                    "key": key,
                    "target": val,
                    "baseline": base,
                    "pct_change": pct,
                })
    return out


def coverage_gaps(segments: list[dict], ts_from, ts_to, factor: float = 1.5) -> list[dict]:
    """חורים בתיעוד: מצלמה שלא הקליטה לאורך זמן גדול מהרגיל."""
    by_cam: dict[str, list[dict]] = {}
    for s in segments:
        by_cam.setdefault(str(s["camera_id"]), []).append(s)

    gaps = []
    for cid, segs in by_cam.items():
        segs.sort(key=lambda s: s["start_ts"])
        typical = max((s.get("duration_s") or 0) for s in segs) or 60.0
        threshold = timedelta(seconds=typical * factor)
        cursor = ts_from
        for i, s in enumerate(segs):
            if s["start_ts"] - cursor > (timedelta(0) if i == 0 else threshold):
                gaps.append({
                    "camera_id": cid,
                    "from": cursor.isoformat(),
                    "to": s["start_ts"].isoformat(),
                })
            cursor = max(cursor, s["end_ts"])
        if ts_to - cursor > threshold:
            gaps.append({"camera_id": cid, "from": cursor.isoformat(), "to": ts_to.isoformat()})
    return gaps


def cosine(a: list[float] | None, b: list[float] | None) -> float:
    if not a or not b or len(a) != len(b):
        return 0.0
    dot = sum(x * y for x, y in zip(a, b))
    na = math.sqrt(sum(x * x for x in a))
    nb = math.sqrt(sum(y * y for y in b))
    return 0.0 if na < EPS or nb < EPS else dot / (na * nb)