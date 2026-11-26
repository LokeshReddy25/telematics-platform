# benchmarks/

Measurement scripts for `results/results.md`. None of them change pipeline
code. They import the pipeline's own modules and write raw output to
`results/raw/`.

| Script | Measures |
|---|---|
| `kafka_throughput.py` | Kafka produce/consume events/s, on a throwaway topic (events pre-generated, so this is transport only) |
| `spark_with_progress.py` | Runs the Spark job using `spark/stream_processor.py`'s own functions and settings, and adds a `StreamingQueryListener` that logs each micro-batch's duration to a JSONL file |
| `clock_offset.py` | Clock offset between the host and the Spark container (needed because latency compares a host-side Kafka ack with the container-side `scored_at`) |
| `e2e_latency.py` | Event acked by Kafka → risk score written to InfluxDB, per event and per trip |
| `run_e2e.sh` | Runs `clock_offset.py` + `e2e_latency.py` three times |
| `feature_skew.py` | Compares live simulator features with the model's synthetic training distributions |
| `trip_coverage.py` | How many trips produced in a time window got at least one score in InfluxDB |
| `summarize.py` | Reads every raw log and prints median/min/max as JSON (`results/raw/summary.json`) |

## Reproduce (from the repo root, Git Bash on Windows)

```bash
docker compose -f kafka/docker-compose.yml up -d
docker compose up -d influxdb grafana
docker build -f devops/Dockerfile.spark -t telematics-spark:bench .

python benchmarks/kafka_throughput.py --bootstrap localhost:29092 --drivers 200 --trips 8   # x3

# Spark job + micro-batch listener (Windows paths shown; adjust for your machine)
MSYS_NO_PATHCONV=1 docker run -d --name telematics-spark-bench --network telematics-net \
  -e INFLUXDB_URL=http://influxdb:8086 -e PROGRESS_FILE=/out/spark_progress.jsonl \
  -v "$(cygpath -w "$PWD")\\benchmarks:/app/benchmarks:ro" -v "$(cygpath -w "$PWD")\\results\\raw:/out" \
  --entrypoint /opt/spark/bin/spark-submit telematics-spark:bench \
  --packages org.apache.spark:spark-sql-kafka-0-10_2.12:3.5.0 /app/benchmarks/spark_with_progress.py

bash benchmarks/run_e2e.sh
python benchmarks/summarize.py > results/raw/summary.json
```
