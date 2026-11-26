"""
benchmarks/summarize.py
-----------------------
Reads the raw logs in results/raw/ and prints median / min / max for every
repeated measurement, so no number in results/results.md is computed by hand.

Usage (from repo root): python benchmarks/summarize.py > results/raw/summary.json
                        python benchmarks/summarize.py results/aws/raw > results/aws/summary_aws.json
Sections whose logs are absent from the directory are simply left out.
"""

import glob
import json
import os
import re
import statistics
import sys
from datetime import datetime

RAW = (
    sys.argv[1]
    if len(sys.argv) > 1
    else os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "results", "raw")
)


def stats(values):
    values = [v for v in values if v is not None]
    if not values:
        return None
    return {
        "runs": len(values),
        "median": round(statistics.median(values), 3),
        "min": round(min(values), 3),
        "max": round(max(values), 3),
        "values": values,
    }


def last_json(path):
    obj = None
    with open(path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line.startswith("{"):
                obj = json.loads(line)
    return obj


def files(pattern):
    return sorted(glob.glob(os.path.join(RAW, pattern)))


out = {}

# Kafka transport throughput (benchmarks/kafka_throughput.py)
kt = [last_json(p) for p in files("kafka_throughput_run*.log")]
out["kafka_produce_events_per_sec"] = stats([r["produce_events_per_sec"] for r in kt])
out["kafka_consume_events_per_sec"] = stats([r["consume_events_per_sec"] for r in kt])
out["kafka_throughput_events_per_run"] = [r["events"] for r in kt]

# Project producer, --speed 0 (from its own log timestamps: Connected -> Done)
ts_re = re.compile(r"^(\d{4}-\d\d-\d\d \d\d:\d\d:\d\d,\d{3})")
rates, counts = [], []
for p in files("kafka_producer_speed0_run*.log"):
    t_conn = t_done = n = None
    with open(p, encoding="utf-8") as f:
        for line in f:
            m = ts_re.match(line)
            if not m:
                continue
            t = datetime.strptime(m.group(1), "%Y-%m-%d %H:%M:%S,%f")
            if "Connected to Kafka" in line:
                t_conn = t
            if "Total events produced" in line:
                t_done = t
                n = int(line.rsplit(":", 1)[1])
    rates.append(round(n / (t_done - t_conn).total_seconds(), 1))
    counts.append(n)
out["project_producer_speed0_events_per_sec"] = stats(rates)
out["project_producer_speed0_events_per_run"] = counts

# End-to-end latency (benchmarks/e2e_latency.py)
e2e = [last_json(p) for p in files("e2e_latency_run*.log")]
e2e = [r for r in e2e if r and "latency_event_to_score" in r]
if e2e:
    E = [r["latency_event_to_score"] for r in e2e]
    T = [r["latency_trip_last_event_to_score"] for r in e2e]
    out["e2e_event_to_score_median_s"] = stats([x["median_s"] for x in E if x])
    out["e2e_event_to_score_p95_s"] = stats([x["p95_s"] for x in E if x])
    out["e2e_event_to_score_min_s"] = stats([x["min_s"] for x in E if x])
    out["e2e_event_to_score_max_s"] = stats([x["max_s"] for x in E if x])
    out["e2e_trip_complete_median_s"] = stats([x["median_s"] for x in T if x])
    out["e2e_trip_complete_p95_s"] = stats([x["p95_s"] for x in T if x])
    for key in (
        "events_sent",
        "events_acked",
        "drivers",
        "trips_produced",
        "trips_with_any_score",
        "scored_records_in_influx",
        "events_without_matching_score",
    ):
        out[f"e2e_{key}"] = stats([r[key] for r in e2e])
    out["e2e_clock_offset_ms_applied"] = [r["clock_offset_ms_applied"] for r in e2e]

# Spark micro-batches (benchmarks/spark_with_progress.py listener output)
prog_path = os.path.join(RAW, "spark_progress.jsonl")
if os.path.exists(prog_path):
    with open(prog_path, encoding="utf-8") as f:
        prog = [json.loads(line) for line in f if line.strip()]
    with_data = [p for p in prog if p["numInputRows"] > 0]
    out["spark_batches_total"] = len(prog)
    out["spark_batches_with_input"] = len(with_data)
    out["spark_trigger_execution_ms_batches_with_input"] = stats(
        [p["durationMs"].get("triggerExecution") for p in with_data]
    )
    out["spark_add_batch_ms_batches_with_input"] = stats([p["durationMs"].get("addBatch") for p in with_data])
    out["spark_input_rows_per_batch_with_input"] = stats([p["numInputRows"] for p in with_data])

# Model training (ml/train_model.py)
mae, r2 = [], []
for p in files("train_model_run*.log"):
    txt = open(p, encoding="utf-8").read()
    mae.append(float(re.search(r"MAE : ([\d.]+)", txt).group(1)))
    r2.append(float(re.search(r"R\^2 : ([\d.]+)", txt).group(1)))
out["model_mae"] = stats(mae)
out["model_r2"] = stats(r2)

# pytest
for name in (
    "pytest_pipeline",
    "pytest_pipeline_after_fix_commit",
    "pytest_pipeline_post_api_removal",
    "pytest_risk_api",
    "pytest_risk_api_post_api_removal",
):
    passed, total_cov = [], []
    for p in files(f"{name}_run*.log"):
        txt = open(p, encoding="utf-8").read()
        m = re.search(r"=+ (\d+) passed", txt)
        passed.append(int(m.group(1)) if m else 0)
        c = re.search(r"^TOTAL\s+\d+\s+\d+\s+(\d+)%", txt, re.M)
        total_cov.append(int(c.group(1)) if c else None)
    if passed:
        out[f"{name}_passed"] = stats(passed)
        out[f"{name}_coverage_pct"] = stats(total_cov)

print(json.dumps(out, indent=2))
