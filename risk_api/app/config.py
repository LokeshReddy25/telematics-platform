"""
config.py: settings for the Risk Score API, read from environment variables.

There is deliberately NO default for INFLUXDB_TOKEN. The API refuses to
start without one, so a token can never be baked into the code or image.
"""

import os
import re
from dataclasses import dataclass
from typing import Mapping, Optional

# Flux relative duration, e.g. -30d, -12h, -90m. Interpolated into queries,
# so it is validated here rather than trusted.
_LOOKBACK_RE = re.compile(r"^-\d+(s|m|h|d|w)$")
_NAME_RE = re.compile(r"^[A-Za-z0-9_.\-]{1,128}$")


class ConfigError(RuntimeError):
    """Raised when required configuration is missing or invalid."""


@dataclass(frozen=True)
class Settings:
    influxdb_url: str
    influxdb_token: str
    influxdb_org: str
    influxdb_bucket: str
    measurement: str = "driver_risk"
    lookback: str = "-30d"
    query_timeout_ms: int = 10_000

    @classmethod
    def from_env(cls, env: Optional[Mapping[str, str]] = None) -> "Settings":
        env = os.environ if env is None else env

        token = env.get("INFLUXDB_TOKEN", "").strip()
        if not token:
            raise ConfigError("INFLUXDB_TOKEN is not set. The Risk API needs a read token for InfluxDB.")

        settings = cls(
            influxdb_url=env.get("INFLUXDB_URL", "http://localhost:8086"),
            influxdb_token=token,
            influxdb_org=env.get("INFLUXDB_ORG", "telematics-org"),
            influxdb_bucket=env.get("INFLUXDB_BUCKET", "telematics"),
            measurement=env.get("RISK_API_MEASUREMENT", "driver_risk"),
            lookback=env.get("RISK_API_LOOKBACK", "-30d"),
            query_timeout_ms=_parse_int(env.get("RISK_API_QUERY_TIMEOUT_MS", "10000"), "RISK_API_QUERY_TIMEOUT_MS"),
        )
        settings.validate()
        return settings

    def validate(self) -> None:
        if not _LOOKBACK_RE.match(self.lookback):
            raise ConfigError(f"RISK_API_LOOKBACK must look like -30d or -12h, got {self.lookback!r}")
        for name, value in (("INFLUXDB_BUCKET", self.influxdb_bucket), ("RISK_API_MEASUREMENT", self.measurement)):
            if not _NAME_RE.match(value):
                raise ConfigError(f"{name} contains unsupported characters: {value!r}")
        if self.query_timeout_ms <= 0:
            raise ConfigError("RISK_API_QUERY_TIMEOUT_MS must be positive")


def _parse_int(raw: str, name: str) -> int:
    try:
        return int(raw)
    except ValueError as exc:
        raise ConfigError(f"{name} must be an integer, got {raw!r}") from exc
