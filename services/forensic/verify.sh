#!/usr/bin/env bash
# בדיקה שהמערכת עובדת — מריצה הכל בתוך קונטיינר ה-api.
set -euo pipefail

# שולחים את הסקריפט לתוך הקונטיינר ומריצים אותו שם
docker compose exec -T forensic python - <<'PY'
import json
import urllib.error
import urllib.request
from datetime import datetime, timedelta, timezone

API = "http://localhost:8005"  # בתוך הקונטיינר — הפורט הפנימי, לא 8005


def get(path):
    with urllib.request.urlopen(API + path) as r:
        return json.loads(r.read())


def post(path, body):
    req = urllib.request.Request(
        API + path,
        data=json.dumps(body).encode(),
        headers={"Content-Type": "application/json"},
    )
    with urllib.request.urlopen(req) as r:
        return json.loads(r.read())


def iso(dt):
    return dt.isoformat()


def line(title):
    print()
    print("=" * 60)
    print(title)
    print("=" * 60)


now = datetime.now(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0)

# ---- 1
line("1. health")
print(get("/health"))

# ---- 2
line("2. cameras")
cams = get("/cameras")
for c in cams:
    print(f"  {c['name']} | {c['zone']} | {c['id']}")

# ---- 3
line("3. חיפוש — 14 יום אחורה")
r = post("/search", {"ts_from": iso(now - timedelta(days=14)), "limit": 1})
print(f"  total: {r['total']}")
print(f"  by_type: {r['by_type']}")
print(f"  by_camera: {r['by_camera']}")

# ---- 4
line("4. ציר זמן — 24 שעות אחרונות")
r = post("/timeline", {"ts_from": iso(now - timedelta(days=1)), "ts_to": iso(datetime.now(timezone.utc))})
print(f"  items:  {len(r['items'])}")
print(f"  tracks: {len(r['tracks'])}")
print(f"  gaps:   {len(r['gaps'])}")
for g in r["gaps"][:3]:
    print(f"    חור: {g['camera_id'][:8]}... {g['from']}  →  {g['to']}")

# ---- 5
line("5. השוואת תקופות: אתמול מול שלשום")
r = post("/compare", {
    "mode": "period_vs_period",
    "left_window": {"ts_from": iso(now - timedelta(days=1)), "ts_to": iso(now)},
    "right_window": {"ts_from": iso(now - timedelta(days=2)), "ts_to": iso(now - timedelta(days=1))},
})
print(f"  {r['summary']}")
print(f"  verdict: {r['verdict']}")
print(f"  events: left={r['left']['event_count']}  right={r['right']['event_count']}")

# ---- 6
line("6. חריגה מול baseline (יום חריג לפני 3 ימים)")
r = post("/compare", {
    "mode": "vs_baseline",
    "target_window": {"ts_from": iso(now - timedelta(days=4)), "ts_to": iso(now - timedelta(days=3))},
    "baseline_window": {"ts_from": iso(now - timedelta(days=11)), "ts_to": iso(now - timedelta(days=10))},
})
print(f"  {r['summary']}")
print(f"  verdict: {r['verdict']}")
for d in r["deltas"]["deviations"][:5]:
    print(f"    {d['dimension']}/{d['key']}: {d['baseline']} → {d['target']}  ({d['pct_change']}%)")

# ---- 7
line("7. השוואת ישויות — אימות לוחית")
entities = sorted({e["entity_key"] for e in post("/search", {"event_types": ["vehicle_seen"], "limit": 500})["items"] if e.get("entity_key")})
if len(entities) >= 2:
    r = post("/compare", {"mode": "event_vs_event", "left_entity": entities[0], "right_entity": entities[1]})
    print(f"  {entities[0]}  מול  {entities[1]}")
    print(f"  {r['summary']}")
    print(f"  verdict: {r['verdict']}")
else:
    print("  אין מספיק ישויות להשוואה")

# ---- 8
line("8. יומן ביקורת — 5 אחרונים")
for a in get("/audit?limit=5"):
    print(f"  {a['ts']} | {a['user']} | {a['action']}")

print()
print("=" * 60)
print("הבדיקה הסתיימה")
print("=" * 60)
PY