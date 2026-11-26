# Run Guide — Connected Vehicle Insurance Telematics Platform

The whole stack (Kafka → Spark → ML risk scoring → InfluxDB → Grafana, plus the
Risk API) starts from **one** Docker Compose command in the repo root. Do not
also start Kafka or Spark by hand, or they will run twice.

## One-time setup

- Docker Desktop installed and running (Compose v2.20+, which supports `include`).
- Nothing else. The model is trained inside Docker on first start if
  `ml/models/risk_model.pkl` is missing, and Spark runs in a container.
  Python 3.11 is only needed to run tests or benchmarks on the host.

## Every time you want to run the demo

Open a terminal in `telematics-platform/` (PowerShell or Git Bash):

```
docker compose up -d --build
```

That single command:

| Service | What it does |
|---|---|
| `model-trainer` | Trains `ml/models/risk_model.pkl` if missing, then exits (0) |
| `zookeeper`, `kafka`, `kafka-ui` | Kafka broker (included from `kafka/docker-compose.yml`) |
| `kafka-init` | Creates topic `vehicle.trip.events` (3 partitions), then exits (0) |
| `influxdb`, `grafana` | Score storage and the provisioned dashboard |
| `spark` | The Structured Streaming job; starts after the topic and model exist |
| `simulator` | Sends 5 drivers × 3 trips, exits and is restarted, so data keeps flowing |
| `risk-api` | REST API over the scores in InfluxDB |

Check it came up:
```
docker compose ps -a
```
`model-trainer` and `kafka-init` should show `Exited (0)`. All other services
should be `running` (influxdb, kafka, zookeeper and risk-api show `healthy`).

Spark needs about a minute to start, because it downloads the Kafka
connector on the first run and caches it in the `spark-ivy` volume. It reads
Kafka from the latest offset. In a fresh-clone test, the first scores
appeared about 60 s after `up` finished, and after that every 30 s.

## View the results

- Grafana dashboard: http://localhost:3000/d/telematics-driver-risk/ (no login,
  anonymous admin is enabled for local dev)
- Risk API docs: http://localhost:8001/docs (for example
  http://localhost:8001/drivers/top-risk?limit=5)
- Kafka UI (raw event stream): http://localhost:8080
- Spark scoring output live: `docker compose logs -f spark`
- Simulator output: `docker compose logs -f simulator`

## Shutting everything down

```
docker compose down
```
Add `-v` to also delete Kafka, InfluxDB, Grafana and the Spark jar cache, for
a completely fresh start next time (the topic is recreated automatically).

## Moving from the old manual flow (one time)

The old flow ran `kafka/docker-compose.yml` on its own and the Spark job with
`docker run --name telematics-spark`. If any of those are still around,
remove them first, or the fixed container names (`telematics-kafka` and so on)
will clash:
```
docker compose -f kafka/docker-compose.yml -p kafka down
docker rm -f telematics-spark
```
`run_simulator_loop.sh` is no longer needed, because the `simulator` service
loops on its own. Running it as well just adds a second producer.

## Troubleshooting

| Symptom | Cause | Fix |
|---|---|---|
| `Conflict. The container name "/telematics-kafka" is already in use` | The old separately started Kafka project is still present | See "Moving from the old manual flow" above |
| Kafka exits with `NodeExistsException` (`/brokers/ids/1`) right after Docker was restarted | A stale Zookeeper node left by an unclean Docker shutdown | Wait about 20 s and run `docker compose up -d` again (no data loss). Seen once on 2026-11-26 |
| `spark` restarting or no `[batch N]` lines in its logs | Still downloading the connector, or Kafka not ready | `docker compose logs spark`, then wait about 60 s |
| Grafana panels empty | No scores yet | Check `docker compose logs spark` for `[batch N]` lines and wait 30–60 s |
| `HADOOP_HOME and hadoop.home.dir are unset` when running `python stream_processor.py` on Windows | Native PySpark needs `winutils.exe` | Use the `spark` container instead |

## Fixes that are now committed

1. **`simulator/trip_generator.py`**: trips used to be backdated by up to 24 h
   at random. With Spark's 2-minute watermark, most were dropped: on a fresh
   clone of that code only 7 of 27 trips got a score. Trips now start within
   0–20 s of "now", and 15 of 15 trips were scored in each of 3 runs.
   Side effect: `is_night` follows the wall clock.
2. **`spark/stream_processor.py`**: `build_spark_session()` declares
   `spark.jars.packages` for the Kafka connector, so the job also works when
   run with plain `python` rather than `spark-submit --packages`.
