"""Isolation Forest on seasonal deviations.

Two stages:
1. ``SeasonalBaselineDetector`` turns every feature into a signed deviation from the
   routine of that series at that hour of the week (z-score). This removes daily and
   weekly seasonality, which Isolation Forest cannot model by itself.
2. ``IsolationForest`` (scikit-learn) learns which *combinations* of deviations are
   normal, and flags rare combinations, e.g. a normal vehicle count with long dwell
   times and few distinct plates.

A window is anomalous if the forest flags it, or if a single feature deviates so
strongly that the baseline alone flags it (``single_feature_threshold``).

Requires: ``pip install ".[ml]"``.
"""

import json
from pathlib import Path
from typing import Any

import joblib
import numpy as np
import pandas as pd
from sklearn.ensemble import IsolationForest

from app.detection.base import (
    EXPLANATION,
    IS_ANOMALY,
    LABEL,
    SCORE,
    SERIES_KEY,
    WINDOW_START,
    AnomalyDetector,
    empty_scores,
    make_explanation,
)
from app.detection.baseline import SeasonalBaselineDetector

BASELINE_DIR = "baseline"
FOREST_FILE = "forest.joblib"
META_FILE = "meta.json"
# Score reported exactly at the decision threshold ("medium" severity).
SCORE_AT_THRESHOLD = 0.6


