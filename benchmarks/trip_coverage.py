"""
benchmarks/trip_coverage.py
---------------------------
What fraction of produced trips end up with a risk score in InfluxDB?

Reads every message on `vehicle.trip.events` from the beginning (Kafka
CreateTime = when the producer sent it), keeps the trips whose FIRST event
was produced after --since (use the time Spark's query started, since it
reads from the latest offset), and checks which of those trip_ids have at
least one driver_risk point in InfluxDB.

Usage:
  python benchmarks/trip_coverage.py --since 2026-11-26T06:15:30Z --until 2026-11-26T06:25:00Z
"""

import argparse
import json
import os
import uuid
from datetime import datetime

from confluent_kafka import Consumer, TopicPartition
from influxdb_client import InfluxDBClient


def ts(s):
    return datetime.fromisoformat(s.replace("Z", "+00:00")).timestamp()


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--bootstrap", default="localhost:29092")
    ap.add_argument("--since", required=True, help="ISO time; trips whose first event was produced before are ignored")
    ap.add_argument("--until", required=True, help="ISO time; trips whose last event was produced after are ignored")
    ap.add_argument("--influx-url", default="http://localhost:8086")
    ap.add_argument("--influx-token", default=os.environ.get("INFLUXDB_TOKEN", "telematics-dev-token"))
    ap.add_argument("--influx-org", default="telematics-org")
    ap.add_argument("--influx-bucket", default="telematics")
    args = ap.parse_args()
    since, until = ts(args.since), ts(args.until)

    c = Consumer({"bootstrap.servers": args.bootstrap, "group.id": f"cov-{uuid.uuid4().hex}"})
    md = c.list_topics("vehicle.trip.events", timeout=10).topics["vehicle.trip.events"]
    parts = [TopicPartition("vehicle.trip.events", p, 0) for p in md.partitions]
    ends = {p.partition: c.get_watermark_offsets(p)[1] for p in parts}
    c.assign(parts)
    trips, seen = {}, {p: 0 for p in ends}
    while any(seen[p] < ends[p] for p in ends):
        for m in c.consume(500, timeout=2.0):
            if m.error():
                continue
            seen[m.partition()] = m.offset() + 1
            ev = json.loads(m.value())
            t = m.timestamp()[1] / 1000.0
            tr = trips.setdefault(ev["trip_id"], {"first": t, "last": t, "event_ts": ev["timestamp"]})
            tr["first"], tr["last"] = min(tr["first"], t), max(tr["last"], t)
    c.close()

    window = {k: v for k, v in trips.items() if v["first"] >= since and v["last"] <= until}

    q = InfluxDBClient(url=args.influx_url, token=args.influx_token, org=args.influx_org).query_api()
    flux = (
        f'from(bucket: "{args.influx_bucket}") |> range(start: -24h) '
        '|> filter(fn: (r) => r._measurement == "driver_risk" and r._field == "risk_score") '
        '|> keep(columns: ["trip_id"]) |> distinct(column: "trip_id")'
    )
    scored = {r.get_value() for t in q.query(flux) for r in t.records}

    hit = [k for k in window if k in scored]
    print(
        json.dumps(
            {
                "since": args.since,
                "until": args.until,
                "trips_in_topic_total": len(trips),
                "trips_produced_in_window": len(window),
                "trips_with_score": len(hit),
                "trips_without_score": len(window) - len(hit),
            }
        )
    )


if __name__ == "__main__":
    main()
