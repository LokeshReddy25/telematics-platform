"""
test_risk_scorer.py
---------------------
Unit tests for ml/risk_scorer.py. Rather than asserting exact risk_score
values (which are a function of the trained model and would make tests
brittle across retraining), these tests verify:
  1. The feature aggregation math (_aggregate_features) is correct against
     hand-crafted events where the expected numbers are known exactly.
  2. The risk category boundaries (_risk_category) match the documented
     Low/Medium/High/Critical bands.
  3. score_trip()'s output contract: the right keys, valid ranges, and
     correct error handling on bad input.

Requires ml/models/risk_model.pkl to already exist (CI trains it fresh via
`python ml/train_model.py` before running pytest; locally, run the same
command first if you haven't already).
"""

import pytest

from risk_scorer import _aggregate_features, _risk_category, score_trip


def _make_event(driver_id, trip_id, timestamp, speed, braking, accel, is_night):
    return {
        "driver_id": driver_id,
        "trip_id": trip_id,
        "timestamp": timestamp,
        "speed_kmh": speed,
        "braking_force_g": braking,
        "acceleration_g": accel,
        "latitude": 50.11,
        "longitude": 8.68,
        "is_night": is_night,
        "driver_profile": "moderate",
    }


def _sample_trip():
    # Four events, two seconds apart, spanning 6 seconds total. Two are
    # "harsh" braking/accel events (>= the thresholds risk_scorer.py uses),
    # two are not -- picked so every aggregate has an unambiguous expected
    # value we can assert against exactly.
    return [
        _make_event("driver_x", "trip_x", "2026-07-23T10:00:00", speed=60.0, braking=0.1, accel=0.1, is_night=False),
        _make_event("driver_x", "trip_x", "2026-07-23T10:00:02", speed=80.0, braking=0.5, accel=0.1, is_night=True),
        _make_event("driver_x", "trip_x", "2026-07-23T10:00:04", speed=100.0, braking=0.1, accel=0.4, is_night=True),
        _make_event("driver_x", "trip_x", "2026-07-23T10:00:06", speed=40.0, braking=0.1, accel=0.1, is_night=False),
    ]


def test_aggregate_features_computes_expected_values():
    features = _aggregate_features(_sample_trip())

    assert features["avg_speed"] == pytest.approx((60 + 80 + 100 + 40) / 4)
    assert features["max_speed"] == 100.0
    assert features["harsh_brake_count"] == 1  # only the 0.5 g event is >= 0.4 g threshold
    assert features["harsh_accel_count"] == 1  # only the 0.4 g event is >= 0.3 g threshold
    assert features["night_driving_ratio"] == pytest.approx(2 / 4)
    assert features["trip_duration_seconds"] == pytest.approx(6.0)


@pytest.mark.parametrize(
    "score,expected_category",
    [
        (0, "Low"),
        (24.9, "Low"),
        (25, "Medium"),
        (49.9, "Medium"),
        (50, "High"),
        (74.9, "High"),
        (75, "Critical"),
        (100, "Critical"),
    ],
)
def test_risk_category_boundaries(score, expected_category):
    assert _risk_category(score) == expected_category


def test_score_trip_returns_expected_contract():
    result = score_trip(_sample_trip())

    assert result["driver_id"] == "driver_x"
    assert result["trip_id"] == "trip_x"
    assert 0.0 <= result["risk_score"] <= 100.0
    assert result["risk_category"] in {"Low", "Medium", "High", "Critical"}
    assert result["harsh_brake_count"] == 1
    assert result["harsh_accel_count"] == 1
    assert "scored_at" in result


def test_score_trip_rejects_empty_input():
    with pytest.raises(ValueError):
        score_trip([])
