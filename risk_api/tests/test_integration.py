"""
Integration test: real InfluxDB, real RiskRepository, real Flux queries.

Creates a throwaway bucket, writes points in exactly the shape
storage/influxdb_writer.py writes them, calls every endpoint over HTTP
(TestClient), then deletes the bucket.

Needs:
    RISK_API_IT_INFLUXDB_URL    e.g. http://localhost:8086
    RISK_API_IT_INFLUXDB_TOKEN  a token allowed to create/delete buckets
    RISK_API_IT_INFLUXDB_ORG    (optional, default telematics-org)
Skipped when the token is not set.
"""

import os
import uuid
from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient
from influxdb_client import InfluxDBClient, Point, WritePrecision
from influxdb_client.client.write_api import SYNCHRONOUS

from app.config import Settings
from app.main import create_app

URL = os.environ.get("RISK_API_IT_INFLUXDB_URL", "http://localhost:8086")
TOKEN = os.environ.get("RISK_API_IT_INFLUXDB_TOKEN")
ORG = os.environ.get("RISK_API_IT_INFLUXDB_ORG", "telematics-org")

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(not TOKEN, reason="RISK_API_IT_INFLUXDB_TOKEN not set"),
]


def _point(driver, trip, score, category, ts):
    return (
        Point("driver_risk")
        .tag("driver_id", driver)
        .tag("trip_id", trip)
        .tag("risk_category", category)
        .field("risk_score", float(score))
        .field("harsh_brake_count", 1)
        .field("harsh_accel_count", 2)
        .field("max_speed", 99.0)
        .field("avg_speed", 70.0)
        .field("night_driving_ratio", 0.0)
        .time(ts, WritePrecision.NS)
    )


@pytest.fixture(scope="module")
def seeded():
    bucket_name = f"risk-api-it-{uuid.uuid4().hex[:10]}"
    admin = InfluxDBClient(url=URL, token=TOKEN, org=ORG)
    bucket = admin.buckets_api().create_bucket(bucket_name=bucket_name, org=ORG)
    now = datetime.now(timezone.utc).replace(microsecond=0)
    trip_a, trip_b, trip_c = (str(uuid.uuid4()) for _ in range(3))
    points = [
        _point("driver_a", trip_a, 30.0, "Medium", now - timedelta(minutes=3)),
        _point("driver_a", trip_a, 55.5, "High", now - timedelta(minutes=2)),  # re-score
        _point("driver_a", trip_b, 10.0, "Low", now - timedelta(minutes=1)),
        _point("driver_b", trip_c, 88.25, "Critical", now - timedelta(minutes=1)),
    ]
    admin.write_api(write_options=SYNCHRONOUS).write(bucket=bucket_name, org=ORG, record=points)
    settings = Settings(influxdb_url=URL, influxdb_token=TOKEN, influxdb_org=ORG, influxdb_bucket=bucket_name)
    try:
        yield {"settings": settings, "trip_a": trip_a, "trip_b": trip_b, "trip_c": trip_c}
    finally:
        admin.buckets_api().delete_bucket(bucket)
        admin.close()


@pytest.fixture(scope="module")
def client(seeded):
    with TestClient(create_app(settings=seeded["settings"])) as c:
        yield c


def test_health_against_real_influx(client):
    assert client.get("/health").json() == {"status": "ok", "influxdb": "reachable"}


def test_driver_risk_against_real_influx(client, seeded):
    body = client.get("/drivers/driver_a/risk").json()
    assert body["records"] == 3
    assert body["trips_scored"] == 2
    assert body["latest"]["trip_id"] == seeded["trip_b"]
    assert body["average_risk_score"] == 32.75  # (55.5 + 10.0) / 2
    assert body["latest"]["harsh_accel_count"] == 2


def test_top_risk_against_real_influx(client):
    body = client.get("/drivers/top-risk?limit=10").json()
    assert [(d["driver_id"], d["risk_score"]) for d in body["drivers"]] == [("driver_b", 88.25), ("driver_a", 10.0)]
    assert client.get("/drivers/top-risk?limit=1").json()["drivers"][0]["driver_id"] == "driver_b"


def test_trip_against_real_influx(client, seeded):
    body = client.get(f"/trips/{seeded['trip_a']}").json()
    assert [h["risk_score"] for h in body["history"]] == [30.0, 55.5]
    assert body["driver_id"] == "driver_a"


def test_not_found_against_real_influx(client):
    assert client.get("/drivers/nobody/risk").status_code == 404
    assert client.get(f"/trips/{uuid.uuid4()}").status_code == 404
