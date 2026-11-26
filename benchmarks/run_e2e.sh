#!/bin/bash
# Runs the end-to-end latency benchmark 3 times against the running stack.
# Each run first measures the host<->Spark-container clock offset, then
# passes it to e2e_latency.py. Raw output goes to results/raw/.
# Usage (from repo root): bash benchmarks/run_e2e.sh
set -u
cd "$(dirname "$0")/.."
for i in 1 2 3; do
  f=results/raw/e2e_latency_run$i.log
  echo "\$ python benchmarks/clock_offset.py telematics-spark-bench   ($(date -Iseconds))" > "$f"
  off_json=$(python benchmarks/clock_offset.py telematics-spark-bench)
  echo "$off_json" >> "$f"
  off=$(python -c "import json,sys;print(json.loads(sys.argv[1])['offset_ms'])" "$off_json")
  echo "\$ python benchmarks/e2e_latency.py --drivers 5 --trips 3 --speed 0.3 --wait 240 --clock-offset-ms $off --progress-file results/raw/spark_progress.jsonl   ($(date -Iseconds))" >> "$f"
  python benchmarks/e2e_latency.py --drivers 5 --trips 3 --speed 0.3 --wait 240 --clock-offset-ms "$off" --progress-file results/raw/spark_progress.jsonl >> "$f" 2>&1
  echo "exit=$?" >> "$f"
  sleep 45
done
echo ALL_DONE
