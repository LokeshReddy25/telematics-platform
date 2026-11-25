"""Risk-score routes: record a computed score (the Spark streaming job
calls this after scoring a trip window) and read a driver's scores.

Scoping rule (per the spec): a login linked to a driver_id may only read
that driver's scores. A login with no driver_id is a service/admin
account (e.g. the Spark job's writer) and is not scoped.
"""

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.orm import Session

from .. import auth, models, schemas
from ..database import get_db

router = APIRouter(prefix="/drivers", tags=["risk"])


def _check_scope(current_user: models.User, driver_id: str) -> None:
    if current_user.driver_id is not None and current_user.driver_id != driver_id:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="You may only read your own risk scores")


@router.post("/{driver_id}/risk-scores", response_model=schemas.RiskScoreOut, status_code=status.HTTP_201_CREATED)
def record_risk_score(
    driver_id: str,
    score_in: schemas.RiskScoreCreate,
    db: Session = Depends(get_db),
    current_user: models.User = Depends(auth.get_current_user),
):
    _check_scope(current_user, driver_id)
    if score_in.driver_id != driver_id:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="driver_id in body must match the URL")
    # vehicle_id / trip_id are foreign keys: a score linked to a vehicle or
    # trip that does not exist is a 404, not a 500 from the FK violation.
    if score_in.vehicle_id is not None and db.get(models.Vehicle, score_in.vehicle_id) is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="vehicle_id not found")
    if score_in.trip_id is not None and db.get(models.Trip, score_in.trip_id) is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="trip_id not found")
    score = models.DriverRiskScore(
        driver_id=score_in.driver_id,
        vehicle_id=score_in.vehicle_id,
        trip_id=score_in.trip_id,
        score=score_in.score,
        risk_level=models.risk_level_for(score_in.score),
        model_version=score_in.model_version,
    )
    db.add(score)
    db.commit()
    db.refresh(score)
    return score


@router.get("/{driver_id}/risk-score", response_model=schemas.RiskScoreOut)
def get_latest_risk_score(
    driver_id: str,
    db: Session = Depends(get_db),
    current_user: models.User = Depends(auth.get_current_user),
):
    _check_scope(current_user, driver_id)
    score = (
        db.query(models.DriverRiskScore)
        .filter(models.DriverRiskScore.driver_id == driver_id)
        .order_by(models.DriverRiskScore.computed_at.desc())
        .first()
    )
    if score is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="No risk scores for this driver")
    return score


@router.get("/{driver_id}/risk-scores", response_model=list[schemas.RiskScoreOut])
def list_risk_scores(
    driver_id: str,
    limit: int = Query(default=10, ge=1, le=100),
    db: Session = Depends(get_db),
    current_user: models.User = Depends(auth.get_current_user),
):
    _check_scope(current_user, driver_id)
    return (
        db.query(models.DriverRiskScore)
        .filter(models.DriverRiskScore.driver_id == driver_id)
        .order_by(models.DriverRiskScore.computed_at.desc())
        .limit(limit)
        .all()
    )
