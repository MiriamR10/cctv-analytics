"""Acceptance tests for ANY detector. A model is accepted only if all of these pass.

Runs against ``DETECTOR_CLASS`` / ``DETECTOR_PARAMS`` from the environment:

    DETECTOR_CLASS=my_models.forest:ForestDetector pytest tests/contract
"""

import time
from datetime import UTC, datetime, timedelta

import numpy as np
import pandas as pd
import pytest

from app.config import settings
from app.detection.base import (
    EXPLANATION,
    IS_ANOMALY,
    LABEL,
    SCORE,
    SERIES_KEY,
    WINDOW_START,
)
from app.detection.registry import resolve_detector_class
from app.features import build_features
from app.services.modeling import validate_output
from app.synthetic import generate_events

WINDOW = 15
TRAIN_DAYS = 21
START = datetime(2025, 1, 5, tzinfo=UTC)  # Sunday
TRAIN_END = START + timedelta(days=TRAIN_DAYS)
SPIKE_AT = TRAIN_END + timedelta(hours=9)  # Sunday 11:00 local, a busy hour
SILENT_SOURCE = "cam-2"


def make_detector():
    return resolve_detector_class(settings.detector_class)(**settings.detector_params)


def score(detector, features, history):
    """Score like the platform does: with recent history if the detector needs it."""
    context = None
    if detector.context_windows > 0:
        context = history.loc[
            history[WINDOW_START]
            >= history[WINDOW_START].max()
            - pd.Timedelta(minutes=WINDOW * (detector.context_windows - 1))
        ]
    return detector.score_with_context(features, context)


@pytest.fixture(scope="module")
def frames():
    history = generate_events(START, TRAIN_DAYS)
    train = build_features(history, START, TRAIN_END, WINDOW)

    test_start = TRAIN_END
    test_end = TRAIN_END + timedelta(days=1)
    live = generate_events(
        test_start,
        1,
        seed=99,
        spikes=[("cam-1", SPIKE_AT, 400)],
    )
    # cam-2 goes silent for 3 busy hours: an anomaly of absence.
    silent_from = TRAIN_END + timedelta(hours=7)
    silent_to = silent_from + timedelta(hours=3)
    live = [
        event
        for event in live
        if not (
            event.source_id == SILENT_SOURCE
            and silent_from <= event.timestamp < silent_to
        )
    ]
    known = train[SERIES_KEY].unique().tolist()
    test = build_features(live, test_start, test_end, WINDOW, known_series=known)
    return train, test, (silent_from, silent_to)


@pytest.fixture(scope="module")
def fitted(frames):
    train, _, _ = frames
    detector = make_detector()
    metrics = detector.fit(train)
    return detector, metrics, train


def _rows(test, scores, series_prefix, start, end):
    mask = (
        test[SERIES_KEY].str.startswith(series_prefix)
        & (test[WINDOW_START] >= start)
        & (test[WINDOW_START] < end)
    )
    return scores.loc[mask.to_numpy()]


def test_fit_returns_json_metrics(fitted):
    import json

    _, metrics, _ = fitted
    json.dumps(metrics)


def test_output_contract(fitted, frames):
    detector, _, history = fitted
    _, test, _ = frames
    scores = score(detector, test, history)
    validate_output(test, scores)
    assert scores[IS_ANOMALY].dtype == bool


def test_empty_input(fitted, frames):
    detector, _, history = fitted
    _, test, _ = frames
    scores = score(detector, test.iloc[0:0], history)
    assert scores.empty
    assert set(scores.columns) >= {SERIES_KEY, WINDOW_START, SCORE, IS_ANOMALY}


def test_detects_spike(fitted, frames):
    detector, _, history = fitted
    _, test, _ = frames
    scores = score(detector, test, history)
    spike = _rows(test, scores, "cam-1|", SPIKE_AT, SPIKE_AT + timedelta(minutes=15))
    assert spike[IS_ANOMALY].any(), "a 400-event spike must be flagged"


def test_detects_silence(fitted, frames):
    detector, _, history = fitted
    _, test, (silent_from, silent_to) = frames
    scores = score(detector, test, history)
    silent = _rows(test, scores, f"{SILENT_SOURCE}|", silent_from, silent_to)
    assert silent[IS_ANOMALY].mean() >= 0.5, "3 silent busy hours must be flagged"


def test_low_false_positive_rate_on_routine(fitted, frames):
    detector, _, history = fitted
    train, test, (silent_from, silent_to) = frames
    scores = score(detector, test, history)
    routine = (test[SERIES_KEY].str.startswith("cam-3|")).to_numpy()
    rate = scores.loc[routine, IS_ANOMALY].mean()
    assert rate <= 0.02, f"false positive rate {rate:.1%} on routine traffic"


def test_unknown_series_is_not_anomalous(fitted, frames):
    detector, _, history = fitted
    _, test, _ = frames
    unknown = test.head(8).copy()
    unknown[SERIES_KEY] = "new-cam|new-zone|vehicle"
    unknown["event_count"] = 500
    scores = score(detector, unknown, history)
    validate_output(unknown, scores)
    assert not scores[IS_ANOMALY].any()


def test_handles_missing_and_extra_features(fitted, frames):
    detector, _, history = fitted
    _, test, _ = frames
    changed = test.head(50).copy()
    changed["avg_dwell_seconds"] = np.nan
    changed["new_feature"] = 1.0
    validate_output(changed, score(detector, changed, history))


def test_save_load_round_trip(fitted, frames, tmp_path):
    detector, _, history = fitted
    _, test, _ = frames
    detector.save(tmp_path / "model")
    restored = type(detector).load(tmp_path / "model")
    before = score(detector, test, history)
    after = score(restored, test, history)
    np.testing.assert_allclose(before[SCORE], after[SCORE], rtol=1e-6)
    assert (before[IS_ANOMALY] == after[IS_ANOMALY]).all()


def test_accepts_labels(frames):
    train, _, _ = frames
    labels = train.head(5)[[SERIES_KEY, WINDOW_START]].assign(
        **{LABEL: [1, 0, 1, 0, 1]}
    )
    detector = make_detector()
    detector.fit(train, labels)


def test_explanations_name_features(fitted, frames):
    detector, _, history = fitted
    _, test, _ = frames
    scores = score(detector, test, history)
    flagged = scores.loc[scores[IS_ANOMALY], EXPLANATION]
    assert len(flagged) > 0
    for explanation in flagged:
        assert explanation.get("reason")
        assert explanation.get("contributions"), "anomalies must say why"


def test_scoring_speed(fitted, frames):
    detector, _, history = fitted
    _, test, _ = frames
    big = pd.concat(
        [test.assign(**{SERIES_KEY: test[SERIES_KEY] + f"#{i}"}) for i in range(30)],
        ignore_index=True,
    )
    started = time.perf_counter()
    score(detector, big, history)
    elapsed = time.perf_counter() - started
    assert elapsed < 30, f"scoring {len(big)} rows took {elapsed:.1f}s"
