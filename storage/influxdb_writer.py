"""
influxdb_writer.py
-------------------
Thin persistence layer between the ML risk scorer and InfluxDB 2.x. This is
the only file in the pipeline that talks to InfluxDB, so both
spark/stream_processor.py (streaming path) and any future batch/backfill
tooling can share it without duplicating connection logic.

InfluxDB 2.x organizes data as: organization -> bucket -> measurement ->
tags/fields. We use:
  - bucket:      "telematics"
  - measurement: "driver_risk"
  - tags:        driver_id, trip_id, risk_category  (indexed, used for
                  Grafana template variables / filtering)
  - fields:      risk_score, harsh_brake_count, harsh_accel_count,
                  max_speed, avg_speed, night_driving_ratio (the actual
                  numeric values Grafana panels plot)
"""

import logging
import os
from datetime import datetime, timezone
from typing import Any, Dict

from influxdb_client import InfluxDBClient, Point, WritePrecision
from influxdb_client.client.write_api import SYNCHRONOUS

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("influxdb_writer")

# All connection details are configurable via environment variables so the
# same code works unchanged across local Docker Compose, Kubernetes
# ConfigMaps, and (eventually) AWS -- only the env values differ per
# environment, never the code.
INFLUXDB_URL = os.environ.get("INFLUXDB_URL", "http://localhost:8086")
INFLUXDB_TOKEN = os.environ.get("INFLUXDB_TOKEN", "telematics-dev-token")
INFLUXDB_ORG = os.environ.get("INFLUXDB_ORG", "telematics-org")
INFLUXDB_BUCKET = os.environ.get("INFLUXDB_BUCKET", "telematics")

MEASUREMENT = "driver_risk"

_client: InfluxDBClient = None
_write_api = None


def _get_client() -> InfluxDBClient:
    """
    Lazily create (and cache) the InfluxDB client + write API. Lazy
    initialization means importing this module never requires InfluxDB to
    already be running -- only the first actual write does -- which keeps
    unit testing and module-level imports (e.g. from Spark's driver process)
    fast and side-effect free.
    """
    global _client, _write_api
    if _client is None:
        _client = InfluxDBClient(url=INFLUXDB_URL, token=INFLUXDB_TOKEN, org=INFLUXDB_ORG)
        _write_api = _client.write_api(write_options=SYNCHRONOUS)
        logger.info(
            "Initialized InfluxDB client for %s (org=%s, bucket=%s)", INFLUXDB_URL, INFLUXDB_ORG, INFLUXDB_BUCKET
        )
    return _client


def write_scored_trip(scored: Dict[str, Any]) -> None:
    """
    Persist one scored-trip result (the dict returned by
    ml.risk_scorer.score_trip) as a single InfluxDB point.

    Args:
        scored: dict containing at minimum driver_id, trip_id, risk_score,
            risk_category, harsh_brake_count, harsh_accel_count, max_speed,
            avg_speed, night_driving_ratio, and optionally scored_at.
    """
    _get_client()  # ensures _write_api is initialized

    scored_at = scored.get("scored_at")
    if scored_at:
        timestamp = datetime.fromisoformat(scored_at)
    else:
        timestamp = datetime.now(timezone.utc)

    point = (
        Point(MEASUREMENT)
        .tag("driver_id", scored["driver_id"])
        .tag("trip_id", scored["trip_id"])
        .tag("risk_category", scored["risk_category"])
        .field("risk_score", float(scored["risk_score"]))
        .field("harsh_brake_count", int(scored["harsh_brake_count"]))
        .field("harsh_accel_count", int(scored["harsh_accel_count"]))
        .field("max_speed", float(scored["max_speed"]))
        .field("avg_speed", float(scored["avg_speed"]))
        .field("night_driving_ratio", float(scored["night_driving_ratio"]))
        .time(timestamp, WritePrecision.NS)
    )

    try:
        _write_api.write(bucket=INFLUXDB_BUCKET, org=INFLUXDB_ORG, record=point)
        logger.info(
            "Wrote scored trip to InfluxDB: driver=%s trip=%s risk=%.2f (%s)",
            scored["driver_id"],
            scored["trip_id"],
            scored["risk_score"],
            scored["risk_category"],
        )
    except Exception:
        logger.exception(
            "Failed to write scored trip to InfluxDB for driver=%s trip=%s",
            scored["driver_id"],
            scored["trip_id"],
        )
        raise


def close() -> None:
    """Flush and close the InfluxDB client. Call on graceful shutdown of the
    streaming job so buffered writes aren't silently dropped."""
    global _client, _write_api
    if _client is not None:
        _client.close()
        _client = None
        _write_api = None
        logger.info("InfluxDB client closed.")
