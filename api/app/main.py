"""
main.py — Vehicle Telematics API (FastAPI).

Entry point: `uvicorn app.main:app`. Tables are created on startup from
the SQLAlchemy models (see database.py for the Alembic note). All routes
are mounted from app/routers/; interactive docs live at /docs.
"""

from fastapi import Depends, FastAPI
from sqlalchemy import text
from sqlalchemy.orm import Session

from .database import Base, engine, get_db
from .routers import auth, risk, telemetry, trips, vehicles

app = FastAPI(
    title="Vehicle Telematics API",
    description="REST API for the Connected Vehicle Insurance Telematics Platform: "
    "vehicles, trips, telemetry ingest and driver risk scores, with JWT auth.",
    version="0.1.0",
)

app.include_router(auth.router)
app.include_router(vehicles.router)
app.include_router(trips.router)
app.include_router(telemetry.router)
app.include_router(risk.router)


@app.on_event("startup")
def create_tables() -> None:
    Base.metadata.create_all(bind=engine)


@app.get("/health")
def health(db: Session = Depends(get_db)):
    """Liveness check that also proves the database is reachable."""
    db.execute(text("SELECT 1"))
    return {"status": "ok"}
