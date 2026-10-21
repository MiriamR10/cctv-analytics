from dataclasses import dataclass
from typing import Protocol

import numpy as np


@dataclass(frozen=True, slots=True)
class Detection:
    label: str
    confidence: float
    x1: float
    y1: float
    x2: float
    y2: float
    track_id: int | None = None

    def to_dict(self) -> dict:
        return {
            "label": self.label,
            "confidence": self.confidence,
            "bbox": [self.x1, self.y1, self.x2, self.y2],
            "track_id": self.track_id,
        }



class Detector(Protocol):
    """Common interface for all object detection backends."""

    def detect(self, frame: np.ndarray) -> list[Detection]:
        """Return detected objects for one image/frame."""
        ...
