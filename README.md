# CCTV Analytics

מערכת אנליטיקה לצילומי מצלמות במעגל סגור (CCTV). המערכת בנויה משירותים (services) עצמאיים: כל יכולת יושבת בתיקייה משלה עם Dockerfile משלה, וכולן מתוזמרות יחד בקובץ `docker-compose.yml` אחד.

## יכולות היעד

| # | יכולת | שירות | סטטוס |
|---|-------|-------|-------|
| 1 | אנליטיקה וזיהוי אובייקטים מוגדרים במרחב, לפי דרישה משתנה | — | מתוכנן |
| 2 | התרעה פרואקטיבית לפי חוקיות בסביבה מתועדת | — | מתוכנן |
| 3 | ריכוז בסיס נתונים | — | מתוכנן |
| 4 | תחקור והשוואת ממצאים, כולל היסטורית | — | מתוכנן |
| 5 | זיהוי דפוסים וחריגות משגרה ומחוקיות מוגדרת | — | מתוכנן |
| 6 | זיהוי רכבים לפי לוחית רישוי (LPR) | [`services/lpr`](services/lpr) | ✅ גרסה ראשונה |
| 7 | דשבורד מנהלים לריכוז נתונים וסטטיסטיקות | — | מתוכנן |

### דרישות פריסה
- **פריסה בתצורות שונות**: על שרתי הארגון, בענן ארגוני או בענן ציבורי. כל השירותים ארוזים כקונטיינרים ואינם תלויים בספק ענן מסוים.
- **תפעול עצמאי**: הצוות הטכני של הארגון יכול להרים ולתפעל את המערכת בעצמו לאחר הכשרה. ההגדרות נקבעות דרך משתני סביבה וקובץ `.env`, בלי לשנות קוד.

## מבנה הריפו

```
cctv-analytics/
├── docker-compose.yml        # תזמור כל השירותים
├── .env.example              # תבנית להגדרות סביבה
├── README.md
└── services/
    └── lpr/                  # זיהוי לוחיות רישוי
        ├── Dockerfile
        ├── requirements.txt
        ├── run_pipeline.sh   # מריץ את כל שלבי ה-pipeline לפי הסדר
        ├── main.py           # זיהוי רכבים, מעקב (SORT), זיהוי לוחית ו-OCR
        ├── add_missing_data.py  # השלמת פריימים חסרים באינטרפולציה
        ├── visualize.py      # יצירת סרטון מסומן
        ├── util.py           # OCR, אימות פורמט לוחית, כתיבת CSV
        ├── sort_lite.py      # מימוש SORT ל-tracking
        ├── models/           # משקולות YOLO (לא בגיט)
        ├── easyocr_models/   # מודלי EasyOCR (לא בגיט)
        ├── data/videos/      # סרטוני קלט (לא בגיט)
        └── output/           # תוצרים (לא בגיט)
```

## הוספת שירות חדש (לצוות)

1. יוצרים תיקייה `services/<שם-השירות>/` ובה `Dockerfile` ו-`requirements.txt`.
2. מוסיפים את השירות ל-`docker-compose.yml` באותו מבנה של `lpr`.
3. מודלים, נתונים ותוצרים נטענים כ-volumes ולא נכנסים לגיט. ה-`.gitignore` כבר מכסה `models/`, `data/` ו-`output/` בכל שירות.
4. כל נתיב או פרמטר שמשתנה בין סביבות מגיע ממשתנה סביבה.
5. מעדכנים את טבלת היכולות למעלה.

---

## שירות LPR: זיהוי לוחיות רישוי

### איך זה עובד

```
וידאו ─► YOLOv8n (רכבים) ─► SORT (מעקב) ─► YOLOv8 (לוחיות) ─► EasyOCR ─► CSV
                                                                           │
                       סרטון מסומן ◄─ visualize.py ◄─ add_missing_data.py ◄┘
```

1. **`main.py`**: לכל פריים מזהה רכבים (מחלקות COCO: מכונית, אופנוע, אוטובוס ומשאית), עוקב אחריהם עם SORT, מזהה לוחיות, משייך כל לוחית לרכב שמכיל אותה ומפענח אותה עם EasyOCR. הפלט הוא `results.csv`.
2. **`add_missing_data.py`**: משלים באינטרפולציה לינארית את התיבות בפריימים שבהם הזיהוי נפל. הפלט הוא `results_interpolated.csv`.
3. **`visualize.py`**: בוחר לכל רכב את הקריאה עם רמת הביטחון הגבוהה ביותר ומייצר סרטון מסומן עם חיתוך מוגדל של הלוחית.

