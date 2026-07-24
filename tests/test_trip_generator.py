"""
test_trip_generator.py
------------------------
Unit tests for simulator/trip_generator.py. Since the generator is built on
random.gauss/random.uniform draws, these tests don't assert exact values --
they assert the INVARIANTS that must hold no matter what the random draws
were: value ranges, internal consistency (all events in one trip share a
trip_id), and that profile assignment actually produces all three profiles
over enough samples.
"""

import random

from trip_generator import (
    DRIVER_PROFILES,
    assign_driver_profile,
    generate_trip_events,
    stream_events,
)


def test_assign_driver_profile_returns_valid_profile():
    random.seed(1)
    for _ in range(50):
        profile = assign_driver_profile()
        assert profile in DRIVER_PROFILES


def test_assign_driver_profile_produces_all_profiles_over_many_samples():
    random.seed(2)
    seen = {assign_driver_profile() for _ in range(500)}
    assert seen == set(DRIVER_PROFILES)


def test_generate_trip_events_returns_requested_count():
    random.seed(3)
    events = generate_trip_events("driver_test", "moderate", num_events=15)
    assert len(events) == 15


def test_generate_trip_events_shares_driver_and_trip_id():
    random.seed(4)
    events = generate_trip_events("driver_test", "aggressive", num_events=10)

    driver_ids = {e["driver_id"] for e in events}
    trip_ids = {e["trip_id"] for e in events}
    assert driver_ids == {"driver_test"}
    assert len(trip_ids) == 1, "all events in one generated trip must share a single trip_id"


def test_generate_trip_events_value_ranges():
    random.seed(5)
    events = generate_trip_events("driver_test", "aggressive", num_events=40)

    for event in events:
        assert 0.0 <= event["speed_kmh"] <= 160.0
        assert 0.0 <= event["braking_force_g"] <= 1.0
        assert 0.0 <= event["acceleration_g"] <= 0.8
        assert isinstance(event["is_night"], bool)
        assert event["driver_profile"] == "aggressive"
        assert isinstance(event["latitude"], float)
        assert isinstance(event["longitude"], float)


def test_stream_events_yields_expected_minimum_event_count():
    random.seed(6)
    events = list(stream_events(num_drivers=3, trips_per_driver=2))

    # generate_trip_events always produces at least 20 events per trip (see
    # the random.randint(20, 45) call in stream_events), so 3 drivers x 2
    # trips guarantees at least 3*2*20 = 120 events.
    assert len(events) >= 120

    driver_ids = {e["driver_id"] for e in events}
    assert len(driver_ids) == 3
