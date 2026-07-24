"""
kafka_producer.py
------------------
Bridges the pure-Python trip simulator (trip_generator.py) to Kafka. This is
the only file in the simulator package that knows about Kafka -- keeping it
separate from trip_generator.py means the generator can be unit-tested and
validated (see validation check #2) without a running Kafka broker.

Publishes each simulated vehicle event as JSON to the `vehicle.trip.events`
topic, keyed by driver_id so all of one driver's events land on the same
partition (this matters downstream: Spark's windowed aggregation by
driver_id/trip_id relies on ordering within a partition being preserved).
"""

import argparse
import json
import logging
import sys
import time
from typing import Optional

from confluent_kafka import Producer

from trip_generator import stream_events

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)
logger = logging.getLogger("kafka_producer")

TOPIC = "vehicle.trip.events"

# Retry policy for the initial connection: Kafka/Zookeeper can take a while
# to become ready inside Docker Compose, so the producer shouldn't crash on
# the first attempt -- it should wait and retry a few times before giving up.
CONNECT_RETRIES = 3
CONNECT_RETRY_PAUSE_SECONDS = 5


def build_producer(bootstrap_servers: str) -> Producer:
    """
    Create a confluent_kafka Producer with retry logic around the initial
    metadata fetch. confluent_kafka's Producer() constructor itself doesn't
    block on broker availability, so we force an early connectivity check by
    requesting cluster metadata -- that's the operation that actually fails
    if the broker isn't reachable yet.
    """
    last_error: Optional[Exception] = None

    for attempt in range(1, CONNECT_RETRIES + 1):
        try:
            producer = Producer(
                {
                    "bootstrap.servers": bootstrap_servers,
                    "client.id": "telematics-trip-simulator",
                    # Small linger to batch events efficiently without adding
                    # noticeable latency to the simulated "real-time" stream.
                    "linger.ms": 50,
                }
            )
            # list_topics() forces a metadata round-trip to the broker,
            # which is what actually proves connectivity.
            producer.list_topics(timeout=10)
            logger.info("Connected to Kafka at %s (attempt %d/%d)", bootstrap_servers, attempt, CONNECT_RETRIES)
            return producer
        except Exception as exc:  # noqa: BLE001 - we want to retry on any connection error
            last_error = exc
            logger.warning("Kafka connection attempt %d/%d failed: %s", attempt, CONNECT_RETRIES, exc)
            if attempt < CONNECT_RETRIES:
                time.sleep(CONNECT_RETRY_PAUSE_SECONDS)

    logger.error("Could not connect to Kafka after %d attempts", CONNECT_RETRIES)
    raise ConnectionError(f"Failed to connect to Kafka at {bootstrap_servers}") from last_error


def delivery_report(err, msg) -> None:
    """
    Callback fired by the Kafka client (asynchronously, via producer.poll())
    once a produced message has either been delivered or permanently failed.
    """
    if err is not None:
        logger.error("Delivery failed for driver=%s: %s", msg.key(), err)
    else:
        logger.debug(
            "Delivered event to %s [partition %d] offset %d",
            msg.topic(),
            msg.partition(),
            msg.offset(),
        )


def produce_events(
    producer: Producer, bootstrap_servers: str, num_drivers: int, trips_per_driver: int, speed: float
) -> None:
    """
    Stream simulated events into Kafka. `speed` is the delay in seconds
    inserted between events, letting the operator slow the simulation down
    to a human-watchable pace or speed it up for load testing.
    """
    sent = 0
    for event in stream_events(num_drivers=num_drivers, trips_per_driver=trips_per_driver):
        payload = json.dumps(event).encode("utf-8")
        key = event["driver_id"].encode("utf-8")

        try:
            producer.produce(
                topic=TOPIC,
                key=key,
                value=payload,
                callback=delivery_report,
            )
        except BufferError:
            # Local producer queue is full; give librdkafka time to drain it.
            logger.warning("Producer queue full, flushing before retrying")
            producer.flush()
            producer.produce(topic=TOPIC, key=key, value=payload, callback=delivery_report)

        # Serve delivery report callbacks without blocking for long.
        producer.poll(0)
        sent += 1

        if sent % 25 == 0:
            logger.info("Produced %d events so far (topic=%s)", sent, TOPIC)

        if speed > 0:
            time.sleep(speed)

    # Block until all outstanding messages are delivered or fail.
    logger.info("Flushing remaining messages...")
    remaining = producer.flush(timeout=30)
    if remaining > 0:
        logger.warning("%d messages were not delivered before flush timeout", remaining)

    logger.info("Done. Total events produced: %d", sent)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Simulate vehicle trips and publish them to Kafka.")
    parser.add_argument(
        "--bootstrap", default="localhost:29092", help="Kafka bootstrap servers (default: localhost:29092)"
    )
    parser.add_argument("--drivers", type=int, default=5, help="Number of simulated drivers (default: 5)")
    parser.add_argument("--trips", type=int, default=2, help="Trips per driver (default: 2)")
    parser.add_argument("--speed", type=float, default=0.1, help="Delay in seconds between events (default: 0.1)")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    logger.info(
        "Starting simulator: bootstrap=%s drivers=%d trips=%d speed=%.2fs",
        args.bootstrap,
        args.drivers,
        args.trips,
        args.speed,
    )

    producer = build_producer(args.bootstrap)
    produce_events(producer, args.bootstrap, args.drivers, args.trips, args.speed)


if __name__ == "__main__":
    try:
        main()
    except ConnectionError as exc:
        logger.error("%s", exc)
        sys.exit(1)
    except KeyboardInterrupt:
        logger.info("Interrupted by user, exiting.")
        sys.exit(0)