### מודלים נדרשים

המודלים אינם בגיט, ויש למקם אותם ידנית:

| קובץ | מיקום | מקור |
|------|-------|------|
| `yolov8n.pt` | `services/lpr/models/` | [Ultralytics YOLOv8](https://github.com/ultralytics/assets/releases) |
| `plate_detector.pt` | `services/lpr/models/` | מודל YOLOv8 שאומן על [dataset לוחיות של Roboflow](https://universe.roboflow.com/roboflow-universe-projects/license-plate-recognition-rxg4e/dataset/4) (זמין מהצוות) |
| `craft_mlt_25k.pth`, `english_g2.pth` | `services/lpr/easyocr_models/` | [EasyOCR Model Hub](https://www.jaided.ai/easyocr/modelhub/) |

> EasyOCR מוגדר עם `download_enabled=False`, כך שהשירות רץ גם בסביבה סגורה בלי גישה לאינטרנט. מסיבה זו המודלים חייבים להיות במקומם מראש.

### הרצה עם Docker (מומלץ)

```bash
# 1. הגדרות (אופציונלי)
cp .env.example .env          # בסביבת נטפרי: NETFREE=true

# 2. מקמו מודלים וסרטון קלט ב-services/lpr/models, easyocr_models, data/videos

# 3. בנייה והרצה
docker compose up --build lpr
```

התוצרים נכתבים ל-`services/lpr/output/`:
- `results.csv`: זיהויים גולמיים לכל פריים
- `results_interpolated.csv`: אחרי השלמת פריימים חסרים
- `annotated.mp4`: סרטון מסומן

הרצה על סרטון אחר:

```bash
LPR_VIDEO_PATH=data/videos/my_video.mp4 docker compose up lpr
```

### הרצה מקומית (פיתוח)

```bash
cd services/lpr
python -m venv .venv && source .venv/bin/activate   # ב-Windows: .venv\Scripts\activate
pip install -r requirements.txt
export EASYOCR_MODELS=./easyocr_models               # ב-Windows: set EASYOCR_MODELS=.\easyocr_models
python main.py && python add_missing_data.py && python visualize.py
```

### משתני סביבה

| משתנה | ברירת מחדל (מקומית) | תיאור |
|-------|---------------------|-------|
| `VIDEO_PATH` | `data/videos/sample_10s.mp4` | סרטון קלט |
| `VEHICLE_MODEL` | `models/yolov8n.pt` | מודל זיהוי רכבים |
| `PLATE_MODEL` | `models/plate_detector.pt` | מודל זיהוי לוחיות |
| `EASYOCR_MODELS` | `/app/easyocr_models` | תיקיית מודלי EasyOCR |
| `RESULTS_CSV` | `./test.csv` | פלט שלב 1 |
| `INTERPOLATED_CSV` | `./test_interpolated.csv` | פלט שלב 2 |
| `OUTPUT_VIDEO` | `./out.mp4` | פלט שלב 3 |

### מגבלות ידועות
- **פורמט הלוחית**: `util.license_complies_format` מאמת כרגע פורמט בריטי (7 תווים: 2 אותיות, 2 ספרות, 3 אותיות). לוחיות ישראליות (7 או 8 ספרות) יידחו, וצריך להתאים את הפונקציה לפני שימוש בארץ.
- **עיבוד אופליין**: ה-pipeline מעבד קובץ וידאו שלם ולא זרם חי (RTSP).
- **CPU בלבד**: הקונטיינר משתמש ב-PyTorch ל-CPU. לעבודה עם GPU יש להחליף את בסיס ה-image ולהגדיר `gpu=True` ב-EasyOCR.

### רישוי וקרדיט
הבסיס של שירות ה-LPR מבוסס על [Automatic-License-Plate-Recognition-using-YOLOv8](https://github.com/Muhammad-Zeerak-Khan/Automatic-License-Plate-Recognition-using-YOLOv8) (MIT, ראו [`services/lpr/LICENSE`](services/lpr/LICENSE)). `sort_lite.py` מבוסס על [SORT](https://github.com/abewley/sort) (GPL-3.0).