class IsolationForestDetector(AnomalyDetector):
    name = "isolation_forest"
    version = "1.0.0"

    def __init__(
        self,
        contamination: float = 0.005,
        n_estimators: int = 200,
        max_samples: int | str = 1024,
        z_clip: float = 10.0,
        min_combined_z: float = 2.5,
        single_feature_threshold: float = 5.0,
        random_state: int = 42,
        baseline_params: dict[str, Any] | None = None,
        max_contributions: int = 3,
    ) -> None:
        super().__init__(
            contamination=contamination,
            n_estimators=n_estimators,
            max_samples=max_samples,
            z_clip=z_clip,
            min_combined_z=min_combined_z,
            single_feature_threshold=single_feature_threshold,
            random_state=random_state,
            baseline_params=baseline_params or {},
            max_contributions=max_contributions,
        )
        self._baseline = SeasonalBaselineDetector(**self.params["baseline_params"])
        self._forest: IsolationForest | None = None
        self._threshold: float = 0.0  # on anomaly strength, see _strength
        self._features: list[str] = []

    # --- training --------------------------------------------------------------------

    def fit(
        self,
        features: pd.DataFrame,
        labels: pd.DataFrame | None = None,
    ) -> dict[str, Any]:
        baseline_metrics = self._baseline.fit(features, labels)
        self._features = self._baseline.features

        training = _without_confirmed(features, labels)
        z = self._z_matrix(training)
        has_history = z.notna().any(axis=1).to_numpy()
        matrix = self._prepare(z.loc[has_history])
        if len(matrix) < 100:
            raise ValueError(
                f"Only {len(matrix)} windows with history; need at least 100",
            )

        self._forest = IsolationForest(
            n_estimators=self.params["n_estimators"],
            max_samples=min(self.params["max_samples"], len(matrix))
            if isinstance(self.params["max_samples"], int)
            else self.params["max_samples"],
            contamination="auto",
            random_state=self.params["random_state"],
            n_jobs=1,  # deterministic and predictable on shared servers
        )
        self._forest.fit(matrix)

        strength = self._strength(matrix)
        self._threshold = float(
            np.quantile(strength, 1.0 - self.params["contamination"]),
        )
        return {
            **baseline_metrics,
            "forest_windows": int(len(matrix)),
            "threshold": self._threshold,
            "training_flag_rate": float((strength >= self._threshold).mean()),
        }

    # --- scoring ---------------------------------------------------------------------

    def score(self, features: pd.DataFrame) -> pd.DataFrame:
        if features.empty:
            return empty_scores()
        if self._forest is None:
            raise RuntimeError("Detector is not fitted")

        long = self._baseline.deviations(features)
        z = self._z_matrix(features, long)
        has_history = z.notna().any(axis=1).to_numpy()
        abs_z = z.abs()
        max_abs_z = abs_z.max(axis=1).fillna(0.0).to_numpy()
        combined_z = np.sqrt((z.fillna(0.0) ** 2).sum(axis=1)).to_numpy()

        strength = np.zeros(len(features))
        if has_history.any():
            strength[has_history] = self._strength(
                self._prepare(z.loc[has_history]),
            )

        forest_flag = (
            has_history
            & (strength >= self._threshold)
            # ignore rare-but-tiny combinations, e.g. all features slightly off
            & (combined_z >= self.params["min_combined_z"])
        )
        single_flag = has_history & (
            max_abs_z >= self.params["single_feature_threshold"]
        )
        is_anomaly = forest_flag | single_flag

        forest_score = np.clip(
            SCORE_AT_THRESHOLD * strength / max(self._threshold, 1e-9),
            0.0,
            1.0,
        )
        single_score = np.clip(
            SCORE_AT_THRESHOLD * max_abs_z / self.params["single_feature_threshold"],
            0.0,
            1.0,
        )
        score = np.where(has_history, np.maximum(forest_score, single_score), 0.0)
        # keep scores of normal windows below the "medium" band
        score = np.where(
            is_anomaly, score, np.minimum(score, SCORE_AT_THRESHOLD - 0.01)
        )

        explanations = self._explanations(
            long,
            len(features),
            has_history,
            is_anomaly,
            forest_flag,
            single_flag,
        )
        return pd.DataFrame(
            {
                SERIES_KEY: features[SERIES_KEY].to_numpy(),
                WINDOW_START: features[WINDOW_START].to_numpy(),
                SCORE: score.astype("float64"),
                IS_ANOMALY: is_anomaly.astype("bool"),
                EXPLANATION: explanations,
            },
        ).astype({WINDOW_START: features[WINDOW_START].dtype})

    def _explanations(
        self,
        long: pd.DataFrame,
        n_rows: int,
        has_history: np.ndarray,
        is_anomaly: np.ndarray,
        forest_flag: np.ndarray,
        single_flag: np.ndarray,
    ) -> list[dict[str, Any]]:
        top = (
            long.dropna(subset=["z"])
            .sort_values("abs_z", ascending=False)
            .groupby("_row", sort=False)
            .head(self.params["max_contributions"])
        )
        by_row = {row: group for row, group in top.groupby("_row", sort=False)}

        explanations = []
        for position in range(n_rows):
            if not has_history[position]:
                explanations.append(make_explanation("insufficient_history"))
                continue
            if forest_flag[position]:
                reason = "unusual_combination"
            elif single_flag[position]:
                reason = "deviation_from_routine"
            else:
                reason = "within_routine"
            group = by_row.get(position)
            contributions = (
                []
                if group is None
                else [
                    {
                        "feature": item.feature,
                        "observed": round(float(item.value), 4),
                        "expected": round(float(item.expected), 4),
                        "deviation": round(float(item.z), 2),
                        "direction": "above" if item.z > 0 else "below",
                    }
                    for item in group.itertuples()
                ]
            )
            explanations.append(
                make_explanation(reason, contributions, detector=self.name),
            )
        return explanations

    # --- helpers ---------------------------------------------------------------------

    def _z_matrix(
        self,
        features: pd.DataFrame,
        long: pd.DataFrame | None = None,
    ) -> pd.DataFrame:
        """Wide frame: one row per input row, one column per trained feature."""
        if long is None:
            long = self._baseline.deviations(features)
        wide = long.pivot_table(
            index="_row",
            columns="feature",
            values="z",
            aggfunc="first",
            dropna=False,
        )
        return wide.reindex(index=range(len(features)), columns=self._features)

    def _prepare(self, z: pd.DataFrame) -> np.ndarray:
        """Missing deviation = as expected (0). Clip so one value can't dominate."""
        clip = self.params["z_clip"]
        return z.fillna(0.0).clip(-clip, clip).to_numpy(dtype="float64")

    def _strength(self, matrix: np.ndarray) -> np.ndarray:
        """Anomaly strength: higher = more anomalous (negated sklearn score)."""
        return -self._forest.score_samples(matrix)

    # --- persistence -----------------------------------------------------------------

    def save(self, directory: Path) -> None:
        directory.mkdir(parents=True, exist_ok=True)
        self._baseline.save(directory / BASELINE_DIR)
        joblib.dump(self._forest, directory / FOREST_FILE)
        (directory / META_FILE).write_text(
            json.dumps(
                {
                    "params": self.params,
                    "features": self._features,
                    "threshold": self._threshold,
                },
            ),
            encoding="utf-8",
        )

    @classmethod
    def load(cls, directory: Path) -> "IsolationForestDetector":
        # joblib uses pickle: load only artifacts this platform wrote itself.
        meta = json.loads((directory / META_FILE).read_text(encoding="utf-8"))
        detector = cls(**meta["params"])
        detector._baseline = SeasonalBaselineDetector.load(directory / BASELINE_DIR)
        detector._forest = joblib.load(directory / FOREST_FILE)
        detector._features = meta["features"]
        detector._threshold = meta["threshold"]
        return detector


def _without_confirmed(
    features: pd.DataFrame,
    labels: pd.DataFrame | None,
) -> pd.DataFrame:
    """Confirmed anomalies must not be learned as 'normal'."""
    if labels is None or labels.empty:
        return features
    confirmed = labels.loc[labels[LABEL] == 1, [SERIES_KEY, WINDOW_START]]
    keys = pd.MultiIndex.from_frame(features[[SERIES_KEY, WINDOW_START]])
    drop = keys.isin(pd.MultiIndex.from_frame(confirmed))
    return features.loc[~drop].reset_index(drop=True)
