# CCTV Analytics

מערכת אנליטיקה לצילומי מצלמות במעגל סגור (CCTV). המערכת מזהה אובייקטים ורכבים בסרטונים, מרכזת את כל הממצאים בבסיס נתונים אחד, ומאפשרת לתחקר אותם, להשוות ביניהם ולאתר חריגות משגרה דרך ממשק אחד.

## הסיפור: מצילום לתובנה

```
 מצלמות / סרטונים
        │
        ├──► [detection]  זיהוי אובייקטים לפי דרישה  ──┐   אנשים, רכבים וכל מחלקה מוגדרת
        │                  + מעקב (tracking)            │
        │                                               ▼
        └──► [lpr]        זיהוי רכבים + לוחית רישוי ──► [forensic] ◄──► PostgreSQL
                          (OCR)                          │              (בסיס נתונים מרכזי)
                                                         ▼
                                       ממשק תחקור: חיפוש, ציר זמן, השוואות,
                                       חריגות מול baseline, חורי תיעוד, יומן ביקורת
```

1. **קליטה וזיהוי.** סרטון נכנס לאחד ממנועי הזיהוי. מנוע `detection` מזהה כל סוג אובייקט שהארגון מגדיר, ומנוע `lpr` מתמחה ברכבים ובקריאת לוחיות רישוי.
2. **ריכוז.** כל ממצא נשלח אוטומטית כ"אירוע" לבסיס הנתונים המרכזי, עם המצלמה, האזור, הזמן, סוג האובייקט, מזהה הישות (למשל מספר רישוי) ורמת הביטחון.
3. **תחקור.** בממשק `forensic` אפשר לחפש לפי זמן, מצלמה, אזור או לוחית, לראות ציר זמן של ישות בין מצלמות, להשוות בין תקופות, לזהות חריגות מול תקופת בסיס, ולאתר זמנים שבהם מצלמה לא הקליטה.

## מיפוי ליכולות שהתבקשו

