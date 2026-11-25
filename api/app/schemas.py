"""
schemas.py — Pydantic request/response models for the Vehicle Telematics API.

One pair per resource: a Create model (what clients send) and an Out model
(what the API returns, always including the server-generated id and
timestamps). Validation lives here — e.g. a risk score outside 0-100 is
rejected with a 422 before it ever reaches the database — so the routers
stay thin.
"""

from datetime import datetime

from pydantic import BaseModel, Field


# --- Auth -----------------------------------------------------------------
class UserCreate(BaseModel):
    email: str
    password: str = Field(min_length=8, description="At least 8 characters.")
    driver_id: str | None = None  # links this login to a driver for risk-score scoping


class UserOut(BaseModel):
    id: str
    email: str
    driver_id: str | None
    created_at: datetime

    model_config = {"from_attributes": True}


class Token(BaseModel):
    access_token: str
    token_type: str = "bearer"


# --- Vehicles ---------------------------------------------------------------
class VehicleCreate(BaseModel):
    vin: str
    make: str
    model: str
    year: int = Field(ge=1900, le=2100)
    driver_id: str


class VehicleOut(BaseModel):
    id: str
    vin: str
    make: str
    model: str
    year: int
    driver_id: str
    created_at: datetime

    model_config = {"from_attributes": True}


# --- Trips ------------------------------------------------------------------
class TripCreate(BaseModel):
    vehicle_id: str
    started_at: datetime
    ended_at: datetime | None = None
    start_latitude: float = Field(ge=-90, le=90)
    start_longitude: float = Field(ge=-180, le=180)
    end_latitude: float | None = Field(default=None, ge=-90, le=90)
    end_longitude: float | None = Field(default=None, ge=-180, le=180)
    distance_km: float = Field(default=0.0, ge=0)


class TripOut(BaseModel):
    id: str
    vehicle_id: str
    started_at: datetime
    ended_at: datetime | None
    start_latitude: float
    start_longitude: float
    end_latitude: float | None
    end_longitude: float | None
    distance_km: float
    created_at: datetime

    model_config = {"from_attributes": True}


# --- Telemetry --------------------------------------------------------------
class TelemetryEventCreate(BaseModel):
    trip_id: str
    vehicle_id: str
    timestamp: datetime
    speed_kmh: float = Field(ge=0)
    latitude: float = Field(ge=-90, le=90)
    longitude: float = Field(ge=-180, le=180)
    acceleration: float | None = None  # g-force; simulator emits acceleration_g
    fuel_level: float | None = Field(default=None, ge=0, le=100)
    engine_temp: float | None = None  # degrees Celsius


class TelemetryEventOut(BaseModel):
    id: str
    trip_id: str
    vehicle_id: str
    timestamp: datetime
    speed_kmh: float
    latitude: float
    longitude: float
    acceleration: float | None
    fuel_level: float | None
    engine_temp: float | None

    model_config = {"from_attributes": True}


class TelemetryIngestResult(BaseModel):
    ingested: int


# --- Risk scores ------------------------------------------------------------
class RiskScoreCreate(BaseModel):
    # risk_level is NOT accepted from clients — the server derives it from
    # the score (models.risk_level_for), so a caller cannot claim LOW for a
    # 95-point driver.
    driver_id: str
    vehicle_id: str | None = None
    trip_id: str | None = None
    score: float = Field(ge=0, le=100)
    model_version: str = "gbm-v1"


class RiskScoreOut(BaseModel):
    id: str
    driver_id: str
    vehicle_id: str | None
    trip_id: str | None
    score: float
    risk_level: str
    computed_at: datetime
    model_version: str

    model_config = {"from_attributes": True}
