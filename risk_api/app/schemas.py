"""
schemas.py: Pydantic response models. These also drive the OpenAPI docs at /docs.
"""

from datetime import datetime
from typing import Any, List, Literal, Optional

from pydantic import BaseModel, Field

RiskCategory = Literal["Low", "Medium", "High", "Critical"]


class RiskRecord(BaseModel):
    """One scored trip window, as written by storage/influxdb_writer.py."""

    driver_id: str = Field(examples=["driver_0003"])
    trip_id: str = Field(examples=["f010dbad-d6c8-4b26-92ed-916f7f836513"])
    scored_at: datetime
    risk_score: float = Field(ge=0, le=100, examples=[38.13])
    risk_category: RiskCategory
    harsh_brake_count: Optional[int] = None
    harsh_accel_count: Optional[int] = None
    max_speed: Optional[float] = None
    avg_speed: Optional[float] = None
    night_driving_ratio: Optional[float] = None


class DriverRisk(BaseModel):
    driver_id: str
    latest: RiskRecord = Field(description="Most recent score written for this driver")
    trips_scored: int = Field(description="Distinct trips with at least one score in the lookback window")
    records: int = Field(description="Score records in the lookback window (a trip can be re-scored)")
    average_risk_score: float = Field(description="Mean of each trip's most recent score")


class TopRiskEntry(BaseModel):
    rank: int = Field(ge=1)
    driver_id: str
    risk_score: float = Field(description="The driver's most recent risk score")
    risk_category: RiskCategory
    trip_id: str
    scored_at: datetime


class TopRiskResponse(BaseModel):
    limit: int
    drivers: List[TopRiskEntry]


class TripRisk(BaseModel):
    trip_id: str
    driver_id: str
    latest: RiskRecord
    history: List[RiskRecord] = Field(description="Every score for this trip, oldest first")


class HealthResponse(BaseModel):
    status: Literal["ok", "degraded"]
    influxdb: Literal["reachable", "unreachable"]


class ErrorBody(BaseModel):
    code: str = Field(examples=["not_found"])
    message: str
    details: Optional[Any] = None


class ErrorResponse(BaseModel):
    error: ErrorBody
