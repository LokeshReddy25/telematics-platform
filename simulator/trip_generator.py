"""
trip_generator.py
------------------
Simulates realistic connected-vehicle telematics events for the insurance
risk-scoring pipeline. This module has NO Kafka dependency on purpose -- it
is a pure data-generation layer that `kafka_producer.py` imports and streams
to Kafka. Keeping generation and transport separate makes the generator
testable on its own (see validation check #2: `python trip_generator.py`).

Why simulate at all: we don't have a fleet of real connected cars, but the
whole downstream pipeline (Kafka -> Spark -> ML -> InfluxDB -> Grafana) needs
a continuous, realistic-looking stream of trip telemetry to prove itself out.
"""

import random
import math
import uuid
from datetime import datetime, timedelta
from typing import Generator, Dict, Any, List

# Base GPS coordinates: Frankfurt, Germany. All simulated trips drift from
# here so the dashboard map (if added later) looks like a real regional fleet.
BASE_LAT = 50.1109
BASE_LON = 8.6821

# Driver behavioural profiles and their population weights. These weights
# are used both to assign a profile per driver AND to shape how that
# driver's speed/braking/acceleration are generated below.
DRIVER_PROFILES = ["safe", "moderate", "aggressive"]
DRIVER_PROFILE_WEIGHTS = [0.40, 0.40, 0.20]

# Per-profile tuning parameters. These are hand-picked so that the resulting
# feature distributions (avg speed, harsh brake counts, etc.) separate
# cleanly into the risk bands used later by ml/train_model.py.
PROFILE_PARAMS = {
    "safe": {
        "target_speed_range": (30, 90),  # km/h cruising band
        "speed_volatility": 3.0,  # gaussian noise stddev per tick
        "harsh_brake_prob": 0.02,  # chance of a harsh braking event per tick
        "harsh_accel_prob": 0.02,
        "max_braking_force": 0.45,
        "max_acceleration": 0.35,
    },
    "moderate": {
        "target_speed_range": (40, 120),
        "speed_volatility": 6.0,
        "harsh_brake_prob": 0.06,
        "harsh_accel_prob": 0.06,
        "max_braking_force": 0.70,
        "max_acceleration": 0.55,
    },
    "aggressive": {
        "target_speed_range": (60, 160),
        "speed_volatility": 11.0,
        "harsh_brake_prob": 0.15,
        "harsh_accel_prob": 0.15,
        "max_braking_force": 1.00,
        "max_acceleration": 0.80,
    },
}

# A harsh brake/accel event (g-force above this) is what insurers actually
# care about -- it's the signal the ML model keys on.
HARSH_BRAKE_THRESHOLD_G = 0.4
HARSH_ACCEL_THRESHOLD_G = 0.3


def assign_driver_profile() -> str:
    """Weighted-random assignment of a driver behavioural profile."""
    return random.choices(DRIVER_PROFILES, weights=DRIVER_PROFILE_WEIGHTS, k=1)[0]


def _is_night(ts: datetime) -> bool:
    """Night driving is defined as 22:00-05:59, a common insurance heuristic
    since night trips statistically carry higher accident risk."""
    hour = ts.hour
    return hour >= 22 or hour < 6


def _next_speed(current_speed: float, profile: str) -> float:
    """
    Evolve speed smoothly using Gaussian noise around the current value,
    biased gently toward the profile's target range. This avoids the
    unrealistic "teleporting" speed you'd get from picking a fresh random
    speed every tick -- real vehicles accelerate/decelerate gradually.
    """
    params = PROFILE_PARAMS[profile]
    low, high = params["target_speed_range"]
    target = (low + high) / 2.0

    # Pull gently toward the profile's target speed (mean reversion) plus
    # per-tick Gaussian jitter for natural variance.
    pull = (target - current_speed) * 0.05
    noise = random.gauss(0, params["speed_volatility"])
    new_speed = current_speed + pull + noise

    # Clamp to the simulator's overall valid speed band.
    return max(0.0, min(160.0, new_speed))


def _braking_force_for_speed(speed_kmh: float, profile: str) -> float:
    """
    Braking force is correlated with speed: higher current speed means a
    braking event (when it happens) tends to be harder, because there's more
    kinetic energy to shed. We scale a profile-capped random draw by how
    fast the vehicle is currently going relative to its own top band.
    """
    params = PROFILE_PARAMS[profile]
    speed_factor = min(1.0, speed_kmh / 160.0)
    base = random.uniform(0.0, params["max_braking_force"])
    # Blend: 60% profile-random, 40% speed-correlated scaling.
    correlated = base * (0.6 + 0.4 * speed_factor)
    return round(max(0.0, min(1.0, correlated)), 3)


