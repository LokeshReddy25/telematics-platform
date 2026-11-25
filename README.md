# Connected Vehicle Insurance Telematics Platform

A production-structured, end-to-end telematics pipeline that turns raw connected-vehicle trip data into a per-trip driver risk score, streamed live to a monitoring dashboard. It models the kind of usage-based insurance (UBI) architecture built by insurers and automotive/connected-vehicle platforms worldwide: simulated vehicle telemetry flows through **Kafka**, is aggregated and scored in near-real-time by **Spark Structured Streaming** and a **scikit-learn** risk model, is persisted to **InfluxDB**, and is visualised live in **Grafana** — with a full Docker/Kubernetes/Terraform/CI DevOps layer underneath so the same pipeline that runs on a laptop can be deployed to AWS.

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
                             │
                             ▼
                 ┌────────────────────┐
                 │      Grafana         │
                 │  live risk score,    │
                 │  category pie chart, │
                 │  harsh-brake bars,   │
                 │  top-5 riskiest table│
                 └────────────────────┘

     (Prometheus scrapes container/service metrics across the stack
      for operational monitoring, alongside the Grafana risk dashboard.)
```

## Prerequisites

| Requirement | Version | Why |
|---|---|---|
| Docker + Docker Compose | 24.x+ | Runs Kafka, InfluxDB, Grafana, and every service locally |
| Python | 3.11 | Simulator, ML training/scoring, local dev of the Spark job |
| Java | 11 | Required by PySpark's JVM runtime |
| (Optional) Terraform | 1.5+ | AWS deployment |
| (Optional) kubectl | 1.28+ | Kubernetes deployment |

## Quickstart

```bash
# 1. Clone the repository
git clone <your-fork-url> telematics-platform
cd telematics-platform

# 2. Create a virtual environment and install ML deps, then train the model
python -m venv .venv
source .venv/bin/activate            # Windows: .venv\Scripts\activate
pip install -r ml/requirements.txt
python ml/train_model.py             # produces ml/models/risk_model.pkl

# 3. Start Kafka (+ Zookeeper + Kafka UI at http://localhost:8080)
cd kafka
docker-compose up -d
cd ..

# 4. Start the trip producer (from the simulator/ directory)
cd simulator
pip install -r requirements.txt
python kafka_producer.py --bootstrap localhost:29092 --drivers 5 --trips 3 --speed 0.3
cd ..

# 5. Start the Spark streaming job (needs Java 11 + pip install -r spark/requirements.txt)
cd spark
pip install -r requirements.txt
python stream_processor.py           # reads env var KAFKA_BOOTSTRAP_SERVERS, defaults to kafka:9092
cd ..

# 6. Start InfluxDB + Grafana (see docker-compose service definitions you
#    add to your local stack, provisioned via dashboard/grafana_config/)
#    Then open Grafana at http://localhost:3000 to view the live dashboard.
```

## Vehicle Telematics API

A FastAPI service (`api/`) that turns the streaming platform into a
queryable product: register vehicles and trips, ingest telemetry events,
and read driver risk scores — all behind JWT auth. Risk scores live in
PostgreSQL (the API's store); InfluxDB stays as the time-series sink for
the streaming pipeline, and the Spark job writes scored trips here via
`POST /drivers/{driver_id}/risk-scores`.

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

## Tech Stack

| Technology | Version | Role |
|---|---|---|
| Python | 3.11 | Simulator, ML, orchestration glue |
| Apache Kafka (Confluent) | 7.5.0 | Event streaming backbone |
| Apache Spark (PySpark) | 3.5.0 | Structured Streaming aggregation |
| scikit-learn | 1.4.0 | GradientBoostingRegressor risk model |
| FastAPI | 0.115 | Vehicle Telematics API (`api/`) |
| PostgreSQL (SQLAlchemy) | 16 / 2.0 | API store: vehicles, trips, telemetry, risk scores |
| JWT (python-jose + passlib) | — | API authentication (HS256, 60-min tokens) |
| pytest | 8.3 | API test suite (`api/tests/`, 24 tests) |
| InfluxDB | 2.7 | Time-series risk score storage |
| Grafana | latest | Live risk dashboard |
| Prometheus | latest | Infrastructure/service metrics |
| Docker / Docker Compose | 24.x | Local containerisation |
| Kubernetes | 1.28+ | Cluster orchestration |
| Terraform (AWS) | 1.5+ | Cloud infrastructure as code |
| GitHub Actions | — | CI/CD |

## How this maps to production

| Local component | AWS/production equivalent | Notes |
|---|---|---|
| `kafka/docker-compose.yml` (Confluent Kafka + Zookeeper) | **Amazon MSK** (`devops/terraform/main.tf`) | Managed, multi-broker, multi-AZ Kafka; same topic/partition model |
| `spark/stream_processor.py` running via `docker run`/`Dockerfile.spark` | **EC2 running the same container**, or **Amazon EMR** for larger clusters | The Terraform config provisions a single EC2 host running the identical Docker image built locally; EMR would be the next step for multi-node Spark |
| `storage/influxdb_writer.py` -> local InfluxDB | **Amazon Timestream** or **InfluxDB Cloud** | Same write contract; only the connection URL/credentials change |
| `dashboard/grafana_config/` -> local Grafana | **Amazon Managed Grafana** | Same datasource/dashboard JSON, provisioned instead of file-mounted |
| `devops/k8s/*.yaml` | **Amazon EKS** | Manifests are cloud-agnostic; apply as-is to any conformant Kubernetes cluster |
| `.github/workflows/ci.yml` `deploy` job | **Terraform apply + ECR push**, gated on AWS credentials in GitHub Secrets | Placeholder job intentionally left for the operator to wire up their own AWS account |

## Project Layout

See the full annotated source tree under each of `simulator/`, `kafka/`, `spark/`, `ml/`, `storage/`, `dashboard/`, and `devops/` — every file contains inline comments explaining what it does and why, since this project doubles as a learning resource alongside being a deployable platform.
