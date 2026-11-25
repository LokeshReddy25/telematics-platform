"""
auth.py — JWT authentication for the Vehicle Telematics API.

Flow: POST /auth/register creates a user with a bcrypt password hash,
POST /auth/login returns a signed JWT (HS256, 60-minute expiry). Every
write endpoint and every risk endpoint requires that token via the
`get_current_user` dependency; a missing, malformed or expired token is
a 401.

The signing secret comes from JWT_SECRET. The default below is for local
dev only and is deliberately obviously fake — production (and the Docker
Compose file) must set a real secret. Never commit a real secret here.
"""

import os
from datetime import datetime, timedelta, timezone

from fastapi import Depends, HTTPException, status
from fastapi.security import OAuth2PasswordBearer
from jose import JWTError, jwt
from passlib.context import CryptContext
from sqlalchemy.orm import Session

from . import models
from .database import get_db

SECRET_KEY = os.environ.get("JWT_SECRET", "dev-only-secret-change-me")
ALGORITHM = "HS256"
ACCESS_TOKEN_EXPIRE_MINUTES = 60

pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")
oauth2_scheme = OAuth2PasswordBearer(tokenUrl="/auth/login")


def verify_password(plain_password: str, hashed_password: str) -> bool:
    return pwd_context.verify(plain_password, hashed_password)


def get_password_hash(password: str) -> str:
    return pwd_context.hash(password)


def create_access_token(subject: str, expires_minutes: int = ACCESS_TOKEN_EXPIRE_MINUTES) -> str:
    """Sign a JWT whose subject is the user's email."""
    expires = datetime.now(timezone.utc) + timedelta(minutes=expires_minutes)
    return jwt.encode({"sub": subject, "exp": expires}, SECRET_KEY, algorithm=ALGORITHM)


def get_current_user(token: str = Depends(oauth2_scheme), db: Session = Depends(get_db)) -> models.User:
    """Resolve the bearer token to a User, or raise 401. Used as a
    dependency on every protected route."""
    credentials_error = HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Could not validate credentials",
        headers={"WWW-Authenticate": "Bearer"},
    )
    try:
        payload = jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
        email: str | None = payload.get("sub")
    except JWTError:
        raise credentials_error from None
    if email is None:
        raise credentials_error
    user = db.query(models.User).filter(models.User.email == email).first()
    if user is None:
        raise credentials_error
    return user
