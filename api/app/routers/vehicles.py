"""Vehicle routes: register a vehicle, list vehicles, fetch one by id."""

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from .. import auth, models, schemas
from ..database import get_db

router = APIRouter(prefix="/vehicles", tags=["vehicles"])


@router.post("", response_model=schemas.VehicleOut, status_code=status.HTTP_201_CREATED)
def create_vehicle(vehicle_in: schemas.VehicleCreate, db: Session = Depends(get_db), _=Depends(auth.get_current_user)):
    if db.query(models.Vehicle).filter(models.Vehicle.vin == vehicle_in.vin).first():
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="A vehicle with this VIN already exists")
    vehicle = models.Vehicle(**vehicle_in.model_dump())
    db.add(vehicle)
    db.commit()
    db.refresh(vehicle)
    return vehicle


@router.get("", response_model=list[schemas.VehicleOut])
def list_vehicles(db: Session = Depends(get_db), _=Depends(auth.get_current_user)):
    return db.query(models.Vehicle).order_by(models.Vehicle.created_at).all()


@router.get("/{vehicle_id}", response_model=schemas.VehicleOut)
def get_vehicle(vehicle_id: str, db: Session = Depends(get_db), _=Depends(auth.get_current_user)):
    vehicle = db.query(models.Vehicle).filter(models.Vehicle.id == vehicle_id).first()
    if vehicle is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Vehicle not found")
    return vehicle
