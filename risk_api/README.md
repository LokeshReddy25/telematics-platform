# Driver Risk Score API (`risk_api/`)

A read-only FastAPI service over the risk scores that the Spark job writes to
InfluxDB (measurement `driver_risk`). It doesn't write anything and doesn't
change the pipeline.

| Method & path | Returns | Errors |
|---|---|---|
| `GET /health` | `{"status":"ok","influxdb":"reachable"}` | 503 if InfluxDB doesn't answer |
| `GET /drivers/{driver_id}/risk` | Latest score, trips scored, record count, mean of each trip's latest score | 404 unknown driver, 422 bad id, 503 |
| `GET /drivers/top-risk?limit=N` | Drivers ranked by their latest score (N = 1–100, default 5) | 422 bad limit, 503 |
| `GET /trips/{trip_id}` | Every score for the trip, oldest first, plus the latest | 404, 422, 503 |

Every error uses the same shape: `{"error": {"code", "message", "details"}}`.
OpenAPI docs are served at `/docs` and `/redoc`.

IDs must match `^[A-Za-z0-9_-]{1,64}$`. InfluxDB OSS has no parameterised
Flux queries, so this check is what keeps user input from changing the query.

## Configuration (environment variables)

| Variable | Default | Notes |
|---|---|---|
| `INFLUXDB_TOKEN` | **none, required** | The app won't start without it |
| `INFLUXDB_URL` | `http://localhost:8086` | |
| `INFLUXDB_ORG` | `telematics-org` | |
| `INFLUXDB_BUCKET` | `telematics` | |
| `RISK_API_LOOKBACK` | `-30d` | Flux range start, e.g. `-12h` |
| `RISK_API_MEASUREMENT` | `driver_risk` | |
| `RISK_API_QUERY_TIMEOUT_MS` | `10000` | |

## Run

```bash
# Whole stack, from the repo root
docker compose up -d --build        # API at http://localhost:8001/docs

# Just the API, against an InfluxDB you already have
cd risk_api
pip install -r requirements.txt
INFLUXDB_TOKEN=<token> uvicorn app.main:app --port 8001
```

## Test

```bash
cd risk_api
pip install -r requirements-test.txt
python -m pytest -m "not integration" --cov=app --cov-report=term-missing   # InfluxDB mocked

# Integration: a real InfluxDB; creates and deletes its own bucket
RISK_API_IT_INFLUXDB_URL=http://localhost:8086 RISK_API_IT_INFLUXDB_TOKEN=<token with bucket rights> \
  python -m pytest -m integration -v
```
