"""API unit tests: routes, validation, status codes, error format. InfluxDB is faked."""

from conftest import row


def assert_error(resp, status, code):
    assert resp.status_code == status
    body = resp.json()
    assert set(body) == {"error"}
    assert body["error"]["code"] == code
    assert isinstance(body["error"]["message"], str)


# ---- /health -------------------------------------------------------------


def test_health_ok(client):
    resp = client.get("/health")
    assert resp.status_code == 200
    assert resp.json() == {"status": "ok", "influxdb": "reachable"}


def test_health_degraded_returns_503(client, repo):
    repo.healthy = False
    resp = client.get("/health")
    assert resp.status_code == 503
    assert resp.json() == {"status": "degraded", "influxdb": "unreachable"}


# ---- /drivers/{driver_id}/risk ------------------------------------------


def test_driver_risk_summarises_history(client, repo):
    repo.drivers["driver_0001"] = [
        row("driver_0001", "trip-a", 40.0, "Medium", minutes=0),
        row("driver_0001", "trip-a", 60.0, "High", minutes=1),  # re-score of trip-a
        row("driver_0001", "trip-b", 20.0, "Low", minutes=2),
    ]
    resp = client.get("/drivers/driver_0001/risk")
    assert resp.status_code == 200
    body = resp.json()
    assert body["driver_id"] == "driver_0001"
    assert body["records"] == 3
    assert body["trips_scored"] == 2
    assert body["average_risk_score"] == 40.0  # mean of latest per trip: (60 + 20) / 2
    assert body["latest"]["trip_id"] == "trip-b"
    assert body["latest"]["risk_category"] == "Low"
    assert body["latest"]["scored_at"].startswith("2026-11-26T06:02:00")
    assert repo.calls == [("driver_history", "driver_0001")]


def test_driver_risk_unknown_driver_404(client):
    assert_error(client.get("/drivers/driver_9999/risk"), 404, "not_found")


def test_driver_risk_rejects_bad_id_before_querying(client, repo):
    for bad in ['x")%20or%20true%20or%20("', 'x")|>drop(columns:["_value"])', "a" * 65, "driver%20one"]:
        resp = client.get(f"/drivers/{bad}/risk")
        assert_error(resp, 422, "validation_error")
        assert resp.json()["error"]["details"][0]["loc"] == ["path", "driver_id"]
    # A payload containing "/" no longer matches the route at all.
    assert_error(client.get('/drivers/x")//comment/risk'), 404, "not_found")
    assert repo.calls == []


def test_driver_risk_influx_down_503(client, repo):
    repo.fail = True
    assert_error(client.get("/drivers/driver_0001/risk"), 503, "upstream_unavailable")


# ---- /drivers/top-risk ----------------------------------------------------


def _top_rows():
    return [
        {"_time": "2026-11-26T06:00:00+00:00", "driver_id": d, "trip_id": f"t{i}", "risk_category": c, "_value": s}
        for i, (d, s, c) in enumerate(
            [("driver_0002", 91.5, "Critical"), ("driver_0004", 62.0, "High"), ("driver_0001", 12.0, "Low")]
        )
    ]


def test_top_risk_default_limit_is_5(client, repo):
    repo.top = _top_rows()
    resp = client.get("/drivers/top-risk")
    assert resp.status_code == 200
    body = resp.json()
    assert body["limit"] == 5
    assert [d["driver_id"] for d in body["drivers"]] == ["driver_0002", "driver_0004", "driver_0001"]
    assert [d["rank"] for d in body["drivers"]] == [1, 2, 3]
    assert body["drivers"][0]["risk_score"] == 91.5
    assert repo.calls == [("top_risk", 5)]


def test_top_risk_respects_limit(client, repo):
    repo.top = _top_rows()
    body = client.get("/drivers/top-risk?limit=2").json()
    assert len(body["drivers"]) == 2
    assert repo.calls == [("top_risk", 2)]


def test_top_risk_empty_is_200_not_404(client):
    resp = client.get("/drivers/top-risk")
    assert resp.status_code == 200
    assert resp.json() == {"limit": 5, "drivers": []}


def test_top_risk_invalid_limits_422(client, repo):
    for bad in ("0", "101", "-3", "abc", "2.5"):
        assert_error(client.get(f"/drivers/top-risk?limit={bad}"), 422, "validation_error")
    assert repo.calls == []


def test_top_risk_influx_down_503(client, repo):
    repo.fail = True
    assert_error(client.get("/drivers/top-risk"), 503, "upstream_unavailable")


# ---- /trips/{trip_id} -----------------------------------------------------

TRIP = "f010dbad-d6c8-4b26-92ed-916f7f836513"


def test_trip_returns_history_oldest_first(client, repo):
    repo.trips[TRIP] = [
        row("driver_0003", TRIP, 32.92, "Medium", minutes=0),
        row("driver_0003", TRIP, 38.13, "Medium", minutes=1),
    ]
    resp = client.get(f"/trips/{TRIP}")
    assert resp.status_code == 200
    body = resp.json()
    assert body["trip_id"] == TRIP
    assert body["driver_id"] == "driver_0003"
    assert [h["risk_score"] for h in body["history"]] == [32.92, 38.13]
    assert body["latest"]["risk_score"] == 38.13


def test_trip_unknown_404(client):
    assert_error(client.get(f"/trips/{TRIP}"), 404, "not_found")


def test_trip_bad_id_422(client, repo):
    assert_error(client.get("/trips/not;valid"), 422, "validation_error")
    assert repo.calls == []


def test_trip_influx_down_503(client, repo):
    repo.fail = True
    assert_error(client.get(f"/trips/{TRIP}"), 503, "upstream_unavailable")


# ---- framework-level behaviour ---------------------------------------------


def test_unknown_route_uses_error_format(client):
    assert_error(client.get("/nope"), 404, "not_found")


def test_wrong_method_405(client):
    assert_error(client.post("/health"), 405, "method_not_allowed")


def test_openapi_lists_all_endpoints(client):
    paths = client.get("/openapi.json").json()["paths"]
    assert {"/health", "/drivers/{driver_id}/risk", "/drivers/top-risk", "/trips/{trip_id}"} <= set(paths)
    assert client.get("/docs").status_code == 200
