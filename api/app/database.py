"""
database.py — SQLAlchemy engine and session for the Vehicle Telematics API.

The connection string comes from DATABASE_URL so the same code runs against
three different databases without edits:
  - docker compose: postgresql://telematics:telematics-dev@postgres:5432/telematics
  - local dev without Docker: sqlite:///./telematics.db (the default below)
  - pytest: an in-memory SQLite database (see api/tests/conftest.py)

Tables are created on app startup (main.py). That is the pragmatic choice
for a first API; Alembic migrations are the production path and are covered
in the learning programme that follows this build.
"""

import os

from sqlalchemy import create_engine
from sqlalchemy.orm import DeclarativeBase, sessionmaker

DATABASE_URL = os.environ.get("DATABASE_URL", "sqlite:///./telematics.db")

# SQLite needs check_same_thread=False for FastAPI's threaded server;
# Postgres ignores the argument, so it is only passed for sqlite URLs.
_connect_args = {"check_same_thread": False} if DATABASE_URL.startswith("sqlite") else {}

engine = create_engine(DATABASE_URL, connect_args=_connect_args)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)


class Base(DeclarativeBase):
    """Shared declarative base — every model in models.py inherits this."""

    pass


def get_db():
    """FastAPI dependency: one database session per request, always closed."""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
