"""
benchmarks/kafka_throughput.py
------------------------------
Measures raw Kafka produce and consume throughput (events/second) for the
project's event payloads, on a dedicated topic so the running Spark job does
not see the benchmark traffic.

- Events come from simulator/trip_generator.stream_events() and are
  JSON-encoded BEFORE the timer starts, so the number isolates Kafka
  transport (generation cost is measured separately by running
  simulator/kafka_producer.py with --speed 0).
- Producer config matches simulator/kafka_producer.py (linger.ms=50).
- Produce rate  = events / (time from first produce() call until flush()
  returns with every delivery acknowledged).
- Consume rate  = events / (time from first message received until the
  last one is received), using assign() from the beginning of every
  partition so consumer-group rebalance time is not counted.

Usage:
    python benchmarks/kafka_throughput.py --bootstrap localhost:29092 --drivers 200 --trips 8
Prints one JSON object with the results.
"""

import argparse
import json
import os
import sys
import time
import uuid

from confluent_kafka import Consumer, Producer, TopicPartition
from confluent_kafka.admin import AdminClient, NewTopic

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "simulator"))
from trip_generator import stream_events  # noqa: E402


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--bootstrap", default="localhost:29092")
    ap.add_argument("--drivers", type=int, default=200)
    ap.add_argument("--trips", type=int, default=8)
    ap.add_argument("--partitions", type=int, default=3)
    args = ap.parse_args()

    topic = f"bench.throughput.{uuid.uuid4().hex[:8]}"
    admin = AdminClient({"bootstrap.servers": args.bootstrap})
    admin.create_topics([NewTopic(topic, num_partitions=args.partitions, replication_factor=1)])[topic].result()

    events = [(e["driver_id"].encode(), json.dumps(e).encode()) for e in stream_events(args.drivers, args.trips)]
    n = len(events)
    payload_bytes = sum(len(v) for _, v in events)

    acked = {"ok": 0, "err": 0}

    def cb(err, _msg):
        acked["err" if err else "ok"] += 1

    producer = Producer({"bootstrap.servers": args.bootstrap, "linger.ms": 50})
    producer.list_topics(timeout=10)
    t0 = time.perf_counter()
    for key, value in events:
        while True:
            try:
                producer.produce(topic, key=key, value=value, callback=cb)
                break
            except BufferError:
                producer.poll(0.05)
        producer.poll(0)
    remaining = producer.flush(60)
    produce_s = time.perf_counter() - t0

    consumer = Consumer(
        {"bootstrap.servers": args.bootstrap, "group.id": f"bench-{uuid.uuid4().hex}", "enable.auto.commit": False}
    )
    consumer.assign([TopicPartition(topic, p, 0) for p in range(args.partitions)])  # offset 0 = beginning
    received = 0
    first = last = None
    deadline = time.time() + 120
    while received < n and time.time() < deadline:
        msgs = consumer.consume(num_messages=1000, timeout=1.0)
        for m in msgs:
            if m.error():
                continue
            if first is None:
                first = time.perf_counter()
            received += 1
        if msgs:
            last = time.perf_counter()
    consumer.close()
    consume_s = (last - first) if first and last else None

    admin.delete_topics([topic])

    print(
        json.dumps(
            {
                "topic": topic,
                "events": n,
                "payload_bytes": payload_bytes,
                "acked_ok": acked["ok"],
                "acked_err": acked["err"],
                "unflushed": remaining,
                "produce_seconds": round(produce_s, 4),
                "produce_events_per_sec": round(n / produce_s, 1),
                "consumed": received,
                "consume_seconds": round(consume_s, 4) if consume_s else None,
                "consume_events_per_sec": round(received / consume_s, 1) if consume_s else None,
            }
        )
    )


if __name__ == "__main__":
    main()
