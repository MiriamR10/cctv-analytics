"""Publish recognised plates to the central forensic service.

One `vehicle_seen` event per tracked car, using the reading with the
highest OCR confidence. Event time = VIDEO_START_TS + frame / fps.
"""
import csv
import os
from datetime import datetime, timedelta, timezone

import cv2

from forensic_client import FORENSIC_URL, publish_events

RESULTS_CSV = os.environ.get('RESULTS_CSV', './test.csv')
VIDEO_PATH = os.environ.get('VIDEO_PATH', 'data/videos/sample_10s.mp4')
CAMERA_NAME = os.environ.get('CAMERA_NAME', 'LPR camera')
CAMERA_ZONE = os.environ.get('CAMERA_ZONE', 'gate')
VIDEO_START_TS = os.environ.get('VIDEO_START_TS', '')


if not FORENSIC_URL:
    raise SystemExit('FORENSIC_URL not set, skipping publish')

start_ts = (datetime.fromisoformat(VIDEO_START_TS) if VIDEO_START_TS
            else datetime.now(timezone.utc))

cap = cv2.VideoCapture(VIDEO_PATH)
fps = cap.get(cv2.CAP_PROP_FPS) or 25.0
cap.release()

with open(RESULTS_CSV, 'r') as file:
    rows = list(csv.DictReader(file))

best_per_car = {}
first_frame = {}
for row in rows:
    car_id = int(float(row['car_id']))
    frame_nmr = int(row['frame_nmr'])
    first_frame[car_id] = min(first_frame.get(car_id, frame_nmr), frame_nmr)
    if (car_id not in best_per_car or
            float(row['license_number_score']) > float(best_per_car[car_id]['license_number_score'])):
        best_per_car[car_id] = row

events = []
for car_id, row in best_per_car.items():
    plate = row['license_number']
    events.append({
        'event_type': 'vehicle_seen',
        'ts': (start_ts + timedelta(seconds=first_frame[car_id] / fps)).isoformat(),
        'entity_key': plate,
        'plate': plate,
        'object_class': 'vehicle',
        'confidence': round(float(row['license_number_score']), 3),
        'payload': {'source': 'lpr', 'track_id': car_id,
                    'video': os.path.basename(VIDEO_PATH), 'frame': first_frame[car_id]},
    })

stored = publish_events(events, CAMERA_NAME, CAMERA_ZONE)
print(f'Published {stored} plate events to {FORENSIC_URL} (camera: {CAMERA_NAME})')
