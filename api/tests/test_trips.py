"""Trip tests: create + get round-trip, creating a trip for an unknown
vehicle is a 404, unknown trip id is a 404."""

from tests.conftest import client, make_trip, make_vehicle, register_user


def test_create_and_get_trip():
    headers = register_user("trip1@example.com")
    vehicle = make_vehicle(headers, vin="VIN-TRIP-001")
    trip = make_trip(headers, vehicle["id"])
    fetched = client.get(f"/trips/{trip['id']}", headers=headers)
    assert fetched.status_code == 200
    assert fetched.json()["vehicle_id"] == vehicle["id"]
    assert fetched.json()["distance_km"] == 0.0


def test_create_trip_for_unknown_vehicle_is_404():
    headers = register_user("trip2@example.com")
    resp = client.post(
        "/trips",
        json={
            "vehicle_id": "no-such-vehicle",
            "started_at": "2026-11-25T08:00:00Z",
            "start_latitude": 13.0827,
            "start_longitude": 80.2707,
        },
        headers=headers,
    )
    assert resp.status_code == 404


def test_unknown_trip_is_404():
    headers = register_user("trip3@example.com")
    resp = client.get("/trips/does-not-exist", headers=headers)
    assert resp.status_code == 404
