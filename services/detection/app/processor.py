import logging
from pathlib import Path

import cv2

from .config import settings
from .db import db
from .detectors import Detection, get_detector

logger = logging.getLogger(__name__)

VIDEO_SAMPLE_INTERVAL_SECONDS = 1.0

def filter_detections(detections, selected_classes):
    if not selected_classes:
        return detections

    return [
        detection
        for detection in detections
        if detection.label.lower() in selected_classes
    ]

def _draw_detections(frame, detections: list[Detection]):
    annotated = frame.copy()

    for detection in detections:
        x1, y1, x2, y2 = map(
            int,
            (detection.x1, detection.y1, detection.x2, detection.y2),
        )

        cv2.rectangle(
            annotated,
            (x1, y1),
            (x2, y2),
            (0, 255, 0),
            2,
        )

        track_id = getattr(detection, "track_id", None)
        track_text = f" ID:{track_id}" if track_id is not None else ""
        label = f"{detection.label}{track_text} {detection.confidence:.2f}"

        cv2.putText(
            annotated,
            label,
            (x1, max(20, y1 - 8)),
            cv2.FONT_HERSHEY_SIMPLEX,
            0.6,
            (0, 255, 0),
            2,
        )

    return annotated

def process_image(
    job_id: str,
    image_path: str,
    selected_classes: set[str] | None = None,
) -> dict:
    """Analyze an image, save the annotated result, and store detections."""
    source_path = Path(image_path)
    output_path = None

    try:
        if not source_path.is_file():
            raise FileNotFoundError(f"Image file not found: {source_path}")

        image = cv2.imread(str(source_path))

        if image is None:
            raise ValueError("The uploaded file is not a valid image")

        detections: list[Detection] = get_detector().detect(image)
        detections = filter_detections(
            detections,
            selected_classes,
        )
        annotated = _draw_detections(image, detections)

        results_dir = Path(settings.storage_dir) / "results"
        results_dir.mkdir(parents=True, exist_ok=True)
        output_path = results_dir / f"{job_id}_annotated.jpg"

        if not cv2.imwrite(str(output_path), annotated):
            raise RuntimeError("Failed to save annotated image")

        with db() as conn:
            job = conn.execute(
                "SELECT id FROM jobs WHERE id = ?",
                (job_id,),
            ).fetchone()

            if job is None:
                raise ValueError(f"Job not found: {job_id}")

            conn.execute(
                """
                UPDATE jobs
                SET status = 'completed',
                    finished_at = datetime('now'),
                    error = NULL,
                    result_path = ?
                WHERE id = ?
                """,
                (str(output_path), job_id),
            )

            for detection in detections:
                conn.execute(
                    """
                    INSERT INTO detections (
                        job_id, frame_number, timestamp_seconds,
                        label, confidence, x1, y1, x2, y2,
                        preview_path
                    )
                    VALUES (?, 0, 0, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        job_id,
                        detection.label,
                        float(detection.confidence),
                        float(detection.x1),
                        float(detection.y1),
                        float(detection.x2),
                        float(detection.y2),
                        str(output_path),
                    ),
                )

        return {
            "job_id": job_id,
            "status": "completed",
            "media_type": "image",
            "result_path": str(output_path),
            "detections_count": len(detections),
            "detections": [
                {
                    "label": d.label,
                    "confidence": float(d.confidence),
                    "bbox": [float(d.x1), float(d.y1), float(d.x2), float(d.y2)],
                }
                for d in detections
            ],
        }

    except Exception as exc:
        logger.exception("Image processing failed for job %s", job_id)

        with db() as conn:
            conn.execute(
                """
                UPDATE jobs
                SET status = 'failed',
                    finished_at = datetime('now'),
                    error = ?
                WHERE id = ?
                """,
                (str(exc)[:1000], job_id),
            )

        if output_path is not None:
            output_path.unlink(missing_ok=True)

        raise


def process_video(
    job_id: str,
    video_path: str,
    sample_interval_seconds: float = VIDEO_SAMPLE_INTERVAL_SECONDS,
    selected_classes: set[str] | None = None,
) -> dict:
    """Process sampled video frames with object tracking."""

    source_path = Path(video_path)
    output_path = None
    capture = None
    writer = None

    try:
        if not source_path.is_file():
            raise FileNotFoundError(f"Video not found: {source_path}")

        if sample_interval_seconds <= 0:
            raise ValueError("Sample interval must be greater than zero")

        capture = cv2.VideoCapture(str(source_path))

        if not capture.isOpened():
            raise ValueError("Could not open uploaded video")

        fps = capture.get(cv2.CAP_PROP_FPS)
        width = int(capture.get(cv2.CAP_PROP_FRAME_WIDTH))
        height = int(capture.get(cv2.CAP_PROP_FRAME_HEIGHT))
        frame_count = int(capture.get(cv2.CAP_PROP_FRAME_COUNT))

        if fps <= 0 or width <= 0 or height <= 0:
            raise ValueError("Invalid video metadata")

        duration_seconds = frame_count / fps if frame_count > 0 else None
        sample_every_n_frames = max(1, round(fps * sample_interval_seconds))

        results_dir = Path(settings.storage_dir) / "results"
        results_dir.mkdir(parents=True, exist_ok=True)
        output_path = results_dir / f"{job_id}_annotated.mp4"

        writer = cv2.VideoWriter(
            str(output_path),
            cv2.VideoWriter_fourcc(*"mp4v"),
            fps,
            (width, height),
        )

        if not writer.isOpened():
            raise RuntimeError("Could not create annotated video")

        detector = get_detector()
        track_method = getattr(detector, "detect_tracked", None)

        saved_detections = []
        sampled_frames = 0
        processed_frames = 0
        frame_number = 0

        while True:
            ok, frame = capture.read()
            if not ok:
                break

            timestamp_seconds = frame_number / fps

            if callable(track_method):
                frame_detections = track_method(frame)
            else:
                frame_detections = detector.detect(frame)
            frame_detections = filter_detections(
                frame_detections,
                selected_classes,
            )
            # Draw detections on every output frame.
            frame = _draw_detections(frame, frame_detections)
            writer.write(frame)

            # Store detections approximately once per second.
            if frame_number % sample_every_n_frames == 0:
                sampled_frames += 1

                for detection in frame_detections:
                    saved_detections.append({
                        "frame_number": frame_number,
                        "timestamp_seconds": timestamp_seconds,
                        "label": detection.label,
                        "confidence": float(detection.confidence),
                        "x1": float(detection.x1),
                        "y1": float(detection.y1),
                        "x2": float(detection.x2),
                        "y2": float(detection.y2),
                        "track_id": getattr(detection, "track_id", None),
                    })

            processed_frames += 1
            frame_number += 1
        # while True:
            # ok, frame = capture.read()
            # if not ok:
            #     break

            # if frame_number % sample_every_n_frames == 0:
            #     timestamp_seconds = frame_number / fps

            #     if callable(track_method):
            #         frame_detections = track_method(frame)
            #     else:
            #         # Detectors without tracking still work.
            #         frame_detections = detector.detect(frame)

            #     sampled_frames += 1

            #     for detection in frame_detections:
            #         track_id = getattr(detection, "track_id", None)

            #         saved_detections.append({
            #             "frame_number": frame_number,
            #             "timestamp_seconds": timestamp_seconds,
            #             "label": detection.label,
            #             "confidence": float(detection.confidence),
            #             "x1": float(detection.x1),
            #             "y1": float(detection.y1),
            #             "x2": float(detection.x2),
            #             "y2": float(detection.y2),
            #             "track_id": track_id,
            #         })

            #     frame = _draw_detections(frame, frame_detections)

            # writer.write(frame)
            # processed_frames += 1
            # frame_number += 1

        capture.release()
        capture = None
        writer.release()
        writer = None

        if processed_frames == 0:
            raise ValueError("Video contains no readable frames")

        with db() as conn:
            job = conn.execute(
                "SELECT id FROM jobs WHERE id = ?",
                (job_id,),
            ).fetchone()

            if job is None:
                raise ValueError(f"Job not found: {job_id}")

            for detection in saved_detections:
                conn.execute(
                    """
                    INSERT INTO detections (
                        job_id,
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
                    )
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, NULL, ?)
                    """,
                    (
                        job_id,
                        detection["frame_number"],
                        detection["timestamp_seconds"],
                        detection["label"],
                        detection["confidence"],
                        detection["x1"],
                        detection["y1"],
                        detection["x2"],
                        detection["y2"],
                        detection["track_id"],
                    ),
                )

            conn.execute(
                """
                UPDATE jobs
                SET status = 'completed',
                    finished_at = datetime('now'),
                    error = NULL,
                    result_path = ?
                WHERE id = ?
                """,
                (str(output_path), job_id),
            )

        return {
            "job_id": job_id,
            "status": "completed",
            "media_type": "video",
            "result_path": str(output_path),
            "duration_seconds": duration_seconds,
            "frames_processed": processed_frames,
            "frames_sampled": sampled_frames,
            "sample_interval_seconds": sample_interval_seconds,
            "detections_count": len(saved_detections),
            "tracked_detections_count": sum(
                d["track_id"] is not None for d in saved_detections
            ),
        }

    except Exception as exc:
        logger.exception("Video processing failed for job %s", job_id)

        with db() as conn:
            conn.execute(
                """
                UPDATE jobs
                SET status = 'failed',
                    finished_at = datetime('now'),
                    error = ?
                WHERE id = ?
                """,
                (str(exc)[:1000], job_id),
            )

        if output_path is not None:
            output_path.unlink(missing_ok=True)

        raise

    finally:
        if capture is not None:
            capture.release()
        if writer is not None:
            writer.release()
