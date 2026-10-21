from .detection.base import Detection, Detector
from dataclasses import dataclass

from pathlib import Path

import cv2
import numpy as np

from .config import settings


# @dataclass
# class Detection:
    # label: str
    # confidence: float

    # x1: float
    # y1: float
    # x2: float
    # y2: float


class DetectorError(RuntimeError):
    pass


class OpenCVHOGDetector:
    """
    Basic pedestrian detector.

    This detector identifies people only.
    It is not a general-purpose object detector.
    """

    def __init__(self):
        self.hog = cv2.HOGDescriptor()

        self.hog.setSVMDetector(
            cv2.HOGDescriptor_getDefaultPeopleDetector()
        )

    def detect(self, frame: np.ndarray) -> list[Detection]:
        boxes, weights = self.hog.detectMultiScale(
            frame,
            winStride=(8, 8),
            padding=(8, 8),
            scale=1.05,
        )

        detections = []

        for (x, y, width, height), weight in zip(boxes, weights):
            detections.append(
                Detection(
                    label="person",
                    confidence=float(
                        max(0.0, min(1.0, weight))
                    ),
                    x1=float(x),
                    y1=float(y),
                    x2=float(x + width),
                    y2=float(y + height),
                )
            )

        return detections


class UltralyticsDetector:
    """Adapter for an organization-approved object detection model."""

    def __init__(self):
        model_path = Path(settings.yolo_model_path)

        if not model_path.is_file():
            raise DetectorError(
                f"Model not found at {model_path}. "
                "Mount approved weights and configure YOLO_MODEL_PATH."
            )

        try:
            from ultralytics import YOLO
        except ImportError as exc:
            raise DetectorError(
                "Ultralytics is not installed."
            ) from exc

        self.model = YOLO(str(model_path))
        self.confidence_threshold = settings.detection_confidence

        configured_classes = settings.detection_classes.strip()
        self.allowed_labels = (
            {
                label.strip().lower()
                for label in configured_classes.split(",")
                if label.strip()
            }
            if configured_classes
            else None
        )

    def detect(self, frame: np.ndarray) -> list[Detection]:
        result = self.model.predict(
            frame,
            conf=self.confidence_threshold,
            verbose=False,
        )[0]

        detections: list[Detection] = []

        if result.boxes is None:
            return detections

        for box in result.boxes:
            x1, y1, x2, y2 = [
                float(value)
                for value in box.xyxy[0].tolist()
            ]

            class_id = int(box.cls[0].item())
            label = str(result.names.get(class_id, class_id))
            confidence = float(box.conf[0].item())

            if (
                self.allowed_labels is not None
                and label.lower() not in self.allowed_labels
            ):
                continue

            detections.append(
                Detection(
                    label=label,
                    confidence=confidence,
                    x1=x1,
                    y1=y1,
                    x2=x2,
                    y2=y2,
                )
            )

        return detections

    def detect_tracked(self, frame: np.ndarray) -> list[Detection]:
        result = self.model.track(
            frame,
            persist=True,
            tracker="bytetrack.yaml",
            conf=self.confidence_threshold,
            verbose=False,
        )[0]

        detections: list[Detection] = []

        if result.boxes is None:
            return detections

        boxes = result.boxes
        ids = boxes.id

        for index, box in enumerate(boxes):
            x1, y1, x2, y2 = [
                float(value)
                for value in box.xyxy[0].tolist()
            ]

            class_id = int(box.cls[0].item())
            label = str(result.names.get(class_id, class_id))
            confidence = float(box.conf[0].item())

            if (
                self.allowed_labels is not None
                and label.lower() not in self.allowed_labels
            ):
                continue

            track_id = (
                int(ids[index].item())
                if ids is not None
                else None
            )

            detections.append(
                Detection(
                    label=label,
                    confidence=confidence,
                    x1=x1,
                    y1=y1,
                    x2=x2,
                    y2=y2,
                    track_id=track_id,
                )
            )

        return detections
_detector = None


def get_detector():
    global _detector

    if _detector is None:
        detector_name = settings.detector.lower()

        if detector_name == "opencv_hog":
            _detector = OpenCVHOGDetector()

        elif detector_name == "ultralytics":
            _detector = UltralyticsDetector()

        else:
            raise DetectorError(
                f"Unsupported detector: {settings.detector}"
            )

    return _detector
