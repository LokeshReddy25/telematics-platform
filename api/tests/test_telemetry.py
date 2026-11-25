"""Telemetry tests: ingest a single event and a batch, events come back
in timestamp order for a trip, and events for unknown trips/vehicles
are rejected."""

from tests.conftest import client, make_trip, make_vehicle, register_user


def _event(trip_id, vehicle_id, ts, speed):
    return {
        "trip_id": trip_id,
        "vehicle_id": vehicle_id,
        "timestamp": ts,
        "speed_kmh": speed,
        "latitude": 13.0827,
        "longitude": 80.2707,
        "acceleration": 0.12,
    }


def test_ingest_single_event():
    headers = register_user("tel1@example.com")
    vehicle = make_vehicle(headers, vin="VIN-TEL-001")
    trip = make_trip(headers, vehicle["id"])
    resp = client.post(
        "/telemetry", json=_event(trip["id"], vehicle["id"], "2026-11-25T08:00:00Z", 45.0), headers=headers
    )
    assert resp.status_code == 201
    assert resp.json() == {"ingested": 1}


def test_ingest_batch_and_order():
    headers = register_user("tel2@example.com")
    vehicle = make_vehicle(headers, vin="VIN-TEL-002")
    trip = make_trip(headers, vehicle["id"])
    # Sent out of order on purpose — the read-back must still be sorted.
    batch = [
        _event(trip["id"], vehicle["id"], "2026-11-25T08:00:06Z", 52.0),
        _event(trip["id"], vehicle["id"], "2026-11-25T08:00:00Z", 40.0),
        _event(trip["id"], vehicle["id"], "2026-11-25T08:00:02Z", 47.5),
    ]
    resp = client.post("/telemetry", json=batch, headers=headers)
    assert resp.status_code == 201
    assert resp.json() == {"ingested": 3}
    fetched = client.get(f"/trips/{trip['id']}/telemetry", headers=headers)
    assert fetched.status_code == 200
    speeds = [e["speed_kmh"] for e in fetched.json()]
    assert speeds == [40.0, 47.5, 52.0]


def test_ingest_for_unknown_trip_is_404():
    headers = register_user("tel3@example.com")
    vehicle = make_vehicle(headers, vin="VIN-TEL-003")
    resp = client.post(
        "/telemetry",
        json=_event("no-such-trip", vehicle["id"], "2026-11-25T08:00:00Z", 45.0),
        headers=headers,
    )
    assert resp.status_code == 404


def test_vehicle_telemetry_time_filter():
    headers = register_user("tel4@example.com")
    vehicle = make_vehicle(headers, vin="VIN-TEL-004")
    trip = make_trip(headers, vehicle["id"])
    batch = [
        _event(trip["id"], vehicle["id"], "2026-11-25T08:00:00Z", 40.0),
        _event(trip["id"], vehicle["id"], "2026-11-25T08:10:00Z", 60.0),
        _event(trip["id"], vehicle["id"], "2026-11-25T08:20:00Z", 55.0),
    ]
    client.post("/telemetry", json=batch, headers=headers)
    fetched = client.get(
        f"/vehicles/{vehicle['id']}/telemetry?from=2026-11-25T08:05:00Z&to=2026-11-25T08:15:00Z",
        headers=headers,
    )
    assert fetched.status_code == 200
    assert [e["speed_kmh"] for e in fetched.json()] == [60.0]
