"""API תחקור והשוואת ממצאים — גרסה בסיסית."""
from __future__ import annotations

from contextlib import asynccontextmanager

from fastapi import Depends, FastAPI, Header, HTTPException
from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from . import anomaly_proxy, logic
from .db import get_session, init_db
from .models import AuditLog, Camera, Event, Segment
from .schemas import (
    CameraIn, CameraOut, CompareIn, CompareOut, EventIn, EventOut,
    SearchQuery, SearchResult, SegmentIn, SegmentOut,
    TimelineItem, TimelineOut, TimelineQuery,
)


@asynccontextmanager
async def lifespan(app: FastAPI):
    await init_db()
    yield


app = FastAPI(title="Forensic Core", version="1.0", lifespan=lifespan)
app.include_router(anomaly_proxy.router)


async def _log(session: AsyncSession, user: str, action: str, detail: dict | None = None):
    session.add(AuditLog(user=user, action=action, detail=detail))


async def _events_in_window(session, ts_from, ts_to, zones=None) -> list[dict]:
    stmt = select(Event).where(Event.ts >= ts_from, Event.ts <= ts_to).order_by(Event.ts)
    if zones:
        stmt = stmt.where(Event.zone.in_(zones))
    rows = (await session.execute(stmt)).scalars().all()
    return [
        {"event_type": r.event_type, "ts": r.ts, "zone": r.zone,
         "entity_key": r.entity_key, "plate": r.plate, "severity": r.severity}
        for r in rows
    ]


# ---------------------------------------------------------------- קליטה
@app.post("/cameras", response_model=CameraOut, status_code=201, tags=["ingest"])
async def create_camera(payload: CameraIn, session: AsyncSession = Depends(get_session)):
    cam = Camera(**payload.model_dump())
    session.add(cam)
    await session.commit()
    return cam


@app.get("/cameras", response_model=list[CameraOut], tags=["ingest"])
async def list_cameras(session: AsyncSession = Depends(get_session)):
    return (await session.execute(select(Camera).order_by(Camera.name))).scalars().all()


@app.post("/segments", response_model=SegmentOut, status_code=201, tags=["ingest"])
async def create_segment(payload: SegmentIn, session: AsyncSession = Depends(get_session)):
    if await session.get(Camera, payload.camera_id) is None:
        raise HTTPException(404, "camera not found")
    if payload.end_ts < payload.start_ts:
        raise HTTPException(422, "end_ts precedes start_ts")
    seg = Segment(**payload.model_dump())
    session.add(seg)
    await session.commit()
    return seg


@app.post("/events", response_model=EventOut, status_code=201, tags=["ingest"])
async def create_event(payload: EventIn, session: AsyncSession = Depends(get_session)):
    if await session.get(Camera, payload.camera_id) is None:
        raise HTTPException(404, "camera not found")
    data = payload.model_dump()
    data["plate"] = logic.norm_plate(data.get("plate"))
    ev = Event(**data)
    session.add(ev)
    await session.commit()
    return ev


@app.post("/events/bulk", response_model=list[EventOut], status_code=201, tags=["ingest"])
async def create_events(payload: list[EventIn], session: AsyncSession = Depends(get_session)):
    out = []
    for item in payload:
        data = item.model_dump()
        data["plate"] = logic.norm_plate(data.get("plate"))
        ev = Event(**data)
        session.add(ev)
        out.append(ev)
    await session.commit()
    return out


