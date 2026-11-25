"""Trip routes: start a trip, list trips, fetch one by id."""

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from .. import auth, models, schemas
from ..database import get_db

router = APIRouter(prefix="/trips", tags=["trips"])


@router.post("", response_model=schemas.TripOut, status_code=status.HTTP_201_CREATED)
def create_trip(trip_in: schemas.TripCreate, db: Session = Depends(get_db), _=Depends(auth.get_current_user)):
    if db.query(models.Vehicle).filter(models.Vehicle.id == trip_in.vehicle_id).first() is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Vehicle not found")
    trip = models.Trip(**trip_in.model_dump())
    db.add(trip)
    db.commit()
    db.refresh(trip)
    return trip


@router.get("", response_model=list[schemas.TripOut])
def list_trips(db: Session = Depends(get_db), _=Depends(auth.get_current_user)):
    return db.query(models.Trip).order_by(models.Trip.started_at.desc()).all()


@router.get("/{trip_id}", response_model=schemas.TripOut)
def get_trip(trip_id: str, db: Session = Depends(get_db), _=Depends(auth.get_current_user)):
    trip = db.query(models.Trip).filter(models.Trip.id == trip_id).first()
    if trip is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Trip not found")
    return trip
