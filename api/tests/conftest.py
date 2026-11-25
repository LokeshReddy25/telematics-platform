"""
conftest.py — fixtures for the API test suite.

Tests run against a fresh in-memory SQLite database (StaticPool keeps the
single connection alive across the session, so the tables created once
stay visible to every request). The app's real get_db dependency is
overridden to hand out that test session, so no Postgres is needed to
run `pytest api/tests` — CI runs this job before any Docker is built.
"""

import os

os.environ["JWT_SECRET"] = "test-secret-for-pytest-only"

from fastapi.testclient import TestClient  # noqa: E402
from sqlalchemy import create_engine  # noqa: E402
from sqlalchemy.orm import sessionmaker  # noqa: E402
from sqlalchemy.pool import StaticPool  # noqa: E402

from app import auth as auth_module  # noqa: E402,F401  (importing app.auth loads the package)
from app.database import Base, get_db  # noqa: E402
from app.main import app  # noqa: E402

engine = create_engine(
    "sqlite://",
    connect_args={"check_same_thread": False},
    poolclass=StaticPool,
)
TestingSessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

Base.metadata.create_all(bind=engine)


def override_get_db():
    db = TestingSessionLocal()
    try:
        yield db
    finally:
        db.close()


app.dependency_overrides[get_db] = override_get_db

client = TestClient(app)


def register_user(email: str, password: str = "password123", driver_id: str | None = None) -> dict:
    """Register a user and return an Authorization header for them."""
    payload = {"email": email, "password": password}
    if driver_id is not None:
        payload["driver_id"] = driver_id
    resp = client.post("/auth/register", json=payload)
    assert resp.status_code == 201, resp.text
    login = client.post("/auth/login", data={"username": email, "password": password})
    assert login.status_code == 200, login.text
    token = login.json()["access_token"]
    return {"Authorization": f"Bearer {token}"}


def make_vehicle(headers: dict, vin: str = "1HGCM82633A004352", driver_id: str = "driver_0001") -> dict:
    resp = client.post(
        "/vehicles",
        json={"vin": vin, "make": "Maruti Suzuki", "model": "Swift", "year": 2022, "driver_id": driver_id},
        headers=headers,
    )
    assert resp.status_code == 201, resp.text
    return resp.json()


def make_trip(headers: dict, vehicle_id: str) -> dict:
    resp = client.post(
        "/trips",
        json={
            "vehicle_id": vehicle_id,
            "started_at": "2026-11-25T08:00:00Z",
            "start_latitude": 13.0827,
            "start_longitude": 80.2707,
        },
        headers=headers,
    )
    assert resp.status_code == 201, resp.text
    return resp.json()
