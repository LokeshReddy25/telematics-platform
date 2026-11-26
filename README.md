# Connected Vehicle Insurance Telematics Platform

[![CI](https://github.com/LokeshReddy25/telematics-platform/actions/workflows/ci.yml/badge.svg)](https://github.com/LokeshReddy25/telematics-platform/actions/workflows/ci.yml)
[![Risk API CI](https://github.com/LokeshReddy25/telematics-platform/actions/workflows/risk-api-ci.yml/badge.svg)](https://github.com/LokeshReddy25/telematics-platform/actions/workflows/risk-api-ci.yml)
[![API CI](https://github.com/LokeshReddy25/telematics-platform/actions/workflows/api-ci.yml/badge.svg)](https://github.com/LokeshReddy25/telematics-platform/actions/workflows/api-ci.yml)

An end-to-end streaming pipeline that turns simulated connected-vehicle trip data into per-trip driver risk scores, in the style of usage-based insurance (UBI). Simulated telemetry flows through **Kafka**. **Spark Structured Streaming** aggregates it in 30-second micro-batches and scores it with a **scikit-learn** Gradient Boosting model trained on synthetic data. Scores are written to **InfluxDB** and shown in **Grafana**, and a **FastAPI** service serves them over REST. Everything runs locally with one `docker compose up`. The repo also has Kubernetes manifests (run on a local cluster), Terraform for AWS (validated, **never applied, so nothing is deployed on AWS**) and GitHub Actions CI. Measured numbers are in [Results](#results).

## Architecture

```
                 ┌────────────────────┐
                 │  Trip Simulator     │
                 │  (trip_generator.py)│
                 │  safe/moderate/     │
                 │  aggressive drivers │
                 └──────────┬──────────┘
                             │ JSON events, keyed by driver_id
                             ▼
                 ┌────────────────────┐
                 │  Kafka Producer     │
                 │  (kafka_producer.py)│
                 └──────────┬──────────┘
                             │
                             ▼
                 ┌────────────────────┐
                 │  Apache Kafka       │
                 │  topic:             │
                 │  vehicle.trip.events│
                 └──────────┬──────────┘
                             │
                             ▼
                 ┌────────────────────────────┐
                 │  Spark Structured Streaming │
                 │  (stream_processor.py)      │
                 │  60s tumbling window,       │
                 │  grouped by driver+trip     │
                 └──────────┬──────────────────┘
                             │ collected trip events
                             ▼
                 ┌────────────────────┐
                 │  ML Risk Scorer     │
                 │  (risk_scorer.py)   │
                 │  GradientBoosting   │
                 │  -> risk_score 0-100│
                 └──────────┬──────────┘
                             │ scored trip dict
                             ▼
                 ┌────────────────────┐
                 │  InfluxDB Writer    │
                 │(influxdb_writer.py) │
                 └──────────┬──────────┘
                             │
                             ▼
                 ┌────────────────────┐
                 │     InfluxDB 2.x     │
                 │  bucket: telematics  │
                 │  measurement:        │
                 │  driver_risk          │
                 └──────────┬──────────┘
                             │ Flux queries (read-only)
                 ┌───────────┴─────────────┐
                 ▼                         ▼
     ┌──────────────────────┐   ┌──────────────────────────┐
     │      Grafana         │   │  Driver Risk Score API    │
     │  live risk score,    │   │  (risk_api/, FastAPI)     │
     │  category pie chart, │   │  GET /drivers/{id}/risk   │
     │  harsh-brake bars,   │   │  GET /drivers/top-risk    │
     │  top-5 riskiest table│   │  GET /trips/{id}          │
     │  :3000               │   │  GET /health   :8001      │
     └──────────────────────┘   └──────────────────────────┘
```

## Prerequisites

| Requirement | Version | Why |
|---|---|---|
| Docker + Docker Compose | Compose v2.20+ (uses `include`) | Runs every service, including Spark and model training |
| Python | 3.11 | Only for running tests/benchmarks or the pieces outside Docker |
| (Optional) Terraform | 1.5+ | AWS infrastructure code (not applied) |
| (Optional) kubectl | 1.28+ | Kubernetes deployment |

## Quickstart

```bash
git clone https://github.com/LokeshReddy25/telematics-platform.git
cd telematics-platform
docker compose up -d --build
```

That one command:

1. trains the risk model into `ml/models/` if it isn't there (`model-trainer`, runs once)
2. starts Zookeeper, Kafka and Kafka UI, then creates the `vehicle.trip.events` topic (`kafka-init`, runs once)
3. starts InfluxDB, Grafana, the Spark streaming job, the trip simulator (re-runs after each batch of trips) and the Risk API

Then open:

| What | URL |
|---|---|
| Grafana dashboard (no login) | http://localhost:3000/d/telematics-driver-risk/ |
| Risk API docs (Swagger UI) | http://localhost:8001/docs |
| Kafka UI | http://localhost:8080 |

Spark takes about a minute to start (it downloads the Kafka connector on the
first run), and it reads Kafka from the latest offset. Expect the first scores
a minute or two after `up` finishes. Spark scores on a 30-second trigger after
that. Check progress with `docker compose logs -f spark`.

Stop with `docker compose down` (add `-v` to delete the stored data).

If you previously started `kafka/docker-compose.yml` on its own, run
`docker compose -f kafka/docker-compose.yml down` first, or the fixed
container names clash.

## Driver Risk Score API

A read-only FastAPI service (`risk_api/`) over the scores the Spark job
writes to InfluxDB. It starts with `docker compose up` on port 8001, and its
OpenAPI docs are at http://localhost:8001/docs. Details are in
[risk_api/README.md](risk_api/README.md).

| Method & path | Returns |
|---|---|
| `GET /health` | 200 if InfluxDB answers its ping, 503 otherwise |
| `GET /drivers/{driver_id}/risk` | Latest score, number of trips scored, number of records, mean of each trip's latest score (404 if none) |
| `GET /drivers/top-risk?limit=N` | Drivers ranked by their most recent score, N = 1–100 (default 5) |
| `GET /trips/{trip_id}` | Every score written for the trip, oldest first (Spark re-scores a trip as events arrive) |

Invalid IDs or limits return 422, missing data returns 404, and an
unreachable InfluxDB returns 503. All errors use
`{"error": {"code", "message", "details"}}`. Configuration comes only from
environment variables, and `INFLUXDB_TOKEN` has no default.

```bash
curl http://localhost:8001/drivers/top-risk?limit=3
cd risk_api && pip install -r requirements-test.txt && python -m pytest -m "not integration" --cov=app
```

## Vehicle Telematics API

A separate FastAPI service (`api/`), not started by the root compose file, that turns the streaming platform into a
queryable product: register vehicles and trips, ingest telemetry events,
and read driver risk scores — all behind JWT auth. Risk scores live in
PostgreSQL (the API's store). It can accept scores via
`POST /drivers/{driver_id}/risk-scores`, but **the Spark job does not call
it**: Spark writes only to InfluxDB. To read the pipeline's scores over
REST, use the Driver Risk Score API above.

```bash
# From api/ — starts the API plus PostgreSQL (Kafka/Spark untouched)
cd api
docker compose up --build
# API at http://localhost:8000 — interactive docs at /docs

# Without Docker: pip install -r requirements.txt, then
# DATABASE_URL defaults to a local SQLite file (telematics.db)
uvicorn app.main:app --reload
```

Endpoints (all except `/health` need a `Bearer` token from `/auth/login`):

| Method & path | What it does |
|---|---|
| `GET /health` | Liveness check (also pings the database) |
| `POST /auth/register`, `POST /auth/login` | Create a user / get a JWT (60-min expiry) |
| `POST /vehicles`, `GET /vehicles`, `GET /vehicles/{id}` | Register and list vehicles (duplicate VIN → 409) |
| `POST /trips`, `GET /trips`, `GET /trips/{id}` | Start a trip, list trips, fetch one |
| `POST /telemetry` | Ingest one event or a batch of events |
| `GET /trips/{id}/telemetry`, `GET /vehicles/{id}/telemetry?from=&to=` | Read events back, in timestamp order |
| `POST /drivers/{id}/risk-scores` | Record a computed score (Spark job's writer) |
| `GET /drivers/{id}/risk-score`, `GET /drivers/{id}/risk-scores?limit=` | Latest score / score history (scoped to your own driver) |

Tests: `python -m pytest api/tests -v` (24 tests, in-memory SQLite, no
Postgres needed). CI runs them on every push in `.github/workflows/api-ci.yml`.

## Environment Variables

| Variable | Used By | Default | Description |
|---|---|---|---|
| `KAFKA_BOOTSTRAP_SERVERS` | spark, simulator (containerised) | `kafka:9092` | Kafka broker address(es) |
| `SPARK_CHECKPOINT_LOCATION` | spark | `/tmp/telematics-checkpoints` | Structured Streaming checkpoint directory |
| `INFLUXDB_URL` | storage, spark | `http://localhost:8086` | InfluxDB 2.x API endpoint |
| `INFLUXDB_TOKEN` | storage, spark | `telematics-dev-token` | InfluxDB auth token |
| `INFLUXDB_ORG` | storage, spark | `telematics-org` | InfluxDB organization name |
| `INFLUXDB_BUCKET` | storage, spark | `telematics` | InfluxDB bucket for scored trips |
| `DATABASE_URL` | api | `sqlite:///./telematics.db` | API database; compose sets `postgresql://telematics:telematics-dev@postgres:5432/telematics` |
| `JWT_SECRET` | api | `dev-only-secret-change-me` | Secret signing the API's JWTs — set a real one outside local dev |
| `INFLUXDB_TOKEN` | risk_api | **none (required)** | Read token; the Risk API refuses to start without it. Compose passes the local dev token |
| `INFLUXDB_URL` / `INFLUXDB_ORG` / `INFLUXDB_BUCKET` | risk_api | `http://localhost:8086` / `telematics-org` / `telematics` | Same meaning as above |
| `RISK_API_LOOKBACK` | risk_api | `-30d` | How far back queries look (Flux duration) |
| `RISK_API_QUERY_TIMEOUT_MS` | risk_api | `10000` | InfluxDB query timeout |

## Tech Stack

| Technology | Version | Role |
|---|---|---|
| Python | 3.11 | Simulator, ML, orchestration glue |
| Apache Kafka (Confluent) | 7.5.0 | Event streaming backbone |
| Apache Spark (PySpark) | 3.5.0 | Structured Streaming aggregation |
| scikit-learn | 1.4.0 | GradientBoostingRegressor risk model |
| FastAPI + Pydantic | 0.115.6 / 2.14 | Driver Risk Score API (`risk_api/`, reads InfluxDB) and Vehicle Telematics API (`api/`) |
| influxdb-client | 1.40.0 | Writes scores (`storage/`), reads them (`risk_api/`) |
| PostgreSQL (SQLAlchemy) | 16 / 2.0 | API store: vehicles, trips, telemetry, risk scores |
| JWT (python-jose + passlib) | — | API authentication (HS256, 60-min tokens) |
| pytest + pytest-cov | 8.3 / 5.0 | `tests/` (17), `api/tests/` (24), `risk_api/tests/` (46 incl. 5 integration) |
| InfluxDB | 2.7 | Time-series risk score storage |
| Grafana | 10.4.2 | Live risk dashboard |
| Docker / Docker Compose | Compose v2.20+ | Local containerisation, one-command stack |
| Kubernetes | 1.28+ | Manifests in `devops/k8s/`, run on a local Docker Desktop cluster |
| Terraform (AWS) | 1.5+ | Infrastructure as code: validated, **not applied** |
| GitHub Actions | — | CI: lint, tests, image builds. The `deploy` job is a placeholder (no CD) |

## Results

Measured on one laptop (i5-12500H, 16 GB RAM, Docker Desktop on Windows 11)
on 2026-11-26. Each figure is the median of 3 runs. Commands, raw logs,
min/max and everything that was **not** measured are in
[results/results.md](results/results.md).

| Metric | Value |
|---|---|
| Kafka throughput, transport only (produce / consume) | 40,042.7 / 44,898.9 events/s |
| `kafka_producer.py` at full speed, incl. event generation | 40,960.6 events/s |
| Event produced → risk score in InfluxDB, median / p95 | 16.155 s / 29.454 s (bounded by the 30 s Spark trigger) |
| Spark micro-batch processing time (batches with input) | median 938 ms |
| Demo run size (`--drivers 5 --trips 3`) | 5 drivers, 15 trips, 454–492 events, 34–39 scored records per run |
| Model (synthetic data, seeded 80/20 random split, 2,000 test rows) | MAE 2.646, R² 0.975 |
| Tests | `tests/` 17/17 (33% coverage) · `api/tests` 24/24 (96%) · `risk_api/tests` 46/46 (100%) |

The model is trained and tested on synthetic data whose labels come from a
formula, so R² shows it learned that formula, not real-world risk. Its
training distributions also differ from what the simulator produces (see
the results file).

## How this maps to production

**Nothing in this repo has been deployed to AWS.** Both Terraform stacks
(`devops/terraform`, `deploy/terraform`) pass `terraform validate` but have
never been applied. The table describes the intended mapping.

| Local component | AWS/production equivalent | Notes |
|---|---|---|
| `kafka/docker-compose.yml` (Confluent Kafka + Zookeeper) | **Amazon MSK** (`devops/terraform/main.tf`) | Managed Kafka; same topic/partition model |
| `spark/stream_processor.py` running via `docker run`/`Dockerfile.spark` | **EC2 running the same container**, or **Amazon EMR** for larger clusters | The Terraform config provisions a single EC2 host running the identical Docker image built locally; EMR would be the next step for multi-node Spark |
| `storage/influxdb_writer.py` -> local InfluxDB | **Amazon Timestream** or **InfluxDB Cloud** | Same write contract; only the connection URL/credentials change |
| `dashboard/grafana_config/` -> local Grafana | **Amazon Managed Grafana** | Same datasource/dashboard JSON, provisioned instead of file-mounted |
| `devops/k8s/*.yaml` | **Amazon EKS** | Manifests are cloud-agnostic; apply as-is to any conformant Kubernetes cluster |
| `.github/workflows/ci.yml` `deploy` job | **Terraform apply + ECR push**, gated on AWS credentials in GitHub Secrets | Placeholder job intentionally left for the operator to wire up their own AWS account |

## Project Layout

See the annotated source under `simulator/`, `kafka/`, `spark/`, `ml/`, `storage/`, `dashboard/`, `devops/`, `risk_api/` and `api/`. Each file has inline comments explaining what it does and why. Measurement scripts are in `benchmarks/` and their output is in `results/`.
