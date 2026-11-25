"""
models.py — SQLAlchemy models for the Vehicle Telematics API.

Four domain tables (Vehicle, Trip, TelemetryEvent, DriverRiskScore) plus a
User table for JWT auth. Field names deliberately match the existing
pipeline where the two meet: the simulator emits speed_kmh / acceleration_g /
latitude / longitude per event, and the Gradient Boosting risk model writes
scores in the 0-100 range with model_version "gbm-v1" (see ml/risk_scorer.py).

Design decision (settled 2026-11-25): PostgreSQL is the API's store for
risk scores too — the Spark job writes scores here (via the
POST /drivers/{driver_id}/risk-scores endpoint) rather than the API
reading them back out of InfluxDB. InfluxDB stays as the time-series sink
for the streaming pipeline; Postgres owns the API's data.
"""

import uuid
from datetime import datetime, timezone

from sqlalchemy import DateTime, Float, ForeignKey, Integer, String
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .database import Base


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _uuid() -> str:
    return str(uuid.uuid4())


class User(Base):
    """Login account. driver_id links a login to a driver, which is what
    scopes risk-score reads: a user with a driver_id may only read that
    driver's scores (see routers/risk.py). Users without a driver_id are
    treated as service/admin accounts."""

    __tablename__ = "users"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=_uuid)
    email: Mapped[str] = mapped_column(String, unique=True, index=True)
    hashed_password: Mapped[str] = mapped_column(String)
    driver_id: Mapped[str | None] = mapped_column(String, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)


class Vehicle(Base):
    """A connected vehicle. VIN is the natural unique key; driver_id is the
    owner/driver the insurer scores."""

    __tablename__ = "vehicles"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=_uuid)
    vin: Mapped[str] = mapped_column(String, unique=True, index=True)
    make: Mapped[str] = mapped_column(String)
    model: Mapped[str] = mapped_column(String)
    year: Mapped[int] = mapped_column(Integer)
    driver_id: Mapped[str] = mapped_column(String, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)

    trips: Mapped[list["Trip"]] = relationship("Trip", back_populates="vehicle")


class Trip(Base):
    """One drive, from ignition-on to ignition-off. end_* fields stay null
    until the trip closes; distance_km accumulates as events arrive."""

    __tablename__ = "trips"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=_uuid)
    vehicle_id: Mapped[str] = mapped_column(String, ForeignKey("vehicles.id"), index=True)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    ended_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    start_latitude: Mapped[float] = mapped_column(Float)
    start_longitude: Mapped[float] = mapped_column(Float)
    end_latitude: Mapped[float | None] = mapped_column(Float, nullable=True)
    end_longitude: Mapped[float | None] = mapped_column(Float, nullable=True)
    distance_km: Mapped[float] = mapped_column(Float, default=0.0)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)

    vehicle: Mapped["Vehicle"] = relationship("Vehicle", back_populates="trips")
    events: Mapped[list["TelemetryEvent"]] = relationship("TelemetryEvent", back_populates="trip")


class TelemetryEvent(Base):
    """One raw event from a vehicle — the same fields the simulator emits
    (speed_kmh, acceleration in g, GPS), plus fuel/engine readings when the
    vehicle reports them (nullable: the simulator does not emit them)."""

    __tablename__ = "telemetry_events"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=_uuid)
    trip_id: Mapped[str] = mapped_column(String, ForeignKey("trips.id"), index=True)
    vehicle_id: Mapped[str] = mapped_column(String, ForeignKey("vehicles.id"), index=True)
    timestamp: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    speed_kmh: Mapped[float] = mapped_column(Float)
    latitude: Mapped[float] = mapped_column(Float)
    longitude: Mapped[float] = mapped_column(Float)
    acceleration: Mapped[float | None] = mapped_column(Float, nullable=True)
    fuel_level: Mapped[float | None] = mapped_column(Float, nullable=True)
    engine_temp: Mapped[float | None] = mapped_column(Float, nullable=True)

    trip: Mapped["Trip"] = relationship("Trip", back_populates="events")


class DriverRiskScore(Base):
    """A computed driver risk score. Written by the Spark streaming job
    (ml/risk_scorer.py) via POST /drivers/{driver_id}/risk-scores once a
    trip window is scored. risk_level is derived server-side from the
    score — clients never set it directly."""

    __tablename__ = "driver_risk_scores"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=_uuid)
    driver_id: Mapped[str] = mapped_column(String, index=True)
    vehicle_id: Mapped[str | None] = mapped_column(String, ForeignKey("vehicles.id"), nullable=True)
    trip_id: Mapped[str | None] = mapped_column(String, ForeignKey("trips.id"), nullable=True)
    score: Mapped[float] = mapped_column(Float)  # 0-100, from the Gradient Boosting model
    risk_level: Mapped[str] = mapped_column(String)  # LOW / MEDIUM / HIGH
    computed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)
    model_version: Mapped[str] = mapped_column(String, default="gbm-v1")


def risk_level_for(score: float) -> str:
    """Map a 0-100 score to a risk band. Cut-offs match the first two bands
    in ml/risk_scorer.py (<25 Low, <50 Medium); the API collapses the
    model's High/Critical top end into a single HIGH band, per the spec."""
    if score < 25:
        return "LOW"
    if score < 50:
        return "MEDIUM"
    return "HIGH"
