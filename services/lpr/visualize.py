# visualize.py — מתוקן, מפרסר נכון, גרסה מלאה עם החיתוך המוגדל של הלוחית
import ast
import os
import re
import cv2
import numpy as np
import pandas as pd

INTERPOLATED_CSV = os.environ.get('INTERPOLATED_CSV', './test_interpolated.csv')
VIDEO_PATH = os.environ.get('VIDEO_PATH', 'data/videos/sample_10s.mp4')
OUTPUT_VIDEO = os.environ.get('OUTPUT_VIDEO', './out.mp4')


def parse_bbox(s):
    """מפרסר קואורדינטות מכל פורמט: '[1.2 3.4 5.6 7.8]' או '[1.2, 3.4, ...]'."""
    if isinstance(s, (list, tuple)):
        return [float(v) for v in s]
    nums = re.findall(r'-?\d+\.?\d*(?:[eE][+-]?\d+)?', str(s))
    return [float(n) for n in nums[:4]]


def draw_border(img, top_left, bottom_right, color=(0, 255, 0),
                thickness=10, line_length_x=200, line_length_y=200):
    x1, y1 = top_left
    x2, y2 = bottom_right
    cv2.line(img, (x1, y1), (x1, y1 + line_length_y), color, thickness)
    cv2.line(img, (x1, y1), (x1 + line_length_x, y1), color, thickness)
    cv2.line(img, (x1, y2), (x1, y2 - line_length_y), color, thickness)
    cv2.line(img, (x1, y2), (x1 + line_length_x, y2), color, thickness)
    cv2.line(img, (x2, y1), (x2 - line_length_x, y1), color, thickness)
    cv2.line(img, (x2, y1), (x2, y1 + line_length_y), color, thickness)
    cv2.line(img, (x2, y2), (x2, y2 - line_length_y), color, thickness)
    cv2.line(img, (x2, y2), (x2 - line_length_x, y2), color, thickness)
    return img


# ------------------------------------------------------------------
# קריאת CSV
# ------------------------------------------------------------------
results = pd.read_csv(INTERPOLATED_CSV)
print(f"נקראו {len(results)} שורות")

# ------------------------------------------------------------------
# חילוץ הלוחית הטובה ביותר לכל car_id (לפי text_score המקסימלי)
# ------------------------------------------------------------------
cap = cv2.VideoCapture(VIDEO_PATH)
if not cap.isOpened():
    raise SystemExit(f"לא ניתן לפתוח {VIDEO_PATH}")

fps = cap.get(cv2.CAP_PROP_FPS) or 25.0
width = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
height = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
print(f"וידאו: {width}×{height} @ {fps}fps")

license_plate = {}

for car_id in np.unique(results['car_id']):
    rows = results[results['car_id'] == car_id]
    if len(rows) == 0:
        continue

    # השורה עם הביטחון הגבוה ביותר
    best_idx = rows['license_number_score'].idxmax()
    best_row = rows.loc[best_idx]

    lp_text = best_row['license_number']
    lp_bbox = parse_bbox(best_row['license_plate_bbox'])
    frame_nmr = int(best_row['frame_nmr'])

    # חילוץ ה-crop של הלוחית מהפריים המתאים
    cap.set(cv2.CAP_PROP_POS_FRAMES, frame_nmr)
    ret, frame = cap.read()
    if not ret:
        continue

    x1, y1, x2, y2 = lp_bbox
    h, w = height, width
    if x2 <= x1 or y2 <= y1:
        continue
    x1c, y1c = max(0, int(x1)), max(0, int(y1))
    x2c, y2c = min(w, int(x2)), min(h, int(y2))
    if x2c <= x1c or y2c <= y1c:
        continue

    license_crop = frame[y1c:y2c, x1c:x2c, :].copy()
    if license_crop.size == 0:
        continue

    # הגדלה לגובה 400 — לתצוגה גדולה בפינת הסרטון
    target_h = 400
    target_w = max(1, int((x2c - x1c) * target_h / max(1, (y2c - y1c))))
    license_crop = cv2.resize(license_crop, (target_w, target_h))

    license_plate[car_id] = {
        'license_crop': license_crop,
        'license_plate_number': lp_text,
        'score': float(best_row['license_number_score']),
    }
    print(f"  ID{int(car_id):3d}: '{lp_text}' (score={best_row['license_number_score']:.3f}, "
          f"frame={frame_nmr})")

