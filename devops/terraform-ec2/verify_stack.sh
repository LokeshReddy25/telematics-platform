#!/bin/bash
# verify_stack.sh: run ON the EC2 instance (via SSM Run Command) to check the
# deployed stack end to end. Prints evidence only; changes nothing.
# Tokens are never printed: the influx CLI uses the config created by the
# container's init setup, and the Risk API holds its own token.
set -u
cd /opt/telematics-platform
C="docker compose -f docker-compose.yml -f docker-compose.aws.yml"
echo "== time: $(date -u -Is)  uptime: $(uptime -p)"
echo "== services"
$C ps -a --format '{{.Service}}\t{{.State}}\t{{.Status}}'
echo "== published ports on the host (expect 127.0.0.1 only)"
ss -ltnH | awk '{print $4}' | sort -u
echo "== Kafka: end offsets of vehicle.trip.events, 30 s apart"
off() { docker exec telematics-kafka kafka-get-offsets --bootstrap-server localhost:9092 --topic vehicle.trip.events 2>/dev/null | awk -F: '{s+=$3} END {print s+0}'; }
o1=$(off); sleep 30; o2=$(off)
echo "total offset t0=$o1  t0+30s=$o2  delta=$((o2 - o1))"
echo "== Spark: scored windows printed so far"
$C logs --no-log-prefix spark 2>/dev/null | grep -c '^\[batch'
$C logs --no-log-prefix spark 2>/dev/null | grep '^\[batch' | tail -n 3
echo "== InfluxDB: scores written in the last 15 min"
docker exec telematics-influxdb influx query --raw=false '
from(bucket: "telematics") |> range(start: -15m)
  |> filter(fn: (r) => r._measurement == "driver_risk" and r._field == "risk_score")
  |> group() |> count()' 2>&1 | tail -n 3
docker exec telematics-influxdb influx query '
from(bucket: "telematics") |> range(start: -15m)
  |> filter(fn: (r) => r._measurement == "driver_risk" and r._field == "risk_score")
  |> keep(columns: ["trip_id"]) |> group() |> distinct(column: "trip_id") |> count()' 2>&1 | tail -n 3
echo "== Risk API (inside the instance, port bound to 127.0.0.1)"
curl -s -w ' HTTP %{http_code}\n' http://127.0.0.1:8001/health
curl -s -w ' HTTP %{http_code}\n' 'http://127.0.0.1:8001/drivers/top-risk?limit=3'
curl -s -o /dev/null -w 'limit=0 -> HTTP %{http_code}\n' 'http://127.0.0.1:8001/drivers/top-risk?limit=0'
curl -s -o /dev/null -w 'grafana /api/health -> HTTP %{http_code}\n' http://127.0.0.1:3000/api/health
echo "== resources"
docker stats --no-stream --format 'table {{.Name}}\t{{.MemUsage}}\t{{.MemPerc}}\t{{.CPUPerc}}'
free -m
df -h /