def _acceleration_for_speed(speed_kmh: float, profile: str) -> float:
    """Acceleration g-force draw, capped per profile. Lower-speed regimes
    allow relatively higher accel (pulling away from a stop), so we don't
    correlate this one with speed as strongly as braking."""
    params = PROFILE_PARAMS[profile]
    return round(random.uniform(0.0, params["max_acceleration"]), 3)


def _drift_gps(lat: float, lon: float, speed_kmh: float, heading_rad: float) -> (float, float):
    """
    Move the GPS point a small realistic distance based on current speed.
    Distance covered per ~2s tick = speed(km/h) * (tick_seconds / 3600).
    We convert km to degrees using an approximate 111km-per-degree constant,
    which is accurate enough for a local simulation (not for navigation).
    """
    tick_seconds = 2.0
    distance_km = speed_kmh * (tick_seconds / 3600.0)
    delta_deg = distance_km / 111.0
    new_lat = lat + delta_deg * math.cos(heading_rad)
    new_lon = lon + delta_deg * math.sin(heading_rad)
    return new_lat, new_lon


def generate_trip_events(driver_id: str, profile: str, num_events: int = 30) -> List[Dict[str, Any]]:
    """
    Generate a single trip's worth of events for one driver.

    Returns a list of event dicts (rather than yielding) because a "trip" is
    a bounded, known-size unit of work -- callers that want a continuous
    multi-trip stream should use `stream_events()` below.
    """
    trip_id = str(uuid.uuid4())
    events = []

    # Start somewhere near Frankfurt with a small random offset so multiple
    # drivers don't all start at the exact same point.
    lat = BASE_LAT + random.uniform(-0.05, 0.05)
    lon = BASE_LON + random.uniform(-0.05, 0.05)
    heading_rad = random.uniform(0, 2 * math.pi)

    params = PROFILE_PARAMS[profile]
    speed = random.uniform(*params["target_speed_range"])

    # Trip starts at a random point in the last 24h so "is_night" varies
    # realistically across generated trips.
    trip_start = datetime.now() - timedelta(seconds=random.randint(0, 86400))

    for i in range(num_events):
        ts = trip_start + timedelta(seconds=i * 2)

        speed = _next_speed(speed, profile)
        braking_force = _braking_force_for_speed(speed, profile)
        acceleration = _acceleration_for_speed(speed, profile)

        # Occasionally inject an explicit harsh event on top of the smooth
        # correlated value, so the ML features (harsh_brake_count etc.) have
        # a clear, profile-weighted signal to learn from.
        if random.random() < params["harsh_brake_prob"]:
            braking_force = round(random.uniform(HARSH_BRAKE_THRESHOLD_G, params["max_braking_force"]), 3)
        if random.random() < params["harsh_accel_prob"]:
            acceleration = round(random.uniform(HARSH_ACCEL_THRESHOLD_G, params["max_acceleration"]), 3)

        # Small random heading drift so the GPS path curves instead of
        # travelling in a perfectly straight line.
        heading_rad += random.uniform(-0.15, 0.15)
        lat, lon = _drift_gps(lat, lon, speed, heading_rad)

        events.append(
            {
                "driver_id": driver_id,
                "trip_id": trip_id,
                "timestamp": ts.isoformat(),
                "speed_kmh": round(speed, 2),
                "braking_force_g": braking_force,
                "acceleration_g": acceleration,
                "latitude": round(lat, 6),
                "longitude": round(lon, 6),
                "is_night": _is_night(ts),
                "driver_profile": profile,
            }
        )

    return events


def stream_events(num_drivers: int = 5, trips_per_driver: int = 2) -> Generator[Dict[str, Any], None, None]:
    """
    Top-level generator used by kafka_producer.py (and the validation check
    in this file's __main__ block). Yields individual event dicts one at a
    time across `num_drivers` drivers, each driving `trips_per_driver` trips.

    Each driver keeps the SAME profile across all of their trips, since
    driving behaviour is a property of the person, not the individual trip.
    """
    drivers = [{"driver_id": f"driver_{i:04d}", "profile": assign_driver_profile()} for i in range(num_drivers)]

    for driver in drivers:
        for _ in range(trips_per_driver):
            num_events = random.randint(20, 45)  # trips vary in length
            trip_events = generate_trip_events(driver["driver_id"], driver["profile"], num_events)
            for event in trip_events:
                yield event


if __name__ == "__main__":
    # Validation entry point (see project validation check #2): generate a
    # small stream and print it so we can eyeball realism and confirm the
    # generator runs cleanly end-to-end with no Kafka dependency required.
    count = 0
    for evt in stream_events(num_drivers=2, trips_per_driver=1):
        print(evt)
        count += 1

    print(f"\nGenerated {count} events across 2 drivers.")
    assert count >= 10, "Expected at least 10 simulated events"
    print("trip_generator.py self-test passed.")
