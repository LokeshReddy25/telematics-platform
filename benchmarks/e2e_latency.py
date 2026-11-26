"""
benchmarks/e2e_latency.py
-------------------------
End-to-end latency: event produced to Kafka -> risk score written to InfluxDB.

What it does:
  1. Generates trips with simulator/trip_generator.stream_events() and sends
     them to the real pipeline topic `vehicle.trip.events`, with the same
     producer config (linger.ms=50) and per-event delay as the README/RUNBOOK
     demo (5 drivers, 3 trips, 0.3 s between events).
  2. Records the host wall-clock time at which Kafka ACKNOWLEDGED each event
     (delivery callback).
  3. Polls InfluxDB (measurement driver_risk, field risk_score) until every
     trip has a score written AFTER its last event could have been read, or
     until --wait seconds after the last event.
  4. Reads the Spark micro-batch start times written by
     benchmarks/spark_with_progress.py (--progress-file).

Latency definition (per event):
  first InfluxDB risk_score point for the event's trip_id that was written by
  a micro-batch which STARTED after the event was acked, minus the ack time.
  A batch reads Kafka offsets when it starts, so that is the first batch that
  can possibly include the event. (A naive "first score after the event"
  would be wrong: Spark's update output mode re-scores partial windows every
  trigger, so a score can be written before later events of the same trip.)

  InfluxDB point times are `scored_at`, set by risk_scorer.score_trip() in
  the Spark container just before the synchronous write. Container and
  batch-start times share the container clock; they are converted to host
  time with --clock-offset-ms (container - host), measured by
  benchmarks/clock_offset.py.

Also reported per trip: "trip complete" latency = same rule applied to the
trip's LAST event.

Prints one JSON object.
"""

import argparse
import bisect
import json
import os
import statistics
import sys
import time
from datetime import datetime, timezone

from confluent_kafka import Producer
from influxdb_client import InfluxDBClient

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "simulator"))
from trip_generator import stream_events  # noqa: E402

TOPIC = "vehicle.trip.events"


def pct(values, p):
    """Nearest-rank percentile (no interpolation): always an observed value."""
    s = sorted(values)
    k = max(0, min(len(s) - 1, -(-p * len(s) // 100) - 1))
    return s[k]


def summary(v):
    if not v:
        return None
    return {
        "n": len(v),
        "median_s": round(statistics.median(v), 3),
        "p95_s": round(pct(v, 95), 3),
        "min_s": round(min(v), 3),
        "max_s": round(max(v), 3),
    }


def load_batch_starts(path):
    """Spark batch start times (epoch seconds, container clock), sorted."""
    starts = []
    with open(path, encoding="utf-8") as f:
        for line in f:
            if line.strip():
                ts = json.loads(line)["timestamp"].replace("Z", "+00:00")
                starts.append(datetime.fromisoformat(ts).timestamp())
    return sorted(starts)


def batch_start_for(point_time, starts):
    """Start time of the batch that wrote a point = latest batch start <= point time."""
    i = bisect.bisect_right(starts, point_time) - 1
    return starts[i] if i >= 0 else None


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--bootstrap", default="localhost:29092")
    ap.add_argument("--drivers", type=int, default=5)
    ap.add_argument("--trips", type=int, default=3)
    ap.add_argument("--speed", type=float, default=0.3)
    ap.add_argument("--wait", type=int, default=240)
    ap.add_argument("--clock-offset-ms", type=float, required=True)
    ap.add_argument("--progress-file", required=True)
    ap.add_argument("--influx-url", default=os.environ.get("INFLUXDB_URL", "http://localhost:8086"))
    ap.add_argument("--influx-token", default=os.environ.get("INFLUXDB_TOKEN", "telematics-dev-token"))
    ap.add_argument("--influx-org", default=os.environ.get("INFLUXDB_ORG", "telematics-org"))
    ap.add_argument("--influx-bucket", default=os.environ.get("INFLUXDB_BUCKET", "telematics"))
    args = ap.parse_args()
    offset = args.clock_offset_ms / 1000.0

    run_start = datetime.now(timezone.utc)
    acks = {}  # trip_id -> list of ack times (host clock)
    driver_of = {}
    sent = 0

    def cb(err, msg):
        if err is None:
            acks[json.loads(msg.value())["trip_id"]].append(time.time())

    producer = Producer({"bootstrap.servers": args.bootstrap, "client.id": "e2e-latency-bench", "linger.ms": 50})
    producer.list_topics(timeout=10)
    for ev in stream_events(args.drivers, args.trips):
        acks.setdefault(ev["trip_id"], [])
        driver_of[ev["trip_id"]] = ev["driver_id"]
        producer.produce(TOPIC, key=ev["driver_id"].encode(), value=json.dumps(ev).encode(), callback=cb)
        producer.poll(0)
        sent += 1
        if args.speed > 0:
            time.sleep(args.speed)
    producer.flush(30)
    last_ack_overall = max(max(v) for v in acks.values() if v)

    client = InfluxDBClient(url=args.influx_url, token=args.influx_token, org=args.influx_org)
    qapi = client.query_api()
    flux = f"""
from(bucket: "{args.influx_bucket}")
  |> range(start: {run_start.strftime("%Y-%m-%dT%H:%M:%SZ")})
  |> filter(fn: (r) => r._measurement == "driver_risk" and r._field == "risk_score")
  |> keep(columns: ["_time", "trip_id"])
"""

    def fetch_points():
        pts = {}
        for table in qapi.query(flux):
            for rec in table.records:
                tid = rec.values.get("trip_id")
                if tid in acks:
                    pts.setdefault(tid, []).append(rec.get_time().timestamp())
        return pts

    # Wait until every trip has a point written after its last ack (host time).
    while True:
        points = fetch_points()
        done = all(tid in points and max(points[tid]) - offset > max(acks[tid]) for tid in acks)
        if done or time.time() - last_ack_overall > args.wait:
            break
        time.sleep(2)
    time.sleep(5)  # let the listener append the progress line of the last batch
    points = fetch_points()
    client.close()

    starts = load_batch_starts(args.progress_file)

    def latency_for(tid, ack):
        best = None
        for pt in points.get(tid, []):
            bs = batch_start_for(pt, starts)
            if bs is None or bs - offset < ack:
                continue  # batch started before the event existed in Kafka
            lat = (pt - offset) - ack
            best = lat if best is None or lat < best else best
        return best

    per_event, per_trip_complete, unmatched_events = [], [], 0
    for tid, ack_list in acks.items():
        for a in ack_list:
            lat = latency_for(tid, a)
            if lat is None:
                unmatched_events += 1
            else:
                per_event.append(lat)
        if ack_list:
            lat = latency_for(tid, max(ack_list))
            if lat is not None:
                per_trip_complete.append(lat)

    print(
        json.dumps(
            {
                "run_start_utc": run_start.isoformat(),
                "clock_offset_ms_applied": args.clock_offset_ms,
                "events_sent": sent,
                "events_acked": sum(len(v) for v in acks.values()),
                "drivers": len(set(driver_of.values())),
                "trips_produced": len(acks),
                "trips_with_any_score": len(points),
                "trips_missing_score": sorted(set(acks) - set(points)),
                "scored_records_in_influx": sum(len(v) for v in points.values()),
                "events_without_matching_score": unmatched_events,
                "latency_event_to_score": summary(per_event),
                "latency_trip_last_event_to_score": summary(per_trip_complete),
            }
        )
    )


if __name__ == "__main__":
    main()
