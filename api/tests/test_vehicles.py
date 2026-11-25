"""Vehicle tests: create + get round-trip, duplicate VIN rejected,
unknown id is a 404, unauthenticated requests are 401s."""

from tests.conftest import client, make_vehicle, register_user


def test_create_and_get_vehicle():
    headers = register_user("veh1@example.com")
    vehicle = make_vehicle(headers, vin="VIN-ROUNDTRIP-001")
    fetched = client.get(f"/vehicles/{vehicle['id']}", headers=headers)
    assert fetched.status_code == 200
    assert fetched.json()["vin"] == "VIN-ROUNDTRIP-001"
    assert fetched.json()["make"] == "Maruti Suzuki"


def test_duplicate_vin_is_409():
    headers = register_user("veh2@example.com")
    make_vehicle(headers, vin="VIN-DUPLICATE-001")
    resp = client.post(
        "/vehicles",
        json={"vin": "VIN-DUPLICATE-001", "make": "Hyundai", "model": "i20", "year": 2021, "driver_id": "driver_2"},
        headers=headers,
    )
    assert resp.status_code == 409


def test_unknown_vehicle_is_404():
    headers = register_user("veh3@example.com")
    resp = client.get("/vehicles/does-not-exist", headers=headers)
    assert resp.status_code == 404


def test_list_vehicles_requires_auth():
    resp = client.get("/vehicles")
    assert resp.status_code == 401
