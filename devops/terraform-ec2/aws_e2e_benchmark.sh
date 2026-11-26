#!/bin/bash
# aws_e2e_benchmark.sh: run ON the EC2 instance. Repeats the local e2e latency
# benchmark (benchmarks/run_e2e.sh, 3 runs) with the same method as
# results/results.md: only the benchmark producer sends events (the looping
# simulator is stopped), and Spark runs through benchmarks/spark_with_progress.py
# so micro-batch start times are logged. Restores the normal services at the end.
set -euxo pipefail
cd /opt/telematics-platform
C="docker compose -f docker-compose.yml -f docker-compose.aws.yml"

# Host Python deps for the benchmark scripts (AL2023 ships python3 without pip).
dnf install -y python3-pip
python3 -m pip install --quiet confluent-kafka==2.3.0 influxdb-client==1.40.0
mkdir -p /opt/pybin && ln -sf /usr/bin/python3 /opt/pybin/python
export PATH=/opt/pybin:$PATH

$C stop simulator spark
docker rm -f telematics-spark-bench 2>/dev/null || true
mkdir -p results/raw
rm -f results/raw/spark_progress.jsonl results/raw/e2e_latency_run*.log

docker run -d --name telematics-spark-bench --network telematics-net \
  -e INFLUXDB_URL=http://influxdb:8086 -e PROGRESS_FILE=/out/spark_progress.jsonl \
  -v "$PWD/benchmarks:/app/benchmarks:ro" -v "$PWD/results/raw:/out" -v "$PWD/ml/models:/app/ml/models:ro" \
  -v telematics-platform_spark-ivy:/root/.ivy2 \
  --entrypoint /opt/spark/bin/spark-submit telematics/spark-stream-processor:local \
  --packages org.apache.spark:spark-sql-kafka-0-10_2.12:3.5.0 /app/benchmarks/spark_with_progress.py

for i in $(seq 1 60); do
  docker logs telematics-spark-bench 2>&1 | grep -q "Streaming query started" && break
  sleep 5
done
docker logs telematics-spark-bench 2>&1 | grep "Streaming query started"

bash benchmarks/run_e2e.sh

docker rm -f telematics-spark-bench
$C start spark simulator
echo "E2E BENCHMARK DONE $(date -u -Is)"
