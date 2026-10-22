from datetime import UTC, datetime, time, timedelta

import pytest

from app.domain import EventRecord
from app.rules.engine import RuleValidationError, TimeRange, evaluate_rule

NOW = datetime(2025, 3, 3, 12, 0, tzinfo=UTC)  # Monday 14:00 Israel time


def event(id, minutes_ago=1, **kwargs):
    defaults = dict(
        source_id="cam-1",
        zone_id="gate",
        entity_type="vehicle",
        entity_id=None,
    )
    return EventRecord(
        id=id,
        timestamp=NOW - timedelta(minutes=minutes_ago),
        **{**defaults, **kwargs},
    )


def test_watchlist_matches_case_and_space_insensitive():
    events = [event(1, entity_id=" 12-345-67 "), event(2, entity_id="99-999-99")]
    drafts = evaluate_rule(1, "watchlist", {"entity_ids": ["12-345-67"]}, events, NOW)
    assert [d.event_ids for d in drafts] == [[1]]
    assert drafts[0].dedup_key == "rule:1:event:1"


def test_watchlist_matches_plates_normalised_by_forensic():
    events = [event(1, entity_id="1234567")]
    drafts = evaluate_rule(1, "watchlist", {"entity_ids": ["12-345-67"]}, events, NOW)
    assert [d.event_ids for d in drafts] == [[1]]


def test_entity_frequency_groups_plate_spellings():
    events = [
        event(1, entity_id="12-345-67", minutes_ago=5),
        event(2, entity_id="1234567", minutes_ago=3),
    ]
    params = {"period_minutes": 30, "min_occurrences": 2}
    drafts = evaluate_rule(1, "entity_frequency", params, events, NOW)
    assert len(drafts) == 1


def test_scope_filters_events():
    events = [
        event(1, entity_id="A", zone_id="gate"),
        event(2, entity_id="A", zone_id="lot"),
    ]
    params = {"entity_ids": ["A"], "scope": {"zone_ids": ["lot"]}}
    drafts = evaluate_rule(1, "watchlist", params, events, NOW)
    assert [d.event_ids for d in drafts] == [[2]]


@pytest.mark.parametrize(
    ("local_hour", "inside"),
    [(23, True), (3, True), (6, False), (12, False)],
)
def test_time_range_wraps_midnight(local_hour, inside):
    tz_offset = timedelta(hours=2)  # Israel standard time in March 3
    moment = datetime(2025, 3, 3, local_hour, 0, tzinfo=UTC) - tz_offset
    assert TimeRange(start=time(22), end=time(6)).contains(moment) is inside


def test_night_range_day_belongs_to_start_day():
    # Sunday night 02:00 local belongs to Saturday's range (day 5)
    moment = datetime(2025, 3, 2, 0, 0, tzinfo=UTC)  # Sun 02:00 local
    sat_night = TimeRange(start=time(22), end=time(6), days_of_week=[5])
    sun_night = TimeRange(start=time(22), end=time(6), days_of_week=[6])
    assert sat_night.contains(moment)
    assert not sun_night.contains(moment)


def test_max_dwell():
    events = [event(1, dwell_seconds=30), event(2, dwell_seconds=900)]
    drafts = evaluate_rule(1, "max_dwell", {"max_seconds": 600}, events, NOW)
    assert [d.event_ids for d in drafts] == [[2]]


def test_count_threshold_gt_is_stable_across_runs():
    events = [event(i, minutes_ago=2) for i in range(1, 7)]
    params = {"window_minutes": 10, "threshold": 5}
    first = evaluate_rule(1, "count_threshold", params, events, NOW)
    second = evaluate_rule(
        1, "count_threshold", params, events, NOW + timedelta(minutes=3)
    )
    assert len(first) == 1
    assert first[0].dedup_key == second[0].dedup_key


def test_count_threshold_lt_detects_empty_window():
    params = {
        "window_minutes": 10,
        "operator": "lt",
        "threshold": 1,
        "scope": {"zone_ids": ["gate"]},
    }
    drafts = evaluate_rule(1, "count_threshold", params, [], NOW)
    assert len(drafts) == 1
    assert drafts[0].explanation["observed"] == 0


def test_count_threshold_lt_requires_scope():
    with pytest.raises(RuleValidationError):
        evaluate_rule(
            1,
            "count_threshold",
            {"window_minutes": 5, "operator": "lt", "threshold": 1},
            [],
            NOW,
        )


def test_entity_frequency():
    events = [event(i, minutes_ago=i * 5, entity_id="12-345-67") for i in range(1, 5)]
    params = {"period_minutes": 60, "min_occurrences": 3}
    drafts = evaluate_rule(1, "entity_frequency", params, events, NOW)
    assert len(drafts) == 1
    assert drafts[0].explanation["observed"] == 4


def test_unknown_rule_type():
    with pytest.raises(RuleValidationError):
        evaluate_rule(1, "nope", {}, [], NOW)
