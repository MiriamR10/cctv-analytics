
import cv2
import numpy as np
import math

W, H, FPS, SECONDS = 960, 540, 20, 12
OUTPUT = "cctv_test_street.mp4"

writer = cv2.VideoWriter(
    OUTPUT,
    cv2.VideoWriter_fourcc(*"mp4v"),
    FPS,
    (W, H),
)

if not writer.isOpened():
    raise RuntimeError("Could not create MP4. Check your OpenCV/FFmpeg installation.")

def rect(img, x1, y1, x2, y2, color):
    cv2.rectangle(img, (int(x1), int(y1)), (int(x2), int(y2)), color, -1)

def circle(img, x, y, r, color):
    cv2.circle(img, (int(x), int(y)), max(1, int(r)), color, -1)

def vehicle(img, x, ground, scale, kind, color):
    if kind == "bus":
        width, height = 145, 58
        rect(img, x, ground-height, x+width, ground-3, color)
        for i in range(4):
            rect(img, x+10+i*33, ground-49, x+33+i*33, ground-29,
                 (185, 220, 238))
        wheels = [x+28, x+116]
    elif kind == "truck":
        rect(img, x, ground-52, x+95, ground-3, (65, 155, 205))
        rect(img, x+95, ground-43, x+140, ground-3, color)
        rect(img, x+104, ground-38, x+131, ground-21, (185, 220, 238))
        wheels = [x+22, x+75, x+120]
    else:
        rect(img, x, ground-34, x+100, ground-4, color)
        pts = np.array([
            [int(x+18), ground-34],
            [int(x+34), ground-54],
            [int(x+72), ground-54],
            [int(x+87), ground-34],
        ], np.int32)
        cv2.fillPoly(img, [pts], color)
        rect(img, x+38, ground-50, x+56, ground-36, (185, 220, 238))
        rect(img, x+60, ground-50, x+78, ground-36, (185, 220, 238))
        wheels = [x+22, x+78]

    for wx in wheels:
        circle(img, wx, ground-3, 11, (20, 22, 25))
        circle(img, wx, ground-3, 5, (155, 160, 165))

def person(img, x, ground, t, shirt):
    head_y = ground-55
    circle(img, x, head_y, 8, (185, 145, 110))
    cv2.ellipse(img, (int(x), int(head_y-3)), (8, 6),
                0, 180, 360, (40, 35, 30), -1)

    rect(img, x-8, ground-43, x+8, ground-19, shirt)
    swing = math.sin(t*5)*7

    cv2.line(img, (int(x-5), ground-37),
             (int(x-10-swing), ground-20), (185,145,110), 4)
    cv2.line(img, (int(x+5), ground-37),
             (int(x+10+swing), ground-20), (185,145,110), 4)
    cv2.line(img, (int(x-4), ground-19),
             (int(x-6-swing), ground), (35,35,40), 5)
    cv2.line(img, (int(x+4), ground-19),
             (int(x+6+swing), ground), (35,35,40), 5)

for frame_no in range(FPS * SECONDS):
    t = frame_no / FPS
    img = np.full((H, W, 3), (220, 185, 135), dtype=np.uint8)

    # Sky and sun
    img[:385] = (225, 190, 145)
    circle(img, 825, 65, 38, (160, 225, 255))

    # Buildings
    buildings = [
        (0, 115, 260), (125, 105, 305), (240, 110, 280),
        (360, 140, 320), (510, 130, 255),
        (650, 145, 300), (805, 155, 270)
    ]
    for x, width, height in buildings:
        rect(img, x, 385-height, x+width, 385, (175, 180, 175))
        for yy in range(385-height+15, 375, 29):
            for xx in range(x+10, x+width-8, 24):
                rect(img, xx, yy, xx+11, yy+16, (90, 110, 125))

    # Sidewalk and road
    rect(img, 0, 385, W, 438, (185, 180, 170))
    rect(img, 0, 438, W, H, (65, 67, 70))

    for xx in range(-30, W, 100):
        rect(img, xx, 488, xx+45, 492, (40, 210, 245))

    # Trees
    for x in (110, 340, 700, 900):
        rect(img, x-4, 350, x+4, 400, (75, 55, 35))
        circle(img, x, 338, 22, (70, 130, 70))

    # Traffic light
    rect(img, 500, 260, 506, 390, (35, 35, 35))
    rect(img, 488, 248, 518, 305, (25, 25, 25))
    circle(img, 503, 264, 7, (70, 60, 120))
    circle(img, 503, 289, 7, (60, 210, 80))

    # Moving vehicles, two lanes
    vehicles = [
        ("car",   int((t*95+40) % 1150)-100, 482, (45,70,225)),
        ("car",   int((t*78+280) % 1200)-100, 535, (235,235,230)),
        ("truck", int((t*70+500) % 1450)-150, 482, (55,115,190)),
        ("bus",   int((t*55+850) % 1550)-160, 535, (80,140,50)),
        ("car",   int((t*110+720) % 1300)-100, 482, (40,155,225)),
    ]
    for kind, x, y, color in vehicles:
        vehicle(img, x, y, 1, kind, color)

    # Walking men
    walkers = [
        (120, (120, 75, 40)),
        (370, (55, 90, 135)),
        (650, (95, 100, 105)),
        (850, (60, 100, 80)),
    ]
    for j, (base_x, shirt) in enumerate(walkers):
        x = int((base_x + t*(12+2*j)) % (W+50))
        person(img, x, 434, t+j, shirt)

    writer.write(img)

writer.release()

# Verify the resulting video can be opened
cap = cv2.VideoCapture(OUTPUT)
ok, first_frame = cap.read()
cap.release()

if not ok:
    raise RuntimeError("Video was created but could not be read back.")

print(f"Success! Video saved to: {OUTPUT}")
print(f"Resolution: {W}x{H} | FPS: {FPS} | Duration: {SECONDS}s")
