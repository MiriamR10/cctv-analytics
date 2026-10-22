from datetime import UTC, datetime, timedelta

from app.detection.base import HOUR_OF_WEEK, SERIES_KEY
from app.domain import EventRecord
from app.features import build_features, split_series_key

START = datetime(2025, 1, 6, 0, 0, tzinfo=UTC)  # Monday 02:00 Israel


def make(id, minutes, **kwargs):
    return EventRecord(
        id=id,
        source_id=kwargs.pop("source_id", "cam-1"),
        zone_id=kwargs.pop("zone_id", None),
        timestamp=START + timedelta(minutes=minutes),
        entity_type="vehicle",
        **kwargs,
    )


def test_dense_grid_includes_empty_windows():
    events = [make(1, 1, entity_id="A"), make(2, 2, entity_id="A"), make(3, 50)]
    frame = build_features(events, START, START + timedelta(hours=1), 15)
    assert frame["event_count"].tolist() == [2, 0, 0, 1]
    assert frame["unique_entities"].tolist() == [1, 0, 0, 0]


def test_series_starts_at_first_event_unless_known():
    events = [make(1, 40)]
    frame = build_features(events, START, START + timedelta(hours=1), 15)
    assert len(frame) == 2
    known = build_features(
        [],
        START,
        START + timedelta(hours=1),
        15,
        known_series=["cam-9|-|vehicle"],
    )
    assert known["event_count"].tolist() == [0, 0, 0, 0]


def test_local_time_columns():
    frame = build_features([make(1, 0)], START, START + timedelta(minutes=15), 15)
    assert frame[HOUR_OF_WEEK].tolist() == [2]  # Monday 02:00 local


def test_series_key_round_trip():
    frame = build_features(
        [make(1, 0, zone_id="gate")], START, START + timedelta(minutes=15), 15
    )
    assert split_series_key(frame[SERIES_KEY].iloc[0]) == ("cam-1", "gate", "vehicle")
