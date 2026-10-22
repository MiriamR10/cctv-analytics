"""
נתוני דמה: 3 מצלמות, 14 יום של סגמנטים ואירועים.

הרצה:
    docker compose exec forensic python -m app.seed
    docker compose exec forensic python -m app.seed --reset    # מוחק הכל ובונה מחדש
    docker compose exec forensic python -m app.seed --days 30

מה בנתונים (כדי שיהיה מה לבדוק):
  * 3 מצלמות ב-3 אזורים שונים
  * 14 יום אחורה, אירועים בכל שעות היממה
  * בוקר/צהריים עמוסים, לילה שקט        -> בסיס להשוואת תקופות
  * יום חריג אחד עם זינוק בלילה          -> בסיס לזיהוי חריגה מול baseline
  * לוחיות חוזרות בכמה מצלמות            -> בסיס להשוואת ישות וציר זמן
  * כמה "חורי הקלטה" מכוונים             -> בסיס לבדיקת gaps
"""
from __future__ import annotations

import argparse
import asyncio
import random
import uuid
from datetime import datetime, timedelta, timezone

from sqlalchemy import delete, func, select

from .db import SessionLocal, init_db
from .models import AuditLog, Camera, Event, Segment

CAMERAS = [
    ("שער כניסה דרומי", "שער"),
    ("חניון תת-קרקעי", "חניון"),
    ("מסדרון מבנה ראשי", "מבנה"),
]

PLATES = [
    "12345678", "87654321", "55544433", "11223344",
    "99887766", "24681357", "13572468", "90817263",
]

EVENT_TYPES = ["vehicle_seen", "person_seen", "line_cross", "loitering", "crowd"]

HOUR_WEIGHTS = {
    0: 1, 1: 1, 2: 1, 3: 1, 4: 1, 5: 2,
    6: 5, 7: 12, 8: 14, 9: 9,
    10: 6, 11: 6, 12: 7, 13: 6, 14: 6, 15: 7,
    16: 12, 17: 14, 18: 13, 19: 8,
    20: 5, 21: 4, 22: 3, 23: 2,
}

ANOMALY_DAY = 3  # לפני 3 ימים — יום חריג


def hour_weights(day_index: int) -> list[int]:
    base = [HOUR_WEIGHTS[h] for h in range(24)]
    weekday = (datetime.now(timezone.utc).date() - timedelta(days=day_index)).weekday()

    if weekday == 4:  # שישי — עמוס יותר
        base = [w * 2 for w in base]
    if day_index == ANOMALY_DAY:  # יום חריג: פעילות מוגברת בשעות הלילה
        base = [w * 6 if h in (0, 1, 2, 3) else w for h, w in enumerate(base)]

    return base


def ts_for_day(day_index: int, rng: random.Random) -> datetime:
    day = (
        datetime.now(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0)
        - timedelta(days=day_index)
    )
    hour = rng.choices(range(24), weights=hour_weights(day_index), k=1)[0]
    return day + timedelta(hours=hour, minutes=rng.randint(0, 59), seconds=rng.randint(0, 59))


async def seed(reset: bool = False, days: int = 14) -> None:
    await init_db()
    rng = random.Random(20260301)

    async with SessionLocal() as s:
        existing = await s.scalar(select(func.count()).select_from(Camera))
        if existing and not reset:
            print(f"seed: כבר יש {existing} מצלמות. להרצה מחדש: --reset")
            return

        if reset:
            print("seed: מוחק נתונים קיימים...")
            await s.execute(delete(AuditLog))
            await s.execute(delete(Event))
            await s.execute(delete(Segment))
            await s.execute(delete(Camera))
            await s.commit()

        # ---- מצלמות
        cameras = []
        for name, zone in CAMERAS:
            cam = Camera(name=name, zone=zone, retained_days=120)
            s.add(cam)
            cameras.append(cam)
        await s.flush()

        # ---- סגמנטים: 10 דקות למצלמה, כל 24 השעות, days ימים אחורה
        now = datetime.now(timezone.utc).replace(minute=0, second=0, microsecond=0)
        start = now - timedelta(days=days)

        # 6 חורי הקלטה מכוונים, כל אחד שעה שלמה
        skip: set[tuple[str, str]] = set()
        for _ in range(6):
            cam = rng.choice(cameras)
            hour_block = (start + timedelta(hours=rng.randint(0, days * 24 - 1))).replace(minute=0)
            for k in range(6):
                skip.add((str(cam.id), (hour_block + timedelta(minutes=10 * k)).isoformat()))

        segments = []
        cursor = start
        while cursor < now:
            for cam in cameras:
                if (str(cam.id), cursor.isoformat()) in skip:
                    continue
                segments.append(
                    Segment(
                        camera_id=cam.id,
                        start_ts=cursor,
                        end_ts=cursor + timedelta(minutes=10),
                        s3_key=f"{cam.zone}/{cam.id}/{cursor:%Y%m%dT%H%M%S}.mp4",
                        sha256=uuid.uuid5(uuid.NAMESPACE_URL, f"{cam.id}{cursor}").hex * 2,
                        duration_s=600.0,
                    )
                )
            cursor += timedelta(minutes=10)
        s.add_all(segments)
        await s.flush()

        # ---- אירועים
        events = []
        for _ in range(days * 120):
            day_index = rng.choices(
                range(days), weights=[max(1, 10 - i) for i in range(days)], k=1
            )[0]
            ts = ts_for_day(day_index, rng)
            cam = rng.choice(cameras)
            etype = rng.choices(EVENT_TYPES, weights=[40, 25, 15, 12, 8], k=1)[0]

            plate = None
            entity = None
            severity = "info"

            if etype in ("vehicle_seen", "line_cross"):
                plate = rng.choice(PLATES) if rng.random() < 0.85 else None
                entity = plate
            elif etype in ("person_seen", "loitering"):
                entity = f"person-{rng.randint(1, 30):03d}"
            elif etype == "crowd":
                entity = f"crowd-{cam.zone}"

            if etype == "loitering":
                severity = "medium"
            elif etype == "crowd":
                severity = "high"
            elif etype == "line_cross" and rng.random() < 0.3:
                severity = "low"

            events.append(
                Event(
                    event_type=etype,
                    ts=ts,
                    camera_id=cam.id,
                    zone=cam.zone,
                    entity_key=entity,
                    plate=plate,
                    object_class=(
                        "vehicle" if plate
                        else ("person" if etype in ("person_seen", "loitering") else None)
                    ),
                    confidence=round(rng.uniform(0.6, 0.99), 3),
                    severity=severity,
                    payload={"source": "seed", "direction": rng.choice(["in", "out"])},
                )
            )

        s.add_all(events)
        await s.commit()

        print("seed: הושלם")
        print(f"  מצלמות:  {len(cameras)}")
        print(f"  סגמנטים: {len(segments)}")
        print(f"  אירועים: {len(events)}")
        print(f"  טווח:    {start:%Y-%m-%d} עד {now:%Y-%m-%d}")
        print(f"  חורי תיעוד מכוונים: {len(skip) // 6} שעות שלמות (יום חריג: לפני {ANOMALY_DAY} ימים)")


def main() -> None:
    parser = argparse.ArgumentParser(description="זריעת נתוני דמה")
    parser.add_argument("--reset", action="store_true", help="מחיקת נתונים קיימים לפני הזריעה")
    parser.add_argument("--days", type=int, default=14, help="כמה ימים אחורה (ברירת מחדל 14)")
    args = parser.parse_args()
    asyncio.run(seed(reset=args.reset, days=args.days))


if __name__ == "__main__":
    main()