# ---------------------------------------------------------------- חיפוש היסטורי
@app.post("/search", response_model=SearchResult, tags=["investigate"])
async def search(q: SearchQuery, session: AsyncSession = Depends(get_session),
                 x_user: str = Header(default="anonymous")):
    filters = []
    if q.ts_from:
        filters.append(Event.ts >= q.ts_from)
    if q.ts_to:
        filters.append(Event.ts <= q.ts_to)
    if q.camera_ids:
        filters.append(Event.camera_id.in_(q.camera_ids))
    if q.zones:
        filters.append(Event.zone.in_(q.zones))
    if q.event_types:
        filters.append(Event.event_type.in_(q.event_types))
    if q.object_class:
        filters.append(Event.object_class == q.object_class)
    if q.entity_key:
        filters.append(Event.entity_key == q.entity_key)
    if q.plate:
        filters.append(Event.plate.like(f"%{logic.norm_plate(q.plate)}%"))
    if q.min_confidence is not None:
        filters.append(Event.confidence >= q.min_confidence)

    # 1. סך הכל
    total = await session.scalar(
        select(func.count()).select_from(Event).where(*filters)
    ) or 0

    # 2. פילוח לפי סוג
    type_rows = await session.execute(
        select(Event.event_type, func.count())
        .where(*filters)
        .group_by(Event.event_type)
    )

    # 3. פילוח לפי מצלמה
    cam_rows = await session.execute(
        select(Camera.name, func.count())
        .select_from(Event)
        .join(Camera, Camera.id == Event.camera_id)
        .where(*filters)
        .group_by(Camera.name)
    )

    # 4. השורות עצמן
    order = Event.ts.asc() if q.order == "asc" else Event.ts.desc()
    rows = (await session.execute(
        select(Event).where(*filters).order_by(order).limit(q.limit).offset(q.offset)
    )).scalars().all()

    await _log(session, x_user, "search", q.model_dump(mode="json"))
    await session.commit()

    return SearchResult(
        total=total,
        items=list(rows),
        by_type=dict(type_rows.all()),
        by_camera=dict(cam_rows.all()),
    )

# ---------------------------------------------------------------- ציר זמן
@app.post("/timeline", response_model=TimelineOut, tags=["investigate"])
async def timeline(q: TimelineQuery, session: AsyncSession = Depends(get_session),
                   x_user: str = Header(default="anonymous")):
    cams = {c.id: c for c in (await session.execute(select(Camera))).scalars().all()}
    allowed = set(q.camera_ids) if q.camera_ids else set(cams)

    stmt = select(Event).where(
        Event.ts >= q.ts_from, Event.ts <= q.ts_to, Event.camera_id.in_(allowed)
    )
    if q.zones:
        stmt = stmt.where(Event.zone.in_(q.zones))
    if q.entity_key:
        stmt = stmt.where(Event.entity_key == q.entity_key)
    rows = (await session.execute(stmt.order_by(Event.ts))).scalars().all()

    items = [
        TimelineItem(
            ts=r.ts,
            camera_name=cams[r.camera_id].name if r.camera_id in cams else "?",
            zone=r.zone,
            event_type=r.event_type,
            entity_key=r.entity_key,
            plate=r.plate,
        )
        for r in rows
    ]

    tracks: dict[str, list[TimelineItem]] = {}
    for it in items:
        if it.entity_key:
            tracks.setdefault(it.entity_key, []).append(it)

    segs = (await session.execute(
        select(Segment).where(
            Segment.end_ts >= q.ts_from,
            Segment.start_ts <= q.ts_to,
            Segment.camera_id.in_(allowed),
        )
    )).scalars().all()

    gaps = logic.coverage_gaps(
        [{"camera_id": s.camera_id, "start_ts": s.start_ts,
          "end_ts": s.end_ts, "duration_s": s.duration_s} for s in segs],
        q.ts_from,
        q.ts_to,
    )

    await _log(session, x_user, "timeline", q.model_dump(mode="json"))
    await session.commit()
    return TimelineOut(items=items, tracks=tracks, gaps=gaps)


