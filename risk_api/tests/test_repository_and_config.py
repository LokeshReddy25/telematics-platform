"""Unit tests for RiskRepository (InfluxDB client mocked) and Settings."""

from unittest.mock import MagicMock

import pytest

from app.config import ConfigError, Settings
from app.repository import RepositoryError, RiskRepository

SETTINGS = Settings(
    influxdb_url="http://influx:8086", influxdb_token="t", influxdb_org="org", influxdb_bucket="telematics"
)


def make_repo(tables=None, exc=None):
    client = MagicMock()
    query = client.query_api.return_value.query
    if exc:
        query.side_effect = exc
    else:
        query.return_value = tables or []
    return RiskRepository(SETTINGS, client=client), client, query


def fake_table(*values):
    table = MagicMock()
    table.records = [MagicMock(values=v) for v in values]
    return table


# ---- RiskRepository ---------------------------------------------------------


def test_driver_history_builds_filtered_pivot_query_and_flattens_records():
    repo, _, query = make_repo([fake_table({"a": 1}), fake_table({"a": 2}, {"a": 3})])
    assert repo.driver_history("driver_0001") == [{"a": 1}, {"a": 2}, {"a": 3}]
    flux = query.call_args.args[0]
    assert 'from(bucket: "telematics")' in flux
    assert "range(start: -30d)" in flux
    assert 'r._measurement == "driver_risk"' in flux
    assert 'r.driver_id == "driver_0001"' in flux
    assert "pivot(" in flux
    assert query.call_args.kwargs == {"org": "org"}


def test_trip_history_filters_on_trip_id():
    repo, _, query = make_repo()
    assert repo.trip_history("abc-123") == []
    assert 'r.trip_id == "abc-123"' in query.call_args.args[0]


def test_top_risk_query_takes_latest_per_driver_and_limits():
    repo, _, query = make_repo()
    repo.top_risk(7)
    flux = query.call_args.args[0]
    assert 'group(columns: ["driver_id"])' in flux
    assert "last()" in flux
    assert 'sort(columns: ["_value"], desc: true)' in flux
    assert "limit(n: 7)" in flux


@pytest.mark.parametrize("bad", ['x") or true or ("', "", "a" * 65, "has space", None])
def test_repository_rejects_unsafe_ids(bad):
    repo, _, query = make_repo()
    with pytest.raises(ValueError):
        repo.driver_history(bad)
    with pytest.raises(ValueError):
        repo.trip_history(bad)
    query.assert_not_called()


@pytest.mark.parametrize("bad", [0, 101, "5", None])
def test_top_risk_rejects_bad_limit(bad):
    repo, _, _ = make_repo()
    with pytest.raises(ValueError):
        repo.top_risk(bad)


def test_query_failure_becomes_repository_error():
    repo, _, _ = make_repo(exc=ConnectionError("refused"))
    with pytest.raises(RepositoryError, match="refused"):
        repo.driver_history("driver_0001")


def test_ping_true_false_and_exception():
    repo, client, _ = make_repo()
    client.ping.return_value = True
    assert repo.ping() is True
    client.ping.return_value = False
    assert repo.ping() is False
    client.ping.side_effect = OSError("down")
    assert repo.ping() is False


def test_close_closes_client():
    repo, client, _ = make_repo()
    repo.close()
    client.close.assert_called_once()


# ---- Settings ---------------------------------------------------------------


def test_settings_require_token():
    with pytest.raises(ConfigError, match="INFLUXDB_TOKEN"):
        Settings.from_env({})
    with pytest.raises(ConfigError):
        Settings.from_env({"INFLUXDB_TOKEN": "   "})


def test_settings_defaults_and_overrides():
    s = Settings.from_env({"INFLUXDB_TOKEN": "abc"})
    assert (s.influxdb_url, s.influxdb_org, s.influxdb_bucket) == (
        "http://localhost:8086",
        "telematics-org",
        "telematics",
    )
    assert (s.measurement, s.lookback, s.query_timeout_ms) == ("driver_risk", "-30d", 10000)

    s = Settings.from_env(
        {
            "INFLUXDB_TOKEN": "abc",
            "INFLUXDB_URL": "http://x:1",
            "INFLUXDB_BUCKET": "b2",
            "RISK_API_LOOKBACK": "-12h",
            "RISK_API_QUERY_TIMEOUT_MS": "500",
        }
    )
    assert (s.influxdb_url, s.influxdb_bucket, s.lookback, s.query_timeout_ms) == ("http://x:1", "b2", "-12h", 500)


@pytest.mark.parametrize(
    "env",
    [
        {"RISK_API_LOOKBACK": "30d"},
        {"RISK_API_LOOKBACK": "-1d) |> drop()"},
        {"INFLUXDB_BUCKET": 'bad"bucket'},
        {"RISK_API_QUERY_TIMEOUT_MS": "soon"},
        {"RISK_API_QUERY_TIMEOUT_MS": "0"},
    ],
)
def test_settings_reject_invalid_values(env):
    with pytest.raises(ConfigError):
        Settings.from_env({"INFLUXDB_TOKEN": "abc", **env})


def test_app_startup_fails_fast_without_token(monkeypatch):
    from fastapi.testclient import TestClient

    from app.main import create_app

    monkeypatch.delenv("INFLUXDB_TOKEN", raising=False)
    with pytest.raises(ConfigError):
        with TestClient(create_app()):
            pass