| # | יכולת נדרשת | איפה במערכת | סטטוס |
|---|-------------|--------------|-------|
| 1 | אנליטיקה וזיהוי אובייקטים מוגדרים במרחב, לפי דרישה משתנה | `detection`: מחלקות נבחרות לכל בקשה (`object_classes`) או בהגדרה (`DETECTION_CLASSES`), מודל ניתן להחלפה | ✅ |
| 2 | התרעה פרואקטיבית לפי חוקיות | אירועים עם רמת חומרה (`severity`); מנגנון חוקים ושליחת התראות | 🟡 חלקי |
| 3 | ריכוז בסיס נתונים | `forensic` + PostgreSQL: מצלמות, קטעי וידאו, אירועים, יומן ביקורת | ✅ |
| 4 | תחקור והשוואת ממצאים, כולל היסטורית | `forensic`: `/search`, `/timeline`, `/compare` (ישות מול ישות, תקופה מול תקופה) | ✅ |
| 5 | זיהוי דפוסים וחריגות משגרה | `forensic`: `/compare` במצב `vs_baseline` (חריגה של 30% ומעלה), זיהוי חורי תיעוד | ✅ |
| 6 | זיהוי רכבים לפי לוחית רישוי | `lpr`: YOLOv8, מעקב SORT ו-EasyOCR, והפרסום ל-`forensic` | ✅ |
| 7 | דשבורד מנהלים | ממשק `forensic` (http://localhost:8005) עם פילוחים לפי סוג ומצלמה | 🟡 בסיסי |

### דרישות פריסה
- **פריסה בתצורות שונות.** כל רכיב ארוז כקונטיינר, וכל המערכת עולה בפקודה אחת: על שרת של הארגון, בענן ארגוני או בענן ציבורי. אין תלות בשירות ענן מסוים.
- **סביבה סגורה.** המודלים נטענים מקבצים מקומיים, וההרצה עצמה לא דורשת גישה לאינטרנט.
- **תפעול עצמאי.** כל ההגדרות (סיסמאות, פורטים, מודל, מחלקות, שמות מצלמות) נמצאות בקובץ `.env` אחד, בלי לשנות קוד.

## מבנה הריפו

```
cctv-analytics/
├── docker-compose.yml          # כל המערכת
├── .env.example                # כל ההגדרות במקום אחד
├── certs/                      # תעודות CA של הארגון (לא בגיט)
├── tools/
│   └── generate_demo_video.py  # יצירת סרטון רחוב סינתטי לבדיקות
└── services/
    ├── forensic/               # בסיס נתונים מרכזי, API תחקור וממשק
    │   ├── app/                #   FastAPI + SQLAlchemy + ממשק סטטי
    │   └── verify.sh           #   בדיקת קצה-לקצה של ה-API
    ├── detection/              # זיהוי אובייקטים לפי דרישה (API)
    │   ├── app/                #   FastAPI + YOLO + ByteTrack
    │   └── models/             #   משקולות (לא בגיט)
    └── lpr/                    # זיהוי לוחיות רישוי (batch)
        ├── main.py …           #   pipeline זיהוי, אינטרפולציה והצגה
        ├── publish_events.py   #   פרסום הלוחיות ל-forensic
        ├── models/  easyocr_models/  data/videos/  output/   (לא בגיט)
```

## הרצה

### 1. דרישות מקדימות
- Docker עם Docker Compose v2.20 ומעלה (לצורך `additional_contexts`).
- **מודלים.** אינם בגיט, ויש להעתיק אותם למקומם:

| קובץ | מיקום | מקור |
|------|-------|------|
| `yolo11s.pt` | `services/detection/models/` | [Ultralytics](https://github.com/ultralytics/assets/releases) |
| `yolov8n.pt` | `services/lpr/models/` | [Ultralytics](https://github.com/ultralytics/assets/releases) |
| `plate_detector.pt` | `services/lpr/models/` | YOLOv8 שאומן על [dataset לוחיות של Roboflow](https://universe.roboflow.com/roboflow-universe-projects/license-plate-recognition-rxg4e/dataset/4) (זמין מהצוות) |
| `craft_mlt_25k.pth`, `english_g2.pth` | `services/lpr/easyocr_models/` | [EasyOCR Model Hub](https://www.jaided.ai/easyocr/modelhub/) |

- **תעודות (רק ברשת עם פרוקסי SSL ארגוני).** קבצי `.crt` שמונחים ב-`certs/` נכנסים לכל ה-images בזמן הבנייה. אם אין צורך, משאירים את התיקייה ריקה.

### 2. הרמת המערכת

```bash
cp .env.example .env                 # ועדכון סיסמה
docker compose up -d --build         # db + forensic + detection
```

| שירות | כתובת |
|-------|-------|
| ממשק תחקור | http://localhost:8005 |
| API תחקור (Swagger) | http://localhost:8005/docs |
| API זיהוי (Swagger) | http://localhost:8000/docs |

### 3. נתוני הדגמה (אופציונלי)

```bash
docker compose exec forensic python -m app.seed   # 3 מצלמות, 14 ימי היסטוריה, יום חריג וחורי תיעוד
```

### 4. הרצת מנועי הזיהוי

**זיהוי אובייקטים**: שולחים תמונה או סרטון. הממצאים נשמרים ומופיעים מיד בממשק התחקור:

```bash
curl -F "file=@street.mp4" -F "object_classes=person,car,truck" \
     -F "camera_name=מצלמת כניסה" -F "zone=כניסה" \
     http://localhost:8000/api/jobs
```

את התוצר המסומן מורידים מ-`GET /api/jobs/{job_id}/result`.

**זיהוי לוחיות רישוי**: מניחים סרטון ב-`services/lpr/data/videos/` ומריצים:

```bash
LPR_VIDEO_PATH=data/videos/my_video.mp4 docker compose run --rm lpr
```

התוצרים נכתבים ל-`services/lpr/output/` (קובצי CSV וסרטון מסומן), וכל לוחית מזוהה מתפרסמת כאירוע `vehicle_seen` בבסיס הנתונים המרכזי.

### 5. בדיקת תקינות

```bash
bash services/forensic/verify.sh
```

## הגדרות עיקריות

כל ההגדרות נמצאות ב-[`.env.example`](.env.example). העיקריות:

| משתנה | תיאור |
|-------|-------|
| `POSTGRES_PASSWORD` | סיסמת בסיס הנתונים (חובה לשנות בייצור) |
| `DETECTION_MODEL` | קובץ המודל ב-`services/detection/models/` |
| `DETECTION_CLASSES` | מחלקות לזיהוי, מופרדות בפסיק (ריק = הכול) |
| `DETECTION_CONFIDENCE` | סף ביטחון מינימלי |
| `*_CAMERA_NAME`, `*_CAMERA_ZONE` | שם המצלמה והאזור שמוצמדים לאירועים |
| `LPR_VIDEO_START_TS` | שעת תחילת ההקלטה (ISO 8601), כדי שהאירועים יקבלו את הזמן האמיתי |

## הוספת רכיב חדש (לצוות)
1. יוצרים תיקייה `services/<שם>/` ובה `Dockerfile` שמתחיל בבלוק התעודות המשותף (ראו את הקיימים).
2. מוסיפים את השירות ל-`docker-compose.yml` עם `<<: *build-certs`.
3. ממצאים נשלחים ל-`forensic` דרך `POST /events/bulk`, כדי שיופיעו בתחקור.
4. מודלים ונתונים נטענים כ-volumes ואינם נכנסים לגיט.

## מגבלות ידועות ושלבים הבאים
- **התרעות.** אין עדיין מנגנון חוקים ושליחת התראות (מייל או webhook). אפשר לבנות אותו על בסיס מצב `vs_baseline` ושדה `severity`.
- **פורמט לוחית.** `lpr/util.py` מאמת כרגע פורמט בריטי, ולכן לוחיות ישראליות יידחו. צריך להתאים את הפונקציה לפני שימוש בארץ.
- **וידאו חי.** שני המנועים מעבדים קבצים ולא זרם RTSP.
- **CPU בלבד.** לעבודה עם GPU מחליפים את בסיס ה-image.
- **הרשאות.** ה-API ללא אימות משתמשים. השדה `X-User` נרשם ביומן הביקורת בלבד.

## רישוי וקרדיט
- `lpr` מבוסס על [Automatic-License-Plate-Recognition-using-YOLOv8](https://github.com/Muhammad-Zeerak-Khan/Automatic-License-Plate-Recognition-using-YOLOv8) (MIT, ראו [`services/lpr/LICENSE`](services/lpr/LICENSE)). `sort_lite.py` מבוסס על [SORT](https://github.com/abewley/sort) (GPL-3.0).
- מודלי YOLO של [Ultralytics](https://github.com/ultralytics/ultralytics) (AGPL-3.0). לשימוש מסחרי צריך לבדוק את תנאי הרישיון.
