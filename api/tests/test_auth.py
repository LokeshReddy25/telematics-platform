"""Auth tests: register/login works, wrong password rejected, missing or
expired tokens are 401s on protected endpoints."""

from app import auth as auth_module
from tests.conftest import client, register_user


def test_register_and_login_round_trip():
    headers = register_user("driver@example.com", driver_id="driver_0001")
    assert headers["Authorization"].startswith("Bearer ")
    # The token actually authorises a protected call.
    resp = client.get("/vehicles", headers=headers)
    assert resp.status_code == 200


def test_register_duplicate_email_is_409():
    register_user("dup@example.com")
    resp = client.post("/auth/register", json={"email": "dup@example.com", "password": "password123"})
    assert resp.status_code == 409


def test_login_wrong_password_is_401():
    register_user("wrongpw@example.com")
    resp = client.post("/auth/login", data={"username": "wrongpw@example.com", "password": "not-the-password"})
    assert resp.status_code == 401


def test_missing_token_is_401():
    resp = client.get("/vehicles")
    assert resp.status_code == 401


def test_expired_token_is_401():
    # A token that expired one minute ago must be rejected.
    token = auth_module.create_access_token(subject="driver@example.com", expires_minutes=-1)
    resp = client.get("/vehicles", headers={"Authorization": f"Bearer {token}"})
    assert resp.status_code == 401


def test_garbage_token_is_401():
    resp = client.get("/vehicles", headers={"Authorization": "Bearer this-is-not-a-jwt"})
    assert resp.status_code == 401
