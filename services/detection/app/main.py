import logging
import uuid
from datetime import datetime, timezone
from pathlib import Path

from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from fastapi.responses import FileResponse

from .config import settings
from .db import db, init_db
from .processor import process_image, process_video
from .publisher import publish_job

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

app = FastAPI(
    title="CCTV Analytics MVP",
    version="0.2.0",
    description="Local CCTV image and video analytics API",
)

ALLOWED_IMAGE_EXTENSIONS = {
    ".jpg",
    ".jpeg",
    ".png",
    ".bmp",
    ".webp",
}

ALLOWED_VIDEO_EXTENSIONS = {
    ".mp4",
}

@app.on_event("startup")
def startup() -> None:
    init_db()


@app.get("/api/health")
def health() -> dict:
    return {
        "status": "ok",
        "detector": settings.detector,
    }


@app.post("/api/jobs")
async def create_job(
    file: UploadFile = File(...),
    object_classes: str = Form(default=""),
    camera_name: str = Form(default=settings.camera_name),
    zone: str = Form(default=settings.camera_zone),
) -> dict:
    filename = Path(file.filename or "upload").name
    extension = Path(filename).suffix.lower()

    if extension in ALLOWED_IMAGE_EXTENSIONS:
        media_type = "image"
    elif extension in ALLOWED_VIDEO_EXTENSIONS:
        media_type = "video"
    else:
        raise HTTPException(
            status_code=415,
            detail="Unsupported file type. Upload JPG, PNG, WEBP, BMP, or MP4.",
        )
    selected_classes = {
        item.strip().lower()
        for item in object_classes.split(",")
        if item.strip()
    }
    job_id = str(uuid.uuid4())
    upload_dir = Path(settings.storage_dir) / "uploads"
    upload_dir.mkdir(parents=True, exist_ok=True)

    file_path = upload_dir / f"{job_id}{extension}"
    max_bytes = settings.max_upload_mb * 1024 * 1024
    total_bytes = 0
    job_created = False

    try:
        with file_path.open("wb") as output:
            while chunk := await file.read(1024 * 1024):
                total_bytes += len(chunk)

                if total_bytes > max_bytes:
                    raise HTTPException(
                        status_code=413,
                        detail=(
                            f"File exceeds the "
                            f"{settings.max_upload_mb} MB limit."
                        ),
                    )

                output.write(chunk)

        if total_bytes == 0:
            raise HTTPException(
                status_code=400,
                detail="The uploaded file is empty.",
            )

        with db() as conn:
            conn.execute(
                """
                INSERT INTO jobs (
                    id, filename, media_type, status,
                    created_at, original_path
                )
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    job_id,
                    filename,
                    media_type,
                    "processing",
                    datetime.now(timezone.utc).isoformat(),
                    str(file_path),
                ),
            )
        job_created = True

        # Process image with selected object classes
        if media_type == "image":
            result = process_image(
                job_id,
                str(file_path),
                selected_classes=selected_classes,
            )
        else:
            # Process video with selected object classes
            result = process_video(
                job_id,
                str(file_path),
                selected_classes=selected_classes,
            )

        # Forward findings to the central forensic service.
        # A publishing failure must not fail an already completed job.
        try:
            result["published_events"] = publish_job(job_id, camera_name, zone)
        except Exception:
            logger.exception("Failed to publish job %s to forensic service", job_id)
            result["published_events"] = 0

        return result

    except HTTPException:
        file_path.unlink(missing_ok=True)
        raise

    except Exception as exc:
        logger.exception("Failed to process job %s", job_id)

        if job_created:
            with db() as conn:
                conn.execute(
                    """
                    UPDATE jobs
                    SET status = 'failed',
                        finished_at = ?,
                        error = ?
                    WHERE id = ?
                    """,
                    (
                        datetime.now(timezone.utc).isoformat(),
                        str(exc)[:1000],
                        job_id,
                    ),
                )

        raise HTTPException(
            status_code=500,
            detail="Media processing failed. Check backend logs.",
        ) from exc

    finally:
        await file.close()


@app.get("/api/jobs/{job_id}")
def get_job(job_id: str) -> dict:
    with db() as conn:
        job = conn.execute(
            "SELECT * FROM jobs WHERE id = ?",
            (job_id,),
        ).fetchone()

        if job is None:
            raise HTTPException(
                status_code=404,
                detail="Job not found.",
            )

        rows = conn.execute(
            """
            SELECT
                id,
                frame_number,
                timestamp_seconds,
                label,
                confidence,
                x1,
                y1,
                x2,
                y2,
                preview_path,
                track_id
            FROM detections
            WHERE job_id = ?
            ORDER BY frame_number, id
            """,
            (job_id,),
        ).fetchall()

    return {
        "job": dict(job),
        "detections": [dict(row) for row in rows],
        "detections_count": len(rows),
    }


@app.get("/api/jobs/{job_id}/result")
def get_job_result(job_id: str) -> FileResponse:
    with db() as conn:
        job = conn.execute(
            """
            SELECT status, result_path, media_type
            FROM jobs
            WHERE id = ?
            """,
            (job_id,),
        ).fetchone()

    if job is None:
        raise HTTPException(
            status_code=404,
            detail="Job not found.",
        )

    if job["status"] != "completed" or not job["result_path"]:
        raise HTTPException(
            status_code=409,
            detail="The result is not available yet.",
        )

    result_path = Path(job["result_path"])

    if not result_path.is_file():
        raise HTTPException(
            status_code=404,
            detail="Result file not found on disk.",
        )

    if job["media_type"] == "video":
        media_type = "video/mp4"
        filename = f"{job_id}_annotated.mp4"
    else:
        media_type = "image/jpeg"
        filename = f"{job_id}_annotated.jpg"

    return FileResponse(
        path=result_path,
        media_type=media_type,
        filename=filename,
    )