# ---------------------------------------------------------------- השוואה
@app.post("/compare", response_model=CompareOut, tags=["investigate"])
async def compare(req: CompareIn, session: AsyncSession = Depends(get_session),
                  x_user: str = Header(default="anonymous")):
    if req.mode == "event_vs_event":
        if not (req.left_entity and req.right_entity):
            raise HTTPException(422, "left_entity and right_entity required")

        def _prof(rows):
            data = [
                {"event_type": r.event_type, "ts": r.ts, "zone": r.zone,
                 "entity_key": r.entity_key, "plate": r.plate, "severity": r.severity}
                for r in rows
            ]
            return logic.entity_profile(data)

        left_rows = (await session.execute(
            select(Event).where(Event.entity_key == req.left_entity).order_by(Event.ts)
        )).scalars().all()
        right_rows = (await session.execute(
            select(Event).where(Event.entity_key == req.right_entity).order_by(Event.ts)
        )).scalars().all()
        if not left_rows or not right_rows:
            raise HTTPException(404, "entity has no events")

        left, right = _prof(left_rows), _prof(right_rows)
        shared_plates = sorted(set(left["plates"]) & set(right["plates"]))
        shared_zones = sorted(set(left["zones"]) & set(right["zones"]))
        same = bool(shared_plates) or req.left_entity == req.right_entity

        out = CompareOut(
            mode=req.mode,
            left=left,
            right=right,
            deltas={
                "event_count_delta": left["event_count"] - right["event_count"],
                "by_type": logic.diff_counters(left["by_type"], right["by_type"]),
            },
            verdict="same_entity" if same else ("related" if shared_zones else "unrelated"),
            summary=(
                f"הושוו {req.left_entity} מול {req.right_entity}: "
                f"{'זוהתה אותה ישות' if same else 'לא זוהתה אותה ישות'}, "
                f"אזורים משותפים: {len(shared_zones)}."
            ),
        )

    elif req.mode == "period_vs_period":
        if not (req.left_window and req.right_window):
            raise HTTPException(422, "left_window and right_window required")
        lw, rw = req.left_window, req.right_window

        left = logic.window_stats(
            await _events_in_window(session, lw.ts_from, lw.ts_to, req.zones),
            lw.ts_from, lw.ts_to,
        )
        right = logic.window_stats(
            await _events_in_window(session, rw.ts_from, rw.ts_to, req.zones),
            rw.ts_from, rw.ts_to,
        )

        pct = logic.pct_change(left["events_per_hour"], right["events_per_hour"])
        verdict = "stable" if pct is None or abs(pct) < 15 else ("increase" if pct > 0 else "decrease")

        out = CompareOut(
            mode=req.mode,
            left=left,
            right=right,
            deltas={
                "events_per_hour_pct": pct,
                "by_type": logic.diff_counters(left["by_type"], right["by_type"]),
                "by_zone": logic.diff_counters(left["by_zone"], right["by_zone"]),
            },
            verdict=verdict,
            summary=(
                f"{left['event_count']} אירועים מול {right['event_count']}, "
                f"שינוי בקצב לשעה: {pct}%. מגמה: {verdict}."
            ),
        )

    elif req.mode == "vs_baseline":
        if not (req.target_window and req.baseline_window):
            raise HTTPException(422, "target_window and baseline_window required")
        tw, bw = req.target_window, req.baseline_window

        target = logic.window_stats(
            await _events_in_window(session, tw.ts_from, tw.ts_to, req.zones),
            tw.ts_from, tw.ts_to,
        )
        base = logic.window_stats(
            await _events_in_window(session, bw.ts_from, bw.ts_to, req.zones),
            bw.ts_from, bw.ts_to,
        )

        devs = logic.deviations(target, base)
        out = CompareOut(
            mode=req.mode,
            left=target,
            right=base,
            deltas={
                "events_per_hour_pct": logic.pct_change(
                    target["events_per_hour"], base["events_per_hour"]
                ),
                "deviations": devs,
            },
            verdict="anomalous" if devs else "within_baseline",
            summary=f"{len(devs)} חריגות של 30% ומעלה מול הבסיס.",
        )

    else:
        raise HTTPException(400, "unknown mode")

    await _log(session, x_user, "compare", {"mode": req.mode, "verdict": out.verdict})
    await session.commit()
    return out


# ---------------------------------------------------------------- ניהול
@app.post("/admin/retention", tags=["admin"])
async def run_retention(dry_run: bool = True, days: int | None = None,
                        session: AsyncSession = Depends(get_session)):
    from datetime import datetime, timedelta, timezone

    from .config import settings

    cutoff = datetime.now(timezone.utc) - timedelta(days=days or settings.retention_days)
    old = (await session.execute(
        select(Segment).where(Segment.end_ts < cutoff)
    )).scalars().all()
    ids = [s.id for s in old]

    if not dry_run and ids:
        await session.execute(delete(Segment).where(Segment.id.in_(ids)))
        await session.commit()

    return {"dry_run": dry_run, "cutoff": cutoff.isoformat(), "candidates": len(ids)}


@app.get("/audit", tags=["admin"])
async def read_audit(limit: int = 200, session: AsyncSession = Depends(get_session)):
    rows = (await session.execute(
        select(AuditLog).order_by(AuditLog.ts.desc()).limit(limit)
    )).scalars().all()
    return [{"ts": r.ts, "user": r.user, "action": r.action, "detail": r.detail} for r in rows]


@app.get("/health", tags=["system"])
async def health():
    return {"status": "ok"}


import os

from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

# ... בסוף הקובץ, אחרי כל ה-endpoints:

@app.get("/", include_in_schema=False)
async def ui():
    return FileResponse(os.path.join(os.path.dirname(__file__), "static", "index.html"))