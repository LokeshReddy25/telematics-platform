from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient

from app.main import create_app
from app.repository import RepositoryError

T0 = datetime(2026, 11, 26, 6, 0, 0, tzinfo=timezone.utc)


def row(driver_id, trip_id, score, category, minutes=0, **extra):
    """A pivoted InfluxDB row, shaped like influxdb_client FluxRecord.values."""
    base = {
        "_time": T0 + timedelta(minutes=minutes),
        "driver_id": driver_id,
        "trip_id": trip_id,
        "risk_category": category,
        "risk_score": score,
        "harsh_brake_count": 2,
        "harsh_accel_count": 3,
        "max_speed": 101.5,
        "avg_speed": 72.25,
        "night_driving_ratio": 0.0,
    }
    base.update(extra)
    return base


class FakeRepository:
    """Stands in for RiskRepository so API tests never touch InfluxDB."""

    def __init__(self):
        self.healthy = True
        self.fail = False
        self.drivers = {}
        self.trips = {}
        self.top = []
        self.calls = []

    def _maybe_fail(self):
        if self.fail:
            raise RepositoryError("boom")

    def ping(self):
        return self.healthy

    def driver_history(self, driver_id):
        self.calls.append(("driver_history", driver_id))
        self._maybe_fail()
        return self.drivers.get(driver_id, [])

    def trip_history(self, trip_id):
        self.calls.append(("trip_history", trip_id))
        self._maybe_fail()
        return self.trips.get(trip_id, [])

    def top_risk(self, limit):
        self.calls.append(("top_risk", limit))
        self._maybe_fail()
        return self.top[:limit]

    def close(self):
        pass


@pytest.fixture
def repo():
    return FakeRepository()


@pytest.fixture
def client(repo):
    with TestClient(create_app(repository=repo)) as c:
        yield c
