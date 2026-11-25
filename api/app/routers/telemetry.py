"""Telemetry routes: ingest raw vehicle events (single or batch) and read
them back per trip or per vehicle, in timestamp order.

The ingest endpoint accepts either one event object or a JSON list of
them, so a vehicle (or the simulator) can stream one event at a time
while a batch uploader can send a whole trip window in one request.
Every event is validated against its trip and vehicle first — an event
for an unknown trip or vehicle is a 404, never silently stored.
"""

from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.orm import Session

from .. import auth, models, schemas
from ..database import get_db

router = APIRouter(tags=["telemetry"])


def _check_trip_and_vehicle(db: Session, trip_id: str, vehicle_id: str) -> None:
    if db.query(models.Trip).filter(models.Trip.id == trip_id).first() is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Trip not found")
    if db.query(models.Vehicle).filter(models.Vehicle.id == vehicle_id).first() is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Vehicle not found")


@router.post("/telemetry", response_model=schemas.TelemetryIngestResult, status_code=status.HTTP_201_CREATED)
def ingest_telemetry(
    payload: schemas.TelemetryEventCreate | list[schemas.TelemetryEventCreate],
    db: Session = Depends(get_db),
    _=Depends(auth.get_current_user),
):
    events = payload if isinstance(payload, list) else [payload]
    for event_in in events:
        _check_trip_and_vehicle(db, event_in.trip_id, event_in.vehicle_id)
    for event_in in events:
        db.add(models.TelemetryEvent(**event_in.model_dump()))
    db.commit()
    return {"ingested": len(events)}


@router.get("/trips/{trip_id}/telemetry", response_model=list[schemas.TelemetryEventOut])
def get_trip_telemetry(trip_id: str, db: Session = Depends(get_db), _=Depends(auth.get_current_user)):
    if db.query(models.Trip).filter(models.Trip.id == trip_id).first() is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Trip not found")
    return (
        db.query(models.TelemetryEvent)
        .filter(models.TelemetryEvent.trip_id == trip_id)
        .order_by(models.TelemetryEvent.timestamp)
        .all()
    )


@router.get("/vehicles/{vehicle_id}/telemetry", response_model=list[schemas.TelemetryEventOut])
def get_vehicle_telemetry(
    vehicle_id: str,
    from_: datetime | None = Query(default=None, alias="from"),
    to: datetime | None = Query(default=None),
    db: Session = Depends(get_db),
    _=Depends(auth.get_current_user),
):
    if db.query(models.Vehicle).filter(models.Vehicle.id == vehicle_id).first() is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Vehicle not found")
    query = db.query(models.TelemetryEvent).filter(models.TelemetryEvent.vehicle_id == vehicle_id)
    if from_ is not None:
        query = query.filter(models.TelemetryEvent.timestamp >= from_)
    if to is not None:
        query = query.filter(models.TelemetryEvent.timestamp <= to)
    return query.order_by(models.TelemetryEvent.timestamp).all()
