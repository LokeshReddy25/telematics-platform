"""
repository.py: read-only access to the risk scores the Spark job writes.

Data layout (from storage/influxdb_writer.py):
  measurement: driver_risk
  tags:        driver_id, trip_id, risk_category
  fields:      risk_score, harsh_brake_count, harsh_accel_count,
               max_speed, avg_speed, night_driving_ratio
  time:        scored_at

InfluxDB OSS 2.x does not support parameterised Flux queries, so IDs are
interpolated into the query text. They are checked against ID_PATTERN
here (and in the route definitions) so a value can never break out of its
string literal.
"""

import re
from typing import Any, Dict, List, Optional

from influxdb_client import InfluxDBClient

from .config import Settings

ID_PATTERN = r"^[A-Za-z0-9_\-]{1,64}$"
_ID_RE = re.compile(ID_PATTERN)

_PIVOT = '  |> pivot(rowKey: ["_time"], columnKey: ["_field"], valueColumn: "_value")\n'


class RepositoryError(Exception):
    """InfluxDB could not be reached or the query failed."""


def _check_id(value: str, what: str) -> str:
    if not isinstance(value, str) or not _ID_RE.match(value):
        raise ValueError(f"invalid {what}: {value!r}")
    return value


class RiskRepository:
    def __init__(self, settings: Settings, client: Optional[InfluxDBClient] = None):
        self._settings = settings
        self._client = client or InfluxDBClient(
            url=settings.influxdb_url,
            token=settings.influxdb_token,
            org=settings.influxdb_org,
            timeout=settings.query_timeout_ms,
        )

    def close(self) -> None:
        self._client.close()

    def ping(self) -> bool:
        try:
            return bool(self._client.ping())
        except Exception:  # noqa: BLE001 - any failure means "unreachable"
            return False

    def _base(self) -> str:
        s = self._settings
        return (
            f'from(bucket: "{s.influxdb_bucket}")\n'
            f"  |> range(start: {s.lookback})\n"
            f'  |> filter(fn: (r) => r._measurement == "{s.measurement}")\n'
        )

    def _query(self, flux: str) -> List[Dict[str, Any]]:
        try:
            tables = self._client.query_api().query(flux, org=self._settings.influxdb_org)
        except Exception as exc:  # noqa: BLE001 - surface as one error type
            raise RepositoryError(f"InfluxDB query failed: {exc}") from exc
        return [dict(record.values) for table in tables for record in table.records]

    def driver_history(self, driver_id: str) -> List[Dict[str, Any]]:
        """All score records for one driver, oldest first."""
        _check_id(driver_id, "driver_id")
        flux = (
            self._base()
            + f'  |> filter(fn: (r) => r.driver_id == "{driver_id}")\n'
            + _PIVOT
            + '  |> group()\n  |> sort(columns: ["_time"])\n'
        )
        return self._query(flux)

    def trip_history(self, trip_id: str) -> List[Dict[str, Any]]:
        """All score records for one trip, oldest first."""
        _check_id(trip_id, "trip_id")
        flux = (
            self._base()
            + f'  |> filter(fn: (r) => r.trip_id == "{trip_id}")\n'
            + _PIVOT
            + '  |> group()\n  |> sort(columns: ["_time"])\n'
        )
        return self._query(flux)

    def top_risk(self, limit: int) -> List[Dict[str, Any]]:
        """Each driver's most recent risk_score, highest first, at most `limit` rows."""
        if not isinstance(limit, int) or not 1 <= limit <= 100:
            raise ValueError(f"limit must be between 1 and 100, got {limit!r}")
        flux = (
            self._base()
            + '  |> filter(fn: (r) => r._field == "risk_score")\n'
            + '  |> group(columns: ["driver_id"])\n'
            + '  |> sort(columns: ["_time"])\n'
            + "  |> last()\n"
            + "  |> group()\n"
            + '  |> sort(columns: ["_value"], desc: true)\n'
            + f"  |> limit(n: {limit})\n"
        )
        return self._query(flux)