# ------------------------------------------------------------------
# יצירת הסרטון
# ------------------------------------------------------------------
cap.set(cv2.CAP_PROP_POS_FRAMES, 0)
fourcc = cv2.VideoWriter_fourcc(*'mp4v')
out = cv2.VideoWriter(OUTPUT_VIDEO, fourcc, fps, (width, height))
if not out.isOpened():
    raise SystemExit("לא ניתן לפתוח VideoWriter")

frame_nmr = -1
written = 0

while True:
    ret, frame = cap.read()
    if not ret:
        break                                  # ← התיקון: עצירה בסוף הסרטון

    frame_nmr += 1
    df_ = results[results['frame_nmr'] == frame_nmr]

    for row_indx in range(len(df_)):
        row = df_.iloc[row_indx]
        car_id = row['car_id']

        try:
            car_x1, car_y1, car_x2, car_y2 = parse_bbox(row['car_bbox'])
            lx1, ly1, lx2, ly2 = parse_bbox(row['license_plate_bbox'])
        except Exception:
            continue

        # גבולות
        car_x1 = max(0, min(width - 1, int(car_x1)))
        car_y1 = max(0, min(height - 1, int(car_y1)))
        car_x2 = max(0, min(width - 1, int(car_x2)))
        car_y2 = max(0, min(height - 1, int(car_y2)))
        lx1 = max(0, min(width - 1, int(lx1)))
        ly1 = max(0, min(height - 1, int(ly1)))
        lx2 = max(0, min(width - 1, int(lx2)))
        ly2 = max(0, min(height - 1, int(ly2)))

        draw_border(frame, (car_x1, car_y1), (car_x2, car_y2),
                    (0, 255, 0), 25, line_length_x=200, line_length_y=200)
        cv2.rectangle(frame, (lx1, ly1), (lx2, ly2), (0, 0, 255), 12)

        # הדבקת ה-crop המוגדל מעל הרכב
        if car_id in license_plate:
            lp = license_plate[car_id]
            crop = lp['license_crop']
            H, W, _ = crop.shape

            # מיקום מעל הרכב, ממורכז
            dst_x1 = int((car_x2 + car_x1 - W) / 2)
            dst_x2 = dst_x1 + W
            dst_y1 = car_y1 - H - 100
            dst_y2 = car_y1 - 100

            # בדיקה שהאזור בתוך הפריים
            if (dst_x1 >= 0 and dst_y1 >= 0 and
                    dst_x2 <= width and dst_y2 <= height):
                frame[dst_y1:dst_y2, dst_x1:dst_x2, :] = crop

                # רקע לבן לטקסט
                tx1, tx2 = dst_x1, dst_x2
                ty1, ty2 = dst_y1 - 100, dst_y1
                if ty1 >= 0:
                    frame[ty1:ty2, tx1:tx2, :] = (255, 255, 255)

                    (text_width, text_height), _ = cv2.getTextSize(
                        lp['license_plate_number'], cv2.FONT_HERSHEY_SIMPLEX, 2.5, 8)
                    cv2.putText(frame,
                                lp['license_plate_number'],
                                (int((tx1 + tx2 - text_width) / 2), int((ty1 + ty2 + text_height) / 2)),
                                cv2.FONT_HERSHEY_SIMPLEX, 2.5, (0, 0, 0), 8)

    out.write(frame)
    written += 1

    if frame_nmr % 200 == 0:
        print(f"  פריים {frame_nmr}...", flush=True)

out.release()
cap.release()
print(f"\nנשמר: {OUTPUT_VIDEO}  ({written} פריימים)")