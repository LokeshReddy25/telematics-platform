"""
main.py: Risk Score API. A read-only REST layer over the driver risk scores
that the Spark streaming job writes to InfluxDB.

Run:   uvicorn app.main:app --port 8000      (from risk_api/)
Docs:  /docs (Swagger UI), /redoc, /openapi.json

Every error response has the same shape:
    {"error": {"code": "...", "message": "...", "details": ...}}
"""

from contextlib import asynccontextmanager
from typing import Any, Dict, List, Optional

from fastapi import Depends, FastAPI, Path, Query, Request
from fastapi.encoders import jsonable_encoder
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

from .config import Settings
from .repository import ID_PATTERN, RepositoryError, RiskRepository
from .schemas import (
    DriverRisk,
    ErrorResponse,
    HealthResponse,
    RiskRecord,
    TopRiskEntry,
    TopRiskResponse,
    TripRisk,
)

_FIELDS = ("risk_score", "harsh_brake_count", "harsh_accel_count", "max_speed", "avg_speed", "night_driving_ratio")

_ERRORS = {
    404: {"model": ErrorResponse, "description": "Not found"},
    422: {"model": ErrorResponse, "description": "Invalid input"},
    503: {"model": ErrorResponse, "description": "InfluxDB unavailable"},
}


class NotFound(Exception):
    def __init__(self, message: str):
        self.message = message


def _error(status: int, code: str, message: str, details: Any = None) -> JSONResponse:
    body = {"error": {"code": code, "message": message, "details": details}}
    return JSONResponse(status_code=status, content=jsonable_encoder(body))


def _to_record(row: Dict[str, Any]) -> RiskRecord:
    data = {k: row.get(k) for k in _FIELDS}
    return RiskRecord(
        driver_id=row["driver_id"],
        trip_id=row["trip_id"],
        risk_category=row["risk_category"],
        scored_at=row["_time"],
        **data,
    )


def create_app(settings: Optional[Settings] = None, repository: Optional[RiskRepository] = None) -> FastAPI:
    """App factory. Tests pass a fake repository; production builds one from env vars at startup."""

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        owns_repo = app.state.repository is None
        if owns_repo:
            app.state.repository = RiskRepository(settings or Settings.from_env())
        yield
        if owns_repo:
            app.state.repository.close()

    app = FastAPI(
        title="Driver Risk Score API",
        description="Read-only REST API over the driver risk scores that the Kafka → Spark → ML "
        "pipeline writes to InfluxDB (measurement `driver_risk`).",
        version="1.0.0",
        lifespan=lifespan,
    )
    app.state.repository = repository

    def get_repo(request: Request) -> RiskRepository:
        return request.app.state.repository

    @app.exception_handler(RequestValidationError)
    async def _validation(_: Request, exc: RequestValidationError):
        details = [{"loc": list(e["loc"]), "msg": e["msg"], "type": e["type"]} for e in exc.errors()]
        return _error(422, "validation_error", "Request parameters are invalid", details)

    @app.exception_handler(StarletteHTTPException)
    async def _http(_: Request, exc: StarletteHTTPException):
        code = {404: "not_found", 405: "method_not_allowed"}.get(exc.status_code, "http_error")
        return _error(exc.status_code, code, str(exc.detail))

    @app.exception_handler(NotFound)
    async def _not_found(_: Request, exc: NotFound):
        return _error(404, "not_found", exc.message)

    @app.exception_handler(RepositoryError)
    async def _repo(_: Request, exc: RepositoryError):
        return _error(503, "upstream_unavailable", "Could not read risk scores from InfluxDB")

    @app.get("/health", response_model=HealthResponse, responses={503: {"model": HealthResponse}}, tags=["ops"])
    def health(repo: RiskRepository = Depends(get_repo)):
        """Liveness/readiness: 200 if InfluxDB answers its ping, 503 otherwise."""
        if repo.ping():
            return HealthResponse(status="ok", influxdb="reachable")
        return JSONResponse(status_code=503, content={"status": "degraded", "influxdb": "unreachable"})

    @app.get("/drivers/top-risk", response_model=TopRiskResponse, responses=_ERRORS, tags=["drivers"])
    def top_risk(
        limit: int = Query(5, ge=1, le=100, description="How many drivers to return (1-100)"),
        repo: RiskRepository = Depends(get_repo),
    ):
        """Drivers ranked by their most recent risk score, highest first."""
        rows = repo.top_risk(limit)
        drivers = [
            TopRiskEntry(
                rank=i,
                driver_id=r["driver_id"],
                risk_score=r["_value"],
                risk_category=r["risk_category"],
                trip_id=r["trip_id"],
                scored_at=r["_time"],
            )
            for i, r in enumerate(rows, start=1)
        ]
        return TopRiskResponse(limit=limit, drivers=drivers)

    @app.get("/drivers/{driver_id}/risk", response_model=DriverRisk, responses=_ERRORS, tags=["drivers"])
    def driver_risk(
        driver_id: str = Path(pattern=ID_PATTERN, description="e.g. driver_0003"),
        repo: RiskRepository = Depends(get_repo),
    ):
        """Latest risk score for a driver, plus a summary over the lookback window."""
        rows = repo.driver_history(driver_id)
        if not rows:
            raise NotFound(f"No risk scores found for driver '{driver_id}'")
        records = [_to_record(r) for r in rows]
        latest_per_trip: Dict[str, RiskRecord] = {}
        for rec in records:  # rows are oldest first, so the last one per trip wins
            latest_per_trip[rec.trip_id] = rec
        avg = sum(r.risk_score for r in latest_per_trip.values()) / len(latest_per_trip)
        return DriverRisk(
            driver_id=driver_id,
            latest=records[-1],
            trips_scored=len(latest_per_trip),
            records=len(records),
            average_risk_score=round(avg, 2),
        )

    @app.get("/trips/{trip_id}", response_model=TripRisk, responses=_ERRORS, tags=["trips"])
    def trip(
        trip_id: str = Path(pattern=ID_PATTERN, description="Trip UUID"),
        repo: RiskRepository = Depends(get_repo),
    ):
        """Every risk score written for one trip (Spark re-scores a trip as new events arrive)."""
        rows = repo.trip_history(trip_id)
        if not rows:
            raise NotFound(f"No risk scores found for trip '{trip_id}'")
        history: List[RiskRecord] = [_to_record(r) for r in rows]
        return TripRisk(trip_id=trip_id, driver_id=history[-1].driver_id, latest=history[-1], history=history)

    return app


app = create_app()
