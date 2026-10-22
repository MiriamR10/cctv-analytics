"""The contract between the platform and any anomaly-detection model.

To plug in a model, subclass ``AnomalyDetector``, implement the four methods, and set
``DETECTOR_CLASS=your.module:YourClass``. Then run ``pytest tests/contract``.
See MODEL_REQUIREMENTS.md for the full requirements.
"""

from abc import ABC, abstractmethod
from pathlib import Path
from typing import Any, ClassVar

import pandas as pd

# --- Input columns (features frame) -------------------------------------------------
SERIES_KEY = "series_key"  # str: "<source_id>|<zone_id>|<entity_type>"
WINDOW_START = "window_start"  # datetime64[ns, UTC]: start of the time window
HOUR_OF_WEEK = "hour_of_week"  # int 0..167 in local time (Mon 00:00 = 0)
DAY_OF_WEEK = "day_of_week"  # int 0..6 in local time (Mon = 0)
HOUR_OF_DAY = "hour_of_day"  # int 0..23 in local time
META_COLUMNS = (SERIES_KEY, WINDOW_START, HOUR_OF_WEEK, DAY_OF_WEEK, HOUR_OF_DAY)
# Every other numeric column is a feature. NaN means "not measurable in this window"
# (e.g. average dwell time of a window with no events).

# --- Optional training labels (operator feedback) ----------------------------------
LABEL = "label"  # 1 = confirmed anomaly, 0 = false positive

# --- Output columns (scores frame) -------------------------------------------------
SCORE = "score"  # float in [0, 1], higher = more anomalous
IS_ANOMALY = "is_anomaly"  # bool
EXPLANATION = "explanation"  # dict, see ``make_explanation``
OUTPUT_COLUMNS = (SERIES_KEY, WINDOW_START, SCORE, IS_ANOMALY, EXPLANATION)


class AnomalyDetector(ABC):
    """Learns the routine of each series and scores new windows against it."""

    name: ClassVar[str] = "detector"
    version: ClassVar[str] = "0.0.0"

    def __init__(self, **params: Any) -> None:
        self.params = params

    @abstractmethod
    def fit(
        self,
        features: pd.DataFrame,
        labels: pd.DataFrame | None = None,
    ) -> dict[str, Any]:
        """Learn the routine from historical windows.

        ``labels`` (optional) has SERIES_KEY, WINDOW_START, LABEL. Returns training
        metrics (JSON-serializable) that are stored with the model version.
        """

    @abstractmethod
    def score(self, features: pd.DataFrame) -> pd.DataFrame:
        """Return one row per input row, with exactly ``OUTPUT_COLUMNS``.

        Unknown series or insufficient history must yield ``is_anomaly=False``.
        """

    @property
    def context_windows(self) -> int:
        """Windows of recent history ``score_with_context`` needs before ``features``.

        Detectors that keep their own profile of the routine (the default) need 0.
        Forecasting detectors return e.g. 2 weeks, and the platform loads it for them.
        """
        return 0

    def score_with_context(
        self,
        features: pd.DataFrame,
        context: pd.DataFrame | None,
    ) -> pd.DataFrame:
        """Like ``score``, with up to ``context_windows`` earlier windows (same
        columns as ``features``). Output covers ``features`` rows only."""
        return self.score(features)

    @abstractmethod
    def save(self, directory: Path) -> None:
        """Persist everything needed by ``load`` into ``directory``."""

    @classmethod
    @abstractmethod
    def load(cls, directory: Path) -> "AnomalyDetector":
        """Restore a detector saved by ``save``."""


def feature_columns(features: pd.DataFrame) -> list[str]:
    return [
        column
        for column in features.columns
        if column not in META_COLUMNS
        and column != LABEL
        and pd.api.types.is_numeric_dtype(features[column])
    ]


def make_explanation(
    reason: str,
    contributions: list[dict[str, Any]] | None = None,
    **extra: Any,
) -> dict[str, Any]:
    """Standard explanation shape shown to operators.

    ``contributions`` items: {"feature", "observed", "expected", "deviation",
    "direction": "above" | "below"}, most significant first.
    """
    return {"reason": reason, "contributions": contributions or [], **extra}


def empty_scores() -> pd.DataFrame:
    return pd.DataFrame(
        {
            SERIES_KEY: pd.Series(dtype="object"),
            WINDOW_START: pd.Series(dtype="datetime64[ns, UTC]"),
            SCORE: pd.Series(dtype="float64"),
            IS_ANOMALY: pd.Series(dtype="bool"),
            EXPLANATION: pd.Series(dtype="object"),
        },
    )
