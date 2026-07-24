"""
risk_scorer.py
---------------
Loads the trained GradientBoostingRegressor (ml/models/risk_model.pkl) once
at import time, and exposes `score_trip()` -- the single function
spark/stream_processor.py calls for each 60-second driver/trip window of
raw events.

This module is intentionally Spark-free: it operates on plain Python
lists/dicts so it can be unit tested in isolation (see validation check #4)
and so Spark can call it from a UDF/foreachBatch without pulling PySpark
types into the ML code.
"""

import os
import pickle
from datetime import datetime, timezone
from statistics import pvariance
from typing import Any, Dict, List

import pandas as pd

# Thresholds must match the ones used implicitly by the simulator/training
# data generation so "harsh" means the same thing everywhere in the pipeline.
HARSH_BRAKE_THRESHOLD_G = 0.4
HARSH_ACCEL_THRESHOLD_G = 0.3

_MODEL_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "models", "risk_model.pkl")

_model = None
_feature_columns: List[str] = []


def _load_model() -> None:
    """
    Load the pickled model bundle into module-level globals. Called once at
    import time (see bottom of file) so repeated `score_trip()` calls in a
    long-running Spark executor don't pay deserialization cost per call.
    """
    global _model, _feature_columns

    if not os.path.exists(_MODEL_PATH):
        raise FileNotFoundError(f"Risk model not found at {_MODEL_PATH}. Run `python ml/train_model.py` first.")

    with open(_MODEL_PATH, "rb") as f:
        bundle = pickle.load(f)

    _model = bundle["model"]
    _feature_columns = bundle["feature_columns"]


def _risk_category(score: float) -> str:
    """Map a 0-100 numeric score to the four insurer-facing risk bands."""
    if score < 25:
        return "Low"
    if score < 50:
        return "Medium"
    if score < 75:
        return "High"
    return "Critical"


def _aggregate_features(trip_events: List[Dict[str, Any]]) -> Dict[str, Any]:
    """
    Reduce a list of raw trip events (as produced by simulator/trip_generator.py
    and carried through Kafka/Spark) into the exact aggregate feature set the
    model was trained on. Column names/order here must match
    ml/train_model.py's FEATURE_COLUMNS -- we import that list directly
    rather than hardcoding it a second time.
    """
    speeds = [e["speed_kmh"] for e in trip_events]
    braking_forces = [e["braking_force_g"] for e in trip_events]
    accel_forces = [e["acceleration_g"] for e in trip_events]
    night_flags = [bool(e["is_night"]) for e in trip_events]

    timestamps = sorted(e["timestamp"] for e in trip_events)
    start = datetime.fromisoformat(timestamps[0])
    end = datetime.fromisoformat(timestamps[-1])
    duration_seconds = max(0.0, (end - start).total_seconds())

    harsh_brake_count = sum(1 for b in braking_forces if b >= HARSH_BRAKE_THRESHOLD_G)
    harsh_accel_count = sum(1 for a in accel_forces if a >= HARSH_ACCEL_THRESHOLD_G)

    avg_speed = sum(speeds) / len(speeds)
    max_speed = max(speeds)
    # Population variance (not sample variance) to match a stable, always-
    # defined value even for very short trips of a single event.
    speed_variance = pvariance(speeds) if len(speeds) > 1 else 0.0
    night_driving_ratio = sum(night_flags) / len(night_flags)

    return {
        "avg_speed": avg_speed,
        "max_speed": max_speed,
        "harsh_brake_count": harsh_brake_count,
        "harsh_accel_count": harsh_accel_count,
        "night_driving_ratio": night_driving_ratio,
        "trip_duration_seconds": duration_seconds,
        "speed_variance": speed_variance,
    }


def score_trip(trip_events: List[Dict[str, Any]]) -> Dict[str, Any]:
    """
    Score a batch of raw events belonging to ONE driver/trip window.

    Args:
        trip_events: list of event dicts as produced by trip_generator.py
            (must all share the same driver_id/trip_id; this function does
            not itself group events -- that's the caller's/Spark's job).

    Returns:
        dict with driver_id, trip_id, risk_score (0-100), risk_category,
        and the underlying aggregate features, ready to hand to
        storage/influxdb_writer.py.
    """
    if not trip_events:
        raise ValueError("score_trip() requires at least one trip event")

    if _model is None:
        _load_model()

    features = _aggregate_features(trip_events)

    # Build a single-row DataFrame with columns in the exact order the model
    # was trained on -- GradientBoostingRegressor is order-sensitive.
    feature_row = pd.DataFrame([[features[col] for col in _feature_columns]], columns=_feature_columns)

    raw_score = float(_model.predict(feature_row)[0])
    risk_score = round(max(0.0, min(100.0, raw_score)), 2)

    return {
        "driver_id": trip_events[0]["driver_id"],
        "trip_id": trip_events[0]["trip_id"],
        "risk_score": risk_score,
        "risk_category": _risk_category(risk_score),
        "harsh_brake_count": features["harsh_brake_count"],
        "harsh_accel_count": features["harsh_accel_count"],
        "max_speed": round(features["max_speed"], 2),
        "avg_speed": round(features["avg_speed"], 2),
        "night_driving_ratio": round(features["night_driving_ratio"], 4),
        "scored_at": datetime.now(timezone.utc).isoformat(),
    }


# Load the model as soon as this module is imported, per the spec ("Load
# risk_model.pkl at module import"). If the model file is missing, callers
# get a clear, immediate error instead of a confusing failure mid-stream.
_load_model()


if __name__ == "__main__":
    # Small smoke test using synthetic events, useful for manual debugging.
    sample_events = [
        {
            "driver_id": "driver_0000",
            "trip_id": "trip-smoke-test",
            "timestamp": "2026-07-23T10:00:00",
            "speed_kmh": 80.0,
            "braking_force_g": 0.5,
            "acceleration_g": 0.4,
            "latitude": 50.11,
            "longitude": 8.68,
            "is_night": False,
            "driver_profile": "moderate",
        },
        {
            "driver_id": "driver_0000",
            "trip_id": "trip-smoke-test",
            "timestamp": "2026-07-23T10:01:00",
            "speed_kmh": 95.0,
            "braking_force_g": 0.2,
            "acceleration_g": 0.1,
            "latitude": 50.111,
            "longitude": 8.681,
            "is_night": False,
            "driver_profile": "moderate",
        },
    ]
    result = score_trip(sample_events)
    print(result)
