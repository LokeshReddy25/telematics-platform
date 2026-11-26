#!/bin/bash
# aws_tests.sh: run ON the EC2 instance. Runs the repo's test suites in
# throwaway python:3.11-slim containers (same Python as CI).
#  - tests/ (pipeline): uses the model trained by the model-trainer service.
#  - risk_api/: unit tests + integration tests against the deployed InfluxDB.
#    The token is read from the running risk-api container and passed as an
#    environment variable; it is never printed.
set -uo pipefail
cd /opt/telematics-platform

echo "== pipeline tests (tests/)"
docker run --rm -v "$PWD:/src" -w /src python:3.11-slim bash -c \
  "pip install -q -r ml/requirements.txt pytest==8.3.3 2>&1 | tail -n 1; python -m pytest tests/ -q -p no:cacheprovider"
echo "pipeline_exit=$?"

echo "== risk_api tests (unit + integration against the deployed InfluxDB)"
RISK_API_IT_INFLUXDB_TOKEN="$(docker exec telematics-platform-risk-api-1 printenv INFLUXDB_TOKEN)"
export RISK_API_IT_INFLUXDB_TOKEN
docker run --rm --network telematics-net -e RISK_API_IT_INFLUXDB_TOKEN \
  -e RISK_API_IT_INFLUXDB_URL=http://influxdb:8086 \
  -v "$PWD/risk_api:/src" -w /src python:3.11-slim bash -c \
  "pip install -q -r requirements-test.txt 2>&1 | tail -n 1; python -m pytest -q -rs -p no:cacheprovider --cov=app --cov-report=term"
echo "risk_api_exit=$?"
echo "TESTS DONE $(date -u -Is)"
