"""Reference detector: robust seasonal baseline (median / MAD per hour-of-week).

It is intentionally simple and explainable. It sets the bar a real model must beat
and lets the platform run end-to-end before a model is chosen.
"""

import json
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

from app.detection.base import (
    EXPLANATION,
    HOUR_OF_DAY,
    HOUR_OF_WEEK,
    IS_ANOMALY,
    LABEL,
    SCORE,
    SERIES_KEY,
    WINDOW_START,
    AnomalyDetector,
    empty_scores,
    feature_columns,
    make_explanation,
)

MAD_TO_STD = 1.4826
DEFAULT_MIN_SCALE = {"default": 1.0, "avg_confidence": 0.05}
# Counts are roughly Poisson. They are scored after the Anscombe transform
# 2*sqrt(x + 3/8), which makes their noise ~N(0, 1) regardless of the level.
DEFAULT_COUNT_FEATURES = ("event_count", "unique_entities")


def anscombe(values: pd.Series) -> pd.Series:
    return 2.0 * np.sqrt(np.maximum(values, 0.0) + 0.375)


BUCKET_LEVELS = ((HOUR_OF_WEEK, "hour_of_week"), (HOUR_OF_DAY, "hour_of_day"))


class SeasonalBaselineDetector(AnomalyDetector):
    name = "seasonal_baseline"
    version = "1.0.0"

    def __init__(
        self,
        threshold: float = 4.0,
        min_samples: int = 3,
        min_scale: dict[str, float] | None = None,
        relative_min_scale: float = 0.1,
        count_features: list[str] | None = None,
        max_contributions: int = 3,
    ) -> None:
        super().__init__(
            threshold=threshold,
            min_samples=min_samples,
            min_scale={**DEFAULT_MIN_SCALE, **(min_scale or {})},
            relative_min_scale=relative_min_scale,
            count_features=list(count_features or DEFAULT_COUNT_FEATURES),
            max_contributions=max_contributions,
        )
        self._profiles: dict[str, pd.DataFrame] = {}
        self._features: list[str] = []

    # --- training --------------------------------------------------------------------

    def fit(
        self,
        features: pd.DataFrame,
        labels: pd.DataFrame | None = None,
    ) -> dict[str, Any]:
        training = self._exclude_confirmed_anomalies(features, labels)
        self._features = feature_columns(training)
        long = self._to_long(training)

        self._profiles = {}
        for bucket_column, level in BUCKET_LEVELS:
            keys = [SERIES_KEY, bucket_column, "feature"]
            grouped = long.groupby(keys, sort=False)["t"]
            median = grouped.transform("median")
            abs_dev = (long["t"] - median).abs()
            profile = (
                long.assign(abs_dev=abs_dev)
                .groupby(keys, sort=False)
                .agg(
                    median=("t", "median"),
                    mad=("abs_dev", "median"),
                    expected=("value", "median"),
                    n=("t", "size"),
                )
                .reset_index()
                .rename(columns={bucket_column: "bucket"})
            )
            self._profiles[level] = profile

        return {
            "series": int(training[SERIES_KEY].nunique()),
            "windows": int(len(training)),
            "excluded_confirmed_anomalies": int(len(features) - len(training)),
            "features": self._features,
        }

    @staticmethod
    def _exclude_confirmed_anomalies(
        features: pd.DataFrame,
        labels: pd.DataFrame | None,
    ) -> pd.DataFrame:
        if labels is None or labels.empty:
            return features
        confirmed = labels.loc[labels[LABEL] == 1, [SERIES_KEY, WINDOW_START]]
        merged = features.merge(
            confirmed.assign(_confirmed=True),
            on=[SERIES_KEY, WINDOW_START],
            how="left",
        )
        return features.loc[merged["_confirmed"].isna().to_numpy()]

    def _to_long(self, features: pd.DataFrame) -> pd.DataFrame:
        columns = [column for column in self._features if column in features.columns]
        long = features.reset_index(drop=True).rename_axis("_row").reset_index()
        long = long.melt(
            id_vars=["_row", SERIES_KEY, WINDOW_START, HOUR_OF_WEEK, HOUR_OF_DAY],
            value_vars=columns,
            var_name="feature",
            value_name="value",
        )
        long = long.dropna(subset=["value"])
        is_count = long["feature"].isin(self.params["count_features"])
        long["t"] = long["value"].where(~is_count, anscombe(long["value"]))
        long["is_count"] = is_count
        return long

    # --- scoring ---------------------------------------------------------------------

    @property
    def features(self) -> list[str]:
        return list(self._features)

    def deviations(self, features: pd.DataFrame) -> pd.DataFrame:
        """Long frame, one row per (input row, feature), with the seasonal deviation.

        Columns include ``_row`` (input position), ``feature``, ``value``,
        ``expected``, ``z`` (signed) and ``abs_z``. ``z`` is NaN when the series has
        no usable history for that hour. Other detectors can build on this.
        """
        if not self._profiles:
            raise RuntimeError("Detector is not fitted")
        return self._attach_expectations(self._to_long(features))

    def score(self, features: pd.DataFrame) -> pd.DataFrame:
        if features.empty:
            return empty_scores()

        long = self.deviations(features)
        threshold = self.params["threshold"]

        rows = []
        by_row = dict(tuple(long.groupby("_row", sort=False))) if len(long) else {}
        for position in range(len(features)):
            row_long = by_row.get(position)
            known = (
                row_long.loc[row_long["median"].notna()]
                if row_long is not None
                else None
            )
            if known is None or known.empty:
                rows.append(
                    (0.0, False, make_explanation("insufficient_history")),
                )
                continue
            known = known.sort_values("abs_z", ascending=False)
            max_z = float(known["abs_z"].iloc[0])
            is_anomaly = max_z >= threshold
            shown = known.loc[known["abs_z"] >= threshold] if is_anomaly else known
            contributions = [
                {
                    "feature": item.feature,
                    "observed": round(float(item.value), 4),
                    "expected": round(float(item.expected), 4),
                    "deviation": round(float(item.z), 2),
                    "direction": "above" if item.z > 0 else "below",
                    "baseline": item.level,
                }
                for item in shown.head(self.params["max_contributions"]).itertuples()
            ]
            rows.append(
                (
                    max_z / (max_z + threshold),
                    is_anomaly,
                    make_explanation(
                        "deviation_from_routine" if is_anomaly else "within_routine",
                        contributions,
                        threshold=threshold,
                    ),
                ),
            )

        scores, flags, explanations = zip(*rows)
        return pd.DataFrame(
            {
                SERIES_KEY: features[SERIES_KEY].to_numpy(),
                WINDOW_START: features[WINDOW_START].to_numpy(),
                SCORE: np.asarray(scores, dtype="float64"),
                IS_ANOMALY: np.asarray(flags, dtype="bool"),
                EXPLANATION: list(explanations),
            },
        ).astype({WINDOW_START: features[WINDOW_START].dtype})

    def _attach_expectations(self, long: pd.DataFrame) -> pd.DataFrame:
        """Use the hour-of-week profile, falling back to hour-of-day."""
        long = long.assign(median=np.nan, mad=np.nan, expected=np.nan, level=None)
        for bucket_column, level in BUCKET_LEVELS:
            missing = long["median"].isna()
            if not missing.any():
                break
            profile = self._profiles[level]
            profile = profile.loc[profile["n"] >= self.params["min_samples"]]
            candidates = long.loc[missing, [SERIES_KEY, bucket_column, "feature"]]
            matched = candidates.merge(
                profile,
                left_on=[SERIES_KEY, bucket_column, "feature"],
                right_on=[SERIES_KEY, "bucket", "feature"],
                how="left",
            )
            matched.index = candidates.index
            found = matched["median"].notna()
            index = matched.index[found]
            long.loc[index, "median"] = matched.loc[found, "median"]
            long.loc[index, "mad"] = matched.loc[found, "mad"]
            long.loc[index, "expected"] = matched.loc[found, "expected"]
            long.loc[index, "level"] = level

        min_scale = long["feature"].map(
            lambda feature: self.params["min_scale"].get(
                feature,
                self.params["min_scale"]["default"],
            ),
        )
        median = long["median"].to_numpy(dtype="float64")
        is_count = long["is_count"].to_numpy(dtype=bool)
        robust = MAD_TO_STD * long["mad"].to_numpy(dtype="float64")
        floor = np.where(
            is_count,
            1.0,  # unit variance after the Anscombe transform
            np.maximum(
                min_scale.to_numpy(dtype="float64"),
                self.params["relative_min_scale"] * np.abs(median),
            ),
        )
        scale = np.maximum(robust, floor)
        z = (long["t"].to_numpy(dtype="float64") - median) / scale
        return long.assign(z=z, abs_z=np.abs(z))

    # --- persistence -----------------------------------------------------------------

    def save(self, directory: Path) -> None:
        directory.mkdir(parents=True, exist_ok=True)
        (directory / "params.json").write_text(
            json.dumps({"params": self.params, "features": self._features}),
            encoding="utf-8",
        )
        for level, profile in self._profiles.items():
            profile.to_json(
                directory / f"profile_{level}.json",
                orient="split",
                double_precision=15,
            )

    @classmethod
    def load(cls, directory: Path) -> "SeasonalBaselineDetector":
        meta = json.loads((directory / "params.json").read_text(encoding="utf-8"))
        detector = cls(**meta["params"])
        detector._features = meta["features"]
        detector._profiles = {
            level: pd.read_json(
                directory / f"profile_{level}.json",
                orient="split",
                dtype={SERIES_KEY: str, "feature": str},
            )
            for _, level in BUCKET_LEVELS
        }
        return detector
