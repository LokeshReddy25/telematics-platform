"""Risk-score tests: latest-score returns the newest by computed_at,
an empty driver is a 404, out-of-range scores are rejected, and a
driver-scoped login cannot read another driver's scores."""

from tests.conftest import client, make_trip, make_vehicle, register_user


def _record(headers, driver_id, score, model_version="gbm-v1"):
    return client.post(
        f"/drivers/{driver_id}/risk-scores",
        json={"driver_id": driver_id, "score": score, "model_version": model_version},
        headers=headers,
    )


def test_latest_score_returns_newest():
    headers = register_user("risk1@example.com")  # service account: no driver_id, not scoped
    _record(headers, "driver_0001", 30.0)
    _record(headers, "driver_0001", 72.0)
    resp = client.get("/drivers/driver_0001/risk-score", headers=headers)
    assert resp.status_code == 200
    assert resp.json()["score"] == 72.0
    assert resp.json()["risk_level"] == "HIGH"


def test_risk_level_derived_from_score():
    headers = register_user("risk2@example.com")
    resp = _record(headers, "driver_0002", 12.0)
    assert resp.status_code == 201
    assert resp.json()["risk_level"] == "LOW"
    resp = _record(headers, "driver_0002", 35.0)
    assert resp.json()["risk_level"] == "MEDIUM"


def test_empty_driver_is_404():
    headers = register_user("risk3@example.com")
    resp = client.get("/drivers/driver_nobody/risk-score", headers=headers)
    assert resp.status_code == 404


def test_score_out_of_range_is_422():
    headers = register_user("risk4@example.com")
    assert _record(headers, "driver_0004", 101.0).status_code == 422
    assert _record(headers, "driver_0004", -1.0).status_code == 422


def test_driver_scoped_user_cannot_read_other_driver():
    # This login is linked to driver_0005, so driver_0006's scores are off-limits.
    headers = register_user("risk5@example.com", driver_id="driver_0005")
    admin = register_user("risk-admin@example.com")
    _record(admin, "driver_0006", 44.0)
    assert client.get("/drivers/driver_0006/risk-score", headers=headers).status_code == 403
    # …but their own scores are fine.
    _record(admin, "driver_0005", 20.0)
    own = client.get("/drivers/driver_0005/risk-score", headers=headers)
    assert own.status_code == 200
    assert own.json()["score"] == 20.0


def test_unknown_vehicle_or_trip_is_404_not_500():
    # A score linked to a vehicle or trip that does not exist must be a
    # 404, not a 500 from the foreign-key violation (seen against Postgres).
    headers = register_user("risk7@example.com")
    resp = client.post(
        "/drivers/driver_0008/risk-scores",
        json={"driver_id": "driver_0008", "vehicle_id": "no-such-vehicle", "score": 50.0},
        headers=headers,
    )
    assert resp.status_code == 404
    resp = client.post(
        "/drivers/driver_0008/risk-scores",
        json={"driver_id": "driver_0008", "trip_id": "no-such-trip", "score": 50.0},
        headers=headers,
    )
    assert resp.status_code == 404
    # …and a score linked to a real vehicle and trip is accepted.
    vehicle = make_vehicle(headers, vin="1HGCM82633A004353", driver_id="driver_0008")
    trip = make_trip(headers, vehicle["id"])
    resp = client.post(
        "/drivers/driver_0008/risk-scores",
        json={
            "driver_id": "driver_0008",
            "vehicle_id": vehicle["id"],
            "trip_id": trip["id"],
            "score": 50.0,
        },
        headers=headers,
    )
    assert resp.status_code == 201
    assert resp.json()["vehicle_id"] == vehicle["id"]
    assert resp.json()["trip_id"] == trip["id"]


def test_list_risk_scores_newest_first():
    headers = register_user("risk6@example.com")
    _record(headers, "driver_0007", 10.0)
    _record(headers, "driver_0007", 20.0)
    _record(headers, "driver_0007", 30.0)
    resp = client.get("/drivers/driver_0007/risk-scores?limit=2", headers=headers)
    assert resp.status_code == 200
    assert [s["score"] for s in resp.json()] == [30.0, 20.0]
