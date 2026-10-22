"""Turn raw events into the generic features frame consumed by any detector."""

from collections.abc import Iterable
from datetime import datetime

import pandas as pd

from app.detection.base import (
    DAY_OF_WEEK,
    HOUR_OF_DAY,
    HOUR_OF_WEEK,
    SERIES_KEY,
    WINDOW_START,
)
from app.domain import EventRecord
from app.timeutils import as_utc, local_tz

SERIES_SEPARATOR = "|"
NO_ZONE = "-"
FEATURE_NAMES = (
    "event_count",
    "unique_entities",
    "avg_dwell_seconds",
    "avg_confidence",
)


def series_key(source_id: str, zone_id: str | None, entity_type: str) -> str:
    return SERIES_SEPARATOR.join((source_id, zone_id or NO_ZONE, entity_type))


def split_series_key(key: str) -> tuple[str, str | None, str]:
    source_id, zone_id, entity_type = key.split(SERIES_SEPARATOR, 2)
    return source_id, None if zone_id == NO_ZONE else zone_id, entity_type


def build_features(
    events: Iterable[EventRecord],
    start: datetime,
    end: datetime,
    window_minutes: int,
    known_series: Iterable[str] = (),
) -> pd.DataFrame:
    """Dense frame: one row per (series, window) in [start, end).

    Windows with no events are included (count 0) so that silence can be detected.
    A series appears from its first event onward, or from ``start`` if it is known.
    """
    start, end = as_utc(start), as_utc(end)
    freq = f"{window_minutes}min"
    windows = pd.date_range(start, end, freq=freq, inclusive="left", tz="UTC")

    frame = pd.DataFrame(
        [
            (
                series_key(event.source_id, event.zone_id, event.entity_type),
                as_utc(event.timestamp),
                event.entity_id,
                event.dwell_seconds,
                event.confidence,
            )
            for event in events
        ],
        columns=[SERIES_KEY, "timestamp", "entity_id", "dwell", "confidence"],
    )
    frame["timestamp"] = pd.to_datetime(frame["timestamp"], utc=True)
    frame = frame.loc[(frame["timestamp"] >= start) & (frame["timestamp"] < end)]
    frame[WINDOW_START] = frame["timestamp"].dt.floor(freq)
    for column in ("dwell", "confidence"):
        frame[column] = pd.to_numeric(frame[column], errors="coerce")

    aggregated = frame.groupby([SERIES_KEY, WINDOW_START]).agg(
        event_count=("timestamp", "size"),
        unique_entities=("entity_id", "nunique"),
        avg_dwell_seconds=("dwell", "mean"),
        avg_confidence=("confidence", "mean"),
    )

    first_seen = frame.groupby(SERIES_KEY)[WINDOW_START].min()
    for key in known_series:
        first_seen[key] = windows[0] if len(windows) else start
    if first_seen.empty or windows.empty:
        return _empty_features()

    grid = pd.MultiIndex.from_tuples(
        [
            (key, window)
            for key, since in first_seen.items()
            for window in windows
            if window >= since
        ],
        names=[SERIES_KEY, WINDOW_START],
    )
    dense = aggregated.reindex(grid)
    dense["event_count"] = dense["event_count"].fillna(0).astype("int64")
    dense["unique_entities"] = dense["unique_entities"].fillna(0).astype("int64")
    dense = dense.reset_index().sort_values([SERIES_KEY, WINDOW_START])
    return add_time_columns(dense).reset_index(drop=True)


def add_time_columns(frame: pd.DataFrame) -> pd.DataFrame:
    local = frame[WINDOW_START].dt.tz_convert(local_tz())
    frame[DAY_OF_WEEK] = local.dt.dayofweek.astype("int64")
    frame[HOUR_OF_DAY] = local.dt.hour.astype("int64")
    frame[HOUR_OF_WEEK] = frame[DAY_OF_WEEK] * 24 + frame[HOUR_OF_DAY]
    return frame


def _empty_features() -> pd.DataFrame:
    frame = pd.DataFrame(
        {
            SERIES_KEY: pd.Series(dtype="object"),
            WINDOW_START: pd.Series(dtype="datetime64[ns, UTC]"),
            "event_count": pd.Series(dtype="int64"),
            "unique_entities": pd.Series(dtype="int64"),
            "avg_dwell_seconds": pd.Series(dtype="float64"),
            "avg_confidence": pd.Series(dtype="float64"),
        },
    )
    return add_time_columns(frame)